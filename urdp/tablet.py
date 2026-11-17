"""Use a tablet or phone as an extra screen of this computer.

KDE Plasma (Wayland) can add a virtual screen and stream it over VNC:
``krfb-virtualmonitor`` asks KWin for the screen, and windows can be moved
onto it like onto any monitor.  This module runs that tool and a small web
server, so the tablet needs nothing but its browser:

* ``/`` serves a page built on noVNC (``web/``), which draws the screen and
  sends touch and pen input back.
* ``/hello`` checks the access code, tells the page the VNC password, and
  learns the tablet's screen size, so the virtual screen can match it.
* ``/ws`` is a WebSocket bridged to the VNC server on this machine.

The access code travels in the URL fragment of the address the QR code holds,
which browsers never send to a server or put in a Referer; the page keeps it
in its own storage.  Everything but the noVNC files is behind that code.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PyQt6.QtCore import (QObject, QProcess, QProcessEnvironment, QTimer,
                          pyqtSignal)

from .i18n import gettext as _
from .store import config_dir

WEB_ROOT = Path(__file__).resolve().parent / "web"
TOOL = "krfb-virtualmonitor"
OUTPUT = "URDP-Tablet"                 # KWin names the screen "Virtual-URDP-Tablet"
DEFAULT_PORT = 1701
DEFAULT_SIZE = (1920, 1200)
MAX_BAD_CODES = 20

#: Where the screen goes, as data so the labels can be translated on use.
POSITIONS = [("right", "Right of the screens"), ("left", "Left of the screens"),
             ("above", "Above the screens"), ("below", "Below the screens")]
#: "sharp" and "light" follow the tablet; the rest are fixed pixel sizes.
SIZES = [("sharp", "Match the tablet (sharp)"),
         ("light", "Match the tablet (lighter)"),
         ("2560x1600", "2560 × 1600"), ("1920x1200", "1920 × 1200"),
         ("1920x1080", "1920 × 1080"), ("1280x800", "1280 × 800")]
#: The sizes that follow the tablet (their labels are translated).
FOLLOW = ("sharp", "light")

_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_FRAME = 16 * 1024 * 1024
_STATIC_TYPES = {".html": "text/html; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}


# --------------------------------------------------------------- settings
@dataclass
class Settings:
    port: int = DEFAULT_PORT
    position: str = "right"
    size: str = "sharp"
    code: str = ""
    #: The last tablet seen, in CSS pixels, so the next start can match it.
    tablet_width: int = 0
    tablet_height: int = 0
    tablet_ratio: float = 0.0


def settings_path() -> Path:
    return config_dir() / "tablet.json"


def new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))


def load_settings() -> Settings:
    settings = Settings()
    try:
        data = json.loads(settings_path().read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        for item in fields(Settings):
            value = data.get(item.name)
            if isinstance(value, type(getattr(settings, item.name))):
                setattr(settings, item.name, value)
    if settings.position not in dict(POSITIONS):
        settings.position = "right"
    if settings.size not in dict(SIZES):
        settings.size = "sharp"
    if not 1024 <= settings.port <= 65535:
        settings.port = DEFAULT_PORT
    if len(settings.code) < 8 or set(settings.code) - set(_CODE_ALPHABET):
        settings.code = new_code()
    return settings


def save_settings(settings: Settings) -> None:
    """Written owner-only: the file holds the access code."""
    path = settings_path()
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tablet-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(asdict(settings), fh, indent=2)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)


# --------------------------------------------------------------- geometry
def geometry(size: str, width: float, height: float,
             ratio: float) -> tuple[int, int, float]:
    """Virtual screen ``(pixel width, pixel height, scale)``.

    *width*/*height*/*ratio* describe the tablet (CSS pixels and
    devicePixelRatio); zero means it has not been seen yet.
    """
    if size not in FOLLOW:
        w, _, h = size.partition("x")
        return int(w), int(h), 1.0
    if width <= 0 or height <= 0:
        return DEFAULT_SIZE[0], DEFAULT_SIZE[1], 1.0
    scale = 1.0
    if size == "sharp":
        scale = min(max(round(ratio * 4) / 4, 1.0), 3.0)
    return (int(round(width * scale)) & ~1, int(round(height * scale)) & ~1,
            scale)


def _logical(output: dict) -> tuple[int, int]:
    mode = next((m for m in output.get("modes", [])
                 if m.get("id") == output.get("currentModeId")), None)
    size = (mode or output).get("size", {})
    w, h = size.get("width", 0), size.get("height", 0)
    if output.get("rotation") in (2, 8):
        w, h = h, w
    scale = output.get("scale") or 1
    return int(round(w / scale)), int(round(h / scale))


def layout(outputs: list[dict], name: str, width: int, height: int,
           side: str) -> dict[str, tuple[int, int]]:
    """New positions that put *name* (logical *width* x *height*) on *side*.

    KWin refuses negative coordinates, so for left/above the other screens
    move over instead.  Returns only the positions to set.
    """
    others = {o["name"]: (o["pos"]["x"], o["pos"]["y"], *_logical(o))
              for o in outputs
              if o.get("name") != name and o.get("enabled", True)
              and o.get("connected", True)}
    if not others:
        return {name: (0, 0)}
    left = min(x for x, _, _, _ in others.values())
    top = min(y for _, y, _, _ in others.values())
    right = max(x + w for x, _, w, _ in others.values())
    bottom = max(y + h for _, y, _, h in others.values())
    shift = (0, 0)
    if side == "left":
        place, shift = (left, top), (width, 0)
    elif side == "above":
        place, shift = (left, top), (0, height)
    elif side == "below":
        place = (left, bottom)
    else:
        place = (right, top)
    moved = {n: (x + shift[0], y + shift[1])
             for n, (x, y, _, _) in others.items() if shift != (0, 0)}
    moved[name] = place
    return moved


def _wayland_env() -> dict[str, str]:
    """The environment for KDE's own tools: they are Qt programs and must
    reach KWin, whatever Qt platform URDP itself runs on (xcb, offscreen)."""
    return {**os.environ, "QT_QPA_PLATFORM": "wayland"}


def _kscreen() -> list[dict]:
    try:
        result = subprocess.run(["kscreen-doctor", "-j"], capture_output=True,
                                text=True, timeout=5, env=_wayland_env())
        return json.loads(result.stdout).get("outputs", [])
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return []


def _kscreen_apply(arguments: list[str]) -> bool:
    if not arguments:
        return True
    try:
        result = subprocess.run(["kscreen-doctor", *arguments],
                                capture_output=True, text=True, timeout=10,
                                env=_wayland_env())
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------- network
@dataclass(frozen=True)
class Address:
    interface: str
    address: str
    prefix: int


_VIRTUAL_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "vnet", "podman",
                     "cni", "flannel", "kube")


def addresses() -> list[Address]:
    """IPv4 addresses a tablet could reach, the default route's first."""
    try:
        raw = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"],
                             capture_output=True, text=True, timeout=5).stdout
        links = json.loads(raw)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    found = []
    for link in links:
        name = link.get("ifname", "")
        if name.startswith(_VIRTUAL_PREFIXES):
            continue
        for info in link.get("addr_info", []):
            if info.get("family") == "inet" and info.get("scope") == "global":
                found.append(Address(name, info["local"], info["prefixlen"]))
    default = _default_interface()
    found.sort(key=lambda a: (a.interface != default,
                              not ipaddress.ip_address(a.address).is_private))
    return found


def _default_interface() -> str:
    try:
        raw = subprocess.run(["ip", "-j", "-4", "route", "show", "default",
                              "table", "main"],
                             capture_output=True, text=True, timeout=5).stdout
        routes = json.loads(raw)
        return routes[0].get("dev", "") if routes else ""
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return ""


def misrouted(address: Address, port: int) -> str:
    """The interface replies to *address*'s own network leave through, when
    it is not that network's interface (a VPN that took over the LAN route);
    otherwise "".  The tablet's requests then arrive but no answer reaches it.
    """
    network = ipaddress.ip_network(f"{address.address}/{address.prefix}",
                                   strict=False)
    if network.num_addresses < 4:
        return ""
    probe = str(network.network_address + 1)
    if probe == address.address:
        probe = str(network.network_address + 2)
    try:
        raw = subprocess.run(
            ["ip", "-j", "-4", "route", "get", probe, "from", address.address,
             "ipproto", "tcp", "sport", str(port)],
            capture_output=True, text=True, timeout=5).stdout
        routes = json.loads(raw)
        dev = routes[0].get("dev", "") if routes else ""
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return ""
    return dev if dev and dev != address.interface else ""


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def desktop_ok() -> bool:
    """A KDE Plasma Wayland session, the only one with virtual screens."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    wayland = (os.environ.get("XDG_SESSION_TYPE") == "wayland"
               or bool(os.environ.get("WAYLAND_DISPLAY")))
    return "KDE" in desktop and wayland


def supported() -> str:
    """Why this desktop cannot do it, or "" when it can."""
    if not desktop_ok():
        return _("This feature needs a KDE Plasma Wayland session.")
    if not shutil.which(TOOL):
        return _("krfb is not installed. Install the krfb package to use "
                 "this feature.")
    return ""


def page_strings() -> dict[str, str]:
    """The tablet page's text, in the application's language."""
    return {"connecting": _("Connecting…"),
            "connected": _("Connected"),
            "reconnecting": _("Reconnecting…"),
            "code": _("Enter the code shown in URDP on the computer."),
            "wrong": _("Wrong code"),
            "connect": _("Connect"),
            "fullscreen": _("Full Screen")}


# -------------------------------------------------------------- websocket
def _accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _unmask(data: bytes, key: bytes) -> bytes:
    n = len(data)
    if not n:
        return data
    pad = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "little")
            ^ int.from_bytes(pad, "little")).to_bytes(n, "little")


def _frame(opcode: int, payload: bytes) -> bytes:
    n = len(payload)
    if n < 126:
        header = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return header + payload


class _Bridge:
    """One tablet connection: WebSocket frames <-> the VNC byte stream."""

    def __init__(self, client: socket.socket, reader, vnc: socket.socket):
        self.client, self.reader, self.vnc = client, reader, vnc
        self.lock = threading.Lock()
        self.closed = threading.Event()

    def send(self, opcode: int, payload: bytes) -> None:
        with self.lock:
            self.client.sendall(_frame(opcode, payload))

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        for sock in (self.client, self.vnc):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _read(self, n: int) -> bytes:
        data = self.reader.read(n)
        if data is None or len(data) < n:
            raise EOFError
        return data

    def _vnc_to_client(self) -> None:
        try:
            while True:
                data = self.vnc.recv(65536)
                if not data:
                    break
                self.send(0x2, data)
        except OSError:
            pass
        self.close()

    def run(self) -> None:
        threading.Thread(target=self._vnc_to_client, daemon=True).start()
        try:
            while not self.closed.is_set():
                first, second = self._read(2)
                opcode, length = first & 0x0F, second & 0x7F
                if not second & 0x80:
                    break                       # clients must mask
                if length == 126:
                    length = struct.unpack("!H", self._read(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._read(8))[0]
                if length > _MAX_FRAME:
                    break
                key = self._read(4)
                payload = _unmask(self._read(length), key)
                if opcode in (0x0, 0x1, 0x2):
                    self.vnc.sendall(payload)
                elif opcode == 0x8:
                    try:
                        self.send(0x8, payload[:2])
                    except OSError:
                        pass
                    break
                elif opcode == 0x9:
                    self.send(0xA, payload)
        except (OSError, EOFError, ValueError):
            pass
        self.close()


# ------------------------------------------------------------- web server
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "_WebServer"

    def log_message(self, *_args) -> None:     # quiet; the UI shows status
        pass

    def _send(self, status: int, body: bytes, kind: str,
              cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache
                         else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; "
                         "connect-src 'self' ws: wss:; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: dict) -> None:
        self._send(200, json.dumps(data).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_HEAD(self) -> None:                 # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:                  # noqa: N802
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        path = url.path
        if path == "/":
            path = "/tablet.html"
        if path == "/hello":
            self._hello(query)
        elif path == "/ws":
            self._websocket(query)
        else:
            self._static(path)

    def _static(self, path: str) -> None:
        root = WEB_ROOT.resolve()
        target = (root / path.lstrip("/")).resolve()
        kind = _STATIC_TYPES.get(target.suffix)
        if (kind is None or not target.is_relative_to(root)
                or not target.is_file()):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, target.read_bytes(), kind,
                   cache="/novnc/" in path)

    def _code_ok(self, query: dict) -> bool | None:
        """True / False, or None when too many wrong codes locked access."""
        given = (query.get("k") or [""])[0]
        return self.server.owner.check_code(given)

    def _hello(self, query: dict) -> None:
        owner = self.server.owner
        answer: dict = {"strings": page_strings()}
        ok = self._code_ok(query)
        if not ok:
            answer.update(ok=False, reason="locked" if ok is None else "code")
            self._json(answer)
            return

        def number(name: str) -> float:
            try:
                return float((query.get(name) or ["0"])[0])
            except ValueError:
                return 0.0

        w, h, ratio = number("w"), number("h"), number("dpr")
        if not (200 <= w <= 8192 and 200 <= h <= 8192 and 0.5 <= ratio <= 5):
            w = h = ratio = 0.0
        restart = owner.tablet_seen(w, h, ratio)
        answer.update(ok=True, password=owner.vnc_password, restart=restart)
        self._json(answer)

    def _websocket(self, query: dict) -> None:
        owner = self.server.owner
        if (self.headers.get("Upgrade", "").lower() != "websocket"
                or not self.headers.get("Sec-WebSocket-Key")):
            self._send(400, b"websocket expected", "text/plain")
            return
        if not self._code_ok(query):
            self._send(403, b"forbidden", "text/plain")
            return
        try:
            vnc = socket.create_connection(("127.0.0.1", owner.vnc_port),
                                           timeout=3)
        except OSError:
            self._send(503, b"screen not ready", "text/plain")
            return
        vnc.settimeout(None)
        vnc.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept",
                         _accept_key(self.headers["Sec-WebSocket-Key"]))
        offered = [p.strip() for p in
                   self.headers.get("Sec-WebSocket-Protocol", "").split(",")]
        if "binary" in offered:
            self.send_header("Sec-WebSocket-Protocol", "binary")
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        self.connection.settimeout(None)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        bridge = _Bridge(self.connection, self.rfile, vnc)
        owner.bridge_opened(bridge, self.client_address[0])
        try:
            bridge.run()
        finally:
            vnc.close()
            owner.bridge_closed(bridge)


class _WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, owner: "TabletScreen") -> None:
        self.owner = owner
        super().__init__(("0.0.0.0", port), _Handler)

    def handle_error(self, request, client_address) -> None:
        """A tablet that drops off Wi-Fi is normal, not worth a traceback."""
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


# ------------------------------------------------------------ controller
class TabletScreen(QObject):
    """Runs the virtual screen and the web server; lives in the GUI thread.

    Signals carry plain values; the web server's threads emit them and Qt
    queues them to the GUI thread.
    """

    #: "stopped" / "starting" / "waiting" / "connected" / "error"
    state_changed = pyqtSignal(str, str)
    _resize = pyqtSignal()
    _client = pyqtSignal(str)
    _locked = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = load_settings()
        self.vnc_password = ""
        self.vnc_port = 0
        self.state = "stopped"
        self.detail = ""
        self._server: _WebServer | None = None
        self._process: QProcess | None = None
        self._geometry: tuple[int, int, float] | None = None
        self._lock = threading.Lock()
        self._bridges: list[_Bridge] = []
        self._client_address = ""
        self._bad_codes = 0
        self._restarting = False
        self._layout_tries = 0
        self._layout_timer = QTimer(self)
        self._layout_timer.setInterval(250)
        self._layout_timer.timeout.connect(self._try_layout)
        self._resize.connect(self._restart_monitor)
        self._client.connect(self._on_client)
        self._locked.connect(lambda: self._set_state("error", _(
            "Too many wrong codes; access is blocked until sharing is "
            "started again.")))

    # ------------------------------------------------------------ public
    @property
    def running(self) -> bool:
        return self._server is not None

    def url(self, address: str) -> str:
        return f"http://{address}:{self.settings.port}/#k={self.settings.code}"

    def start(self) -> None:
        if self.running:
            return
        problem = supported()
        if problem:
            self._set_state("error", problem)
            return
        save_settings(self.settings)
        self.vnc_password = secrets.token_hex(4)    # VNC uses 8 characters
        self.vnc_port = free_local_port()
        self._bad_codes = 0
        try:
            self._server = _WebServer(self.settings.port, self)
        except OSError as exc:
            self._server = None
            self._set_state("error", _("Could not open port {port}: {error}")
                            .format(port=self.settings.port,
                                    error=exc.strerror or exc))
            return
        threading.Thread(target=self._server.serve_forever,
                         daemon=True).start()
        self._set_state("starting")
        s = self.settings
        self._start_monitor(geometry(s.size, s.tablet_width, s.tablet_height,
                                     s.tablet_ratio))

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()
            server.server_close()
        with self._lock:
            bridges, self._bridges = self._bridges, []
        for bridge in bridges:
            bridge.close()
        self._stop_monitor()
        self._client_address = ""
        self._set_state("stopped")

    def new_code(self) -> None:
        self.settings.code = new_code()
        save_settings(self.settings)
        with self._lock:
            bridges, self._bridges = self._bridges, []
        for bridge in bridges:
            bridge.close()

    # ------------------------------------------- called from web threads
    def check_code(self, given: str) -> bool | None:
        with self._lock:
            if self._bad_codes >= MAX_BAD_CODES:
                return None
            if hmac.compare_digest(given.encode(), self.settings.code.encode()):
                return True
            self._bad_codes += 1
            if self._bad_codes >= MAX_BAD_CODES:
                self._locked.emit()
            return False

    def tablet_seen(self, width: float, height: float, ratio: float) -> bool:
        """Remember the tablet; True when the screen restarts to match it."""
        if not width:
            return False
        s = self.settings
        with self._lock:
            s.tablet_width, s.tablet_height = int(width), int(height)
            s.tablet_ratio = round(ratio, 3)
            wanted = geometry(s.size, width, height, ratio)
            if wanted == self._geometry or s.size not in FOLLOW:
                return self._restarting
            self._restarting = True
        self._resize.emit()
        return True

    def bridge_opened(self, bridge: _Bridge, address: str) -> None:
        with self._lock:
            old, self._bridges = self._bridges, [bridge]
        for item in old:                # one tablet at a time: newest wins
            item.close()
        self._client.emit(address)

    def bridge_closed(self, bridge: _Bridge) -> None:
        with self._lock:
            if bridge in self._bridges:
                self._bridges.remove(bridge)
            empty = not self._bridges
        if empty:
            self._client.emit("")

    # ----------------------------------------------------------- monitor
    def _start_monitor(self, wanted: tuple[int, int, float]) -> None:
        width, height, _scale = wanted
        self._geometry = wanted
        process = QProcess(self)
        process.setProgram(shutil.which(TOOL) or TOOL)
        process.setArguments(["--name", OUTPUT,
                              "--resolution", f"{width}x{height}",
                              "--password", self.vnc_password,
                              "--port", str(self.vnc_port)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        # The virtual screen is a Wayland feature; whatever platform URDP
        # itself runs on (xcb, offscreen), the tool must talk to KWin.
        env = QProcessEnvironment.systemEnvironment()
        env.insert("QT_QPA_PLATFORM", "wayland")
        process.setProcessEnvironment(env)
        process.finished.connect(lambda *_a, p=process: self._monitor_exited(p))
        process.errorOccurred.connect(
            lambda _e, p=process: self._monitor_exited(p))
        self._process = process
        process.start()
        self._layout_tries = 0
        self._layout_timer.start()

    def _stop_monitor(self) -> None:
        self._layout_timer.stop()
        process, self._process = self._process, None
        if process is not None:
            process.blockSignals(True)
            process.terminate()
            if not process.waitForFinished(3000):
                process.kill()
                process.waitForFinished(1000)
            process.deleteLater()
        # When the screen goes, KWin closes the gap the left/above layouts
        # opened, so the other screens need no restoring.
        self._geometry = None

    def _restart_monitor(self) -> None:
        if not self.running:
            return
        with self._lock:
            bridges, self._bridges = self._bridges, []
        for bridge in bridges:
            bridge.close()
        self._stop_monitor()
        s = self.settings
        save_settings(s)
        self._set_state("starting")
        QTimer.singleShot(300, lambda: self.running and self._start_monitor(
            geometry(s.size, s.tablet_width, s.tablet_height, s.tablet_ratio)))

    def _monitor_exited(self, process: QProcess) -> None:
        if process is not self._process:
            return
        output = bytes(process.readAll()).decode("utf-8", "replace").strip()
        self._process = None
        self.stop()
        message = _("The virtual screen stopped.")
        if output:
            message += "\n" + output.splitlines()[-1][:200]
        self._set_state("error", message)

    def _try_layout(self) -> None:
        self._layout_tries += 1
        outputs = _kscreen()
        name = "Virtual-" + OUTPUT
        if not any(o.get("name") == name for o in outputs):
            if self._layout_tries > 40:
                self._layout_timer.stop()
                self.stop()
                self._set_state("error", _("The virtual screen did not start."))
            return
        self._layout_timer.stop()
        width, height, scale = self._geometry or (*DEFAULT_SIZE, 1.0)
        logical_w, logical_h = int(round(width / scale)), int(round(height / scale))
        positions = layout(outputs, name, logical_w, logical_h,
                           self.settings.position)
        # Always set the scale: KWin remembers it for the screen's name.
        arguments = [f"output.{name}.scale.{scale:g}"]
        arguments += [f"output.{n}.position.{x},{y}"
                      for n, (x, y) in positions.items()]
        _kscreen_apply(arguments)
        with self._lock:
            self._restarting = False
        self._set_state("connected" if self._client_address else "waiting",
                        self._client_address)

    # ------------------------------------------------------------- state
    def _on_client(self, address: str) -> None:
        self._client_address = address
        if self.running and self.state in ("waiting", "connected"):
            self._set_state("connected" if address else "waiting", address)

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        self.state_changed.emit(state, detail)
