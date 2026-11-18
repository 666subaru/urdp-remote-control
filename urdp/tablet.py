"""Use a tablet or phone as an extra screen of this computer.

KDE Plasma (Wayland) can add a virtual screen and stream it over VNC:
``krfb-virtualmonitor`` asks KWin for the screen, and windows can be moved
onto it like onto any monitor.  This module runs that tool and a small web
server, so the tablet needs nothing but its browser:

* ``/`` serves a page built on noVNC (``web/``), which draws the screen and
  sends touch and pen input back.
* ``/nonce`` hands out a one-time challenge; ``/hello`` takes the answer,
  proves the page knows the access code, and gives it a short-lived ticket
  and the VNC password; it also learns the tablet's screen size, so the
  virtual screen can match it.
* ``/ws`` is a WebSocket bridged to the VNC server on this machine,
  ``/input`` one that carries touch, pen and keys, and ``/audio`` one that
  brings the computer's sound (``tabletaudio``); each wants a ticket.

The access code travels in the URL fragment of the address the QR code holds,
which browsers never send to a server or put in a Referer; the page keeps it
in its own storage.  It never crosses the network: the page is plain HTTP, so
it answers ``HMAC(key, nonce)`` with ``key = PBKDF2(code)``, and the VNC
password comes back encrypted with that key.  Someone listening on the
network learns neither; guessing the code offline from what they saw costs a
PBKDF2 per guess.  Everything but the page files is behind the code.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import queue
import secrets
import shutil
import socket
import string
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PyQt6.QtCore import (QObject, QProcess, QProcessEnvironment, QTimer,
                          pyqtSignal)

from . import tabletaudio, tabletvideo, uinput
from .i18n import gettext as _
from .store import config_dir

WEB_ROOT = Path(__file__).resolve().parent / "web"
TOOL = "krfb-virtualmonitor"
OUTPUT = "URDP-Tablet"                 # KWin names the screen "Virtual-URDP-Tablet"
DEFAULT_PORT = 1701
DEFAULT_SIZE = (1920, 1200)
#: Wrong answers in all, after which sharing must be started again.
MAX_BAD_CODES = 200
#: Wrong answers from one address before it has to wait, and how long.
IP_TRIES, IP_WAIT = 5, 60.0
#: PBKDF2 rounds that turn the code into a key; the page does the same.
KEY_ROUNDS = 20000
NONCE_LIFE, TICKET_LIFE = 60.0, 60.0
MAX_CONNECTIONS = 64

#: Where the screen goes, as data so the labels can be translated on use.
POSITIONS = [("right", "Right of the screens"), ("left", "Left of the screens"),
             ("above", "Above the screens"), ("below", "Below the screens")]
#: "sharp" and "light" follow the tablet; the rest are fixed pixel sizes.
SIZES = [("sharp", "Match the tablet (sharp)"),
         ("light", "Match the tablet (lighter)"),
         ("2560x1600", "2560 × 1600"), ("1920x1200", "1920 × 1200"),
         ("1920x1080", "1920 × 1080"), ("1280x800", "1280 × 800")]
#: How touch and pen reach the computer.
INPUTS = [("mouse", "As a mouse (works in every program)"),
          ("tablet", "As touch and pen (pressure, gestures)")]
#: Where the computer's sound plays while sharing runs.
SOUNDS = [("computer", "On the computer"), ("tablet", "On the tablet")]
#: How the picture reaches the tablet.
PICTURES = [("fast", "Fast (asks once for permission to record the screen)"),
            ("vnc", "Compatible (slower)")]
#: The sizes that follow the tablet (their labels are translated).
FOLLOW = ("sharp", "light")

_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_FRAME = 16 * 1024 * 1024
_STATIC_TYPES = {".html": "text/html; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".mp4": "video/mp4"}


# --------------------------------------------------------------- settings
@dataclass
class Settings:
    port: int = DEFAULT_PORT
    position: str = "right"
    size: str = "sharp"
    #: "mouse" (pen and touch act as a mouse) or "tablet" (real devices)
    input: str = "mouse"
    #: "computer" or "tablet" (see SOUNDS)
    sound: str = "computer"
    #: "fast" (the screen through the ScreenCast portal) or "vnc"
    picture: str = "fast"
    #: The ScreenCast portal's restore token, so it asks only once.
    video_token: str = ""
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
    if settings.input not in dict(INPUTS):
        settings.input = "mouse"
    if settings.sound not in dict(SOUNDS):
        settings.sound = "computer"
    if settings.picture not in dict(PICTURES):
        settings.picture = "fast"
    if not 1024 <= settings.port <= 65535:
        settings.port = DEFAULT_PORT
    if not (200 <= settings.tablet_width <= 8192
            and 200 <= settings.tablet_height <= 8192
            and math.isfinite(settings.tablet_ratio)
            and 0.5 <= settings.tablet_ratio <= 5):
        settings.tablet_width = settings.tablet_height = 0
        settings.tablet_ratio = 0.0
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
    move over instead.  Returns the position of every screen, the layout
    starting at 0,0.
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
             for n, (x, y, _, _) in others.items()}
    moved[name] = place
    # Start the whole layout at 0,0: KWin restores the positions it saved
    # for this set of screens, which can leave a gap from an earlier layout.
    low_x = min(x for x, _ in moved.values())
    low_y = min(y for _, y in moved.values())
    return {n: (x - low_x, y - low_y) for n, (x, y) in moved.items()}


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


def derive_key(code: str, salt: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(),
                               KEY_ROUNDS, 32)


def proof(key: bytes, purpose: str, nonce: str) -> bytes:
    return hmac.new(key, f"{purpose}:{nonce}".encode(), "sha256").digest()


def seal(key: bytes, nonce: str, secret: str) -> str:
    """*secret* (at most 32 bytes) XORed with a pad only the code opens."""
    data = secret.encode()
    pad = proof(key, "vnc", nonce)
    return bytes(a ^ b for a, b in zip(data, pad)).hex()


def vnc_password() -> str:
    """VNC reads only 8 characters; make each of them count."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


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


def _read_page() -> dict[str, bytes]:
    page = {}
    for name in ("tablet.html", "tablet.js", "tablet.css", "auth.js",
                 "fastview.js"):
        try:
            page["/" + name] = (WEB_ROOT / name).read_bytes()
        except OSError:
            pass
    return page


#: The page's own files as this URDP started with them.  Served from here,
#: so an update on disk while URDP runs cannot pair a new page with this
#: server (the page would speak a protocol the server does not know).
_PAGE = _read_page()
_VERSION = hashlib.sha256(b"".join(_PAGE.values())).hexdigest()[:12]


def page_version() -> str:
    """Changes whenever the page's own files change, so an open page that
    reconnects to a newer URDP reloads itself instead of running old code."""
    return _VERSION


def page_strings() -> dict[str, str]:
    """The tablet page's text, in the application's language."""
    return {"connecting": _("Connecting…"),
            "connected": _("Connected"),
            "reconnecting": _("Reconnecting…"),
            "code": _("Enter the code shown in URDP on the computer."),
            "wrong": _("Wrong code"),
            "wait": _("Too many wrong codes; wait a minute and try again."),
            "sound": _("Touch the screen to turn on the sound."),
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
    """One WebSocket connection.  With *vnc*, frames and the VNC byte stream
    are bridged both ways; otherwise each message goes to *on_message*."""

    def __init__(self, client: socket.socket, reader,
                 vnc: socket.socket | None = None, on_message=None):
        self.client, self.reader, self.vnc = client, reader, vnc
        self.on_message = on_message
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
            if sock is None:
                continue
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
        if self.vnc is not None:
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
                    if self.vnc is not None:
                        self.vnc.sendall(payload)
                    elif self.on_message is not None:
                        try:
                            self.on_message(payload)
                        except Exception:              # noqa: BLE001
                            pass                # one bad message, not the link
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
    #: A connection that sends nothing is dropped; WebSockets clear this.
    timeout = 15
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
                         "connect-src 'self'; frame-ancestors 'none'")
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
        if path in ("/nonce", "/hello", "/ws", "/input") and not self._same_site():
            self._send(403, b"forbidden", "text/plain")
        elif path == "/nonce":
            self._json({"nonce": self.server.owner.new_nonce(),
                        "salt": self.server.owner.salt, "rounds": KEY_ROUNDS,
                        "strings": page_strings(), "version": page_version()})
        elif path == "/hello":
            self._hello(query)
        elif path == "/ws":
            self._websocket(query)
        elif path == "/input":
            self._input(query)
        elif path == "/audio":
            self._audio(query)
        elif path == "/video":
            self._video(query)
        else:
            self._static(path)

    def _same_site(self) -> bool:
        """Turn away other web sites: a page elsewhere cannot answer the
        challenge, but it could use up the wrong-code allowance (browsers
        send Sec-Fetch-Site only to localhost and HTTPS), or open a
        WebSocket here (browsers always send its Origin)."""
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        if origin is not None:
            host = urlsplit(origin).netloc
            return bool(host) and host == self.headers.get("Host", "")
        return True

    def _static(self, path: str) -> None:
        if path in _PAGE:
            self._send(200, _PAGE[path], _STATIC_TYPES[Path(path).suffix])
            return
        root = WEB_ROOT.resolve()
        try:
            target = (root / path.lstrip("/")).resolve()
        except (OSError, ValueError):          # a NUL in the path
            target = root
        kind = _STATIC_TYPES.get(target.suffix)
        if (kind is None or not target.is_relative_to(root)
                or not target.is_file()):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, target.read_bytes(), kind,
                   cache="/novnc/" in path)

    def _hello(self, query: dict) -> None:
        owner = self.server.owner
        answer: dict = {"strings": page_strings(), "version": page_version()}
        nonce = (query.get("n") or [""])[0]
        given = (query.get("p") or [""])[0]
        result = owner.check_proof(nonce, given, self.client_address[0])
        if result != "ok":
            answer.update(ok=False, reason=result)
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
        answer.update(ok=True, restart=restart, input=owner.has_input,
                      audio=owner.has_audio, video=owner.has_video,
                      ticket=owner.new_ticket(self.client_address[0]),
                      password=seal(owner.key(), nonce, owner.vnc_password))
        self._json(answer)

    def _may_upgrade(self, query: dict, use: str) -> bool:
        if (self.headers.get("Upgrade", "").lower() != "websocket"
                or not self.headers.get("Sec-WebSocket-Key")):
            self._send(400, b"websocket expected", "text/plain")
            return False
        ticket = (query.get("t") or [""])[0]
        if not self.server.owner.use_ticket(ticket, use,
                                            self.client_address[0]):
            self._send(403, b"forbidden", "text/plain")
            return False
        return True

    def _upgrade(self) -> None:
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

    def _input(self, query: dict) -> None:
        owner = self.server.owner
        if not self._may_upgrade(query, "input"):
            return
        if not owner.has_input:
            self._send(503, b"no input devices", "text/plain")
            return
        self._upgrade()
        bridge = _Bridge(self.connection, self.rfile,
                         on_message=owner.input_message)
        owner.input_opened(bridge)
        try:
            bridge.run()
        finally:
            owner.input_closed(bridge)

    def _video(self, query: dict) -> None:
        """The screen as JPEGs, one binary message each, newest only: a
        tablet that cannot keep up gets fewer frames, not older ones."""
        owner = self.server.owner
        if not self._may_upgrade(query, "video"):
            return
        if not owner.has_video:
            self._send(503, b"no fast picture", "text/plain")
            return
        self._upgrade()
        bridge = _Bridge(self.connection, self.rfile)
        frames = owner.video_opened(bridge)
        owner.bridge_opened(bridge, self.client_address[0])
        threading.Thread(target=bridge.run, daemon=True).start()
        try:
            while not bridge.closed.is_set():
                frame = frames.take(0.5)
                if frame:
                    bridge.send(0x2, frame)
        except OSError:
            pass
        finally:
            bridge.close()
            owner.video_closed(bridge)
            owner.bridge_closed(bridge)

    def _audio(self, query: dict) -> None:
        """The computer's sound, one binary message per 10 ms piece.  The
        page sends nothing but close and ping, which the bridge reads in a
        thread of its own while this one sends."""
        owner = self.server.owner
        if not self._may_upgrade(query, "audio"):
            return
        if not owner.has_audio:
            self._send(503, b"no sound", "text/plain")
            return
        self._upgrade()
        bridge = _Bridge(self.connection, self.rfile)
        pieces = owner.audio_opened(bridge)
        threading.Thread(target=bridge.run, daemon=True).start()
        try:
            while not bridge.closed.is_set():
                try:
                    piece = pieces.get(timeout=0.5)
                except queue.Empty:
                    continue
                bridge.send(0x2, piece)
        except OSError:
            pass
        finally:
            bridge.close()
            owner.audio_closed(bridge)

    def _websocket(self, query: dict) -> None:
        owner = self.server.owner
        if not self._may_upgrade(query, "ws"):
            return
        try:
            vnc = socket.create_connection(("127.0.0.1", owner.vnc_port),
                                           timeout=3)
        except OSError:
            self._send(503, b"screen not ready", "text/plain")
            return
        vnc.settimeout(None)
        vnc.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._upgrade()
        bridge = _Bridge(self.connection, self.rfile, vnc)
        owner.bridge_opened(bridge, self.client_address[0])
        try:
            bridge.run()
        finally:
            vnc.close()
            owner.bridge_closed(bridge)


class _Latest:
    """The newest frame for one listener; older ones are dropped."""

    def __init__(self, first: bytes = b"") -> None:
        self._frame = first
        self._ready = threading.Condition()

    def put(self, frame: bytes) -> None:
        with self._ready:
            self._frame = frame
            self._ready.notify()

    def take(self, timeout: float) -> bytes:
        with self._ready:
            if not self._frame:
                self._ready.wait(timeout)
            frame, self._frame = self._frame, b""
            return frame


class _WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, owner: "TabletScreen") -> None:
        self.owner = owner
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(("0.0.0.0", port), _Handler)

    def process_request(self, request, client_address) -> None:
        """At most MAX_CONNECTIONS at once, so idle connections cannot use
        up the threads and files the tablet needs."""
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

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
    #: something the user should know about the picture ("" clears it)
    notice = pyqtSignal(str)

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
        self._bad_from: dict[str, tuple[int, float]] = {}   # ip -> count, until
        self._nonces: dict[str, float] = {}                 # nonce -> expiry
        self._tickets: dict[str, tuple[str, float, set]] = {}
        self.salt = secrets.token_hex(8)
        self._key: tuple[str, str, bytes] | None = None
        self._restarting = False
        self._input: uinput.TabletInput | None = None
        self._input_bridge: _Bridge | None = None
        self._input_lock = threading.Lock()
        self._speaker: tabletaudio.Speaker | None = None
        self._audio: tuple[_Bridge, queue.Queue] | None = None
        self._capture: tabletvideo.Capture | None = None
        self._video: tuple[_Bridge, _Latest] | None = None
        self._video_ready = False
        self._last_frame = b""
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
        self.vnc_password = vnc_password()
        self.vnc_port = free_local_port()
        with self._lock:
            self._bad_codes = 0
            self._bad_from.clear()
            self._nonces.clear()
            self._tickets.clear()
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
        self._open_input()
        self._open_audio()
        self.notice.emit("")
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
        self._close_input()
        self._close_audio()
        self._stop_monitor()
        self._client_address = ""
        self._set_state("stopped")

    def new_code(self) -> None:
        """Shut out everyone who had the old code, also from the VNC server
        itself: it gets a new password, so the screen restarts."""
        self.settings.code = new_code()
        save_settings(self.settings)
        with self._lock:
            self.salt = secrets.token_hex(8)
            self._key = None
            self._nonces.clear()
            self._tickets.clear()
            bridges, self._bridges = self._bridges, []
            if self._input_bridge is not None:
                bridges.append(self._input_bridge)
        for bridge in bridges:
            bridge.close()
        if self.running:
            self.vnc_password = vnc_password()
            self._restart_monitor()

    # ------------------------------------------------------------- input
    @property
    def has_input(self) -> bool:
        return self._input is not None

    def _open_input(self) -> None:
        """Touch and pen become real devices when /dev/uinput is writable;
        without it the page falls back to sending input through VNC."""
        if self._input is not None or not uinput.available():
            return
        try:
            self._input = uinput.TabletInput()
        except OSError:
            self._input = None

    def _close_input(self) -> None:
        with self._lock:
            bridge, self._input_bridge = self._input_bridge, None
        if bridge is not None:
            bridge.close()
        with self._input_lock:
            device, self._input = self._input, None
        if device is not None:
            device.close()

    def input_opened(self, bridge: _Bridge) -> None:
        with self._lock:
            old, self._input_bridge = self._input_bridge, bridge
        if old is not None:
            old.close()

    def input_closed(self, bridge: _Bridge) -> None:
        with self._lock:
            if self._input_bridge is bridge:
                self._input_bridge = None
        with self._input_lock:
            if self._input is not None:
                self._input.release_all()   # nothing stays pressed

    def input_message(self, payload: bytes) -> None:
        """One input event from the page, a JSON array:

        ``["t", phase, id, x, y]``  touch, phase d / m / u
        ``["p", phase, x, y, pressure, tiltX, tiltY, barrel, rubber]``
                                     pen, phase h / d / m / u / o
        ``["w", dx, dy]``            wheel clicks
        ``["k", code, down]``        a key of a physical keyboard (evdev code)
        ``["x", text]``              text from the on-screen keyboard
        x and y run from 0 to 1 across the virtual screen.
        """
        def number(value) -> float:
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(value)
            return value

        try:
            event = json.loads(payload)
            kind = event[0]
            with self._input_lock:
                device = self._input
                if device is None:
                    return
                mouse = self.settings.input == "mouse"
                if kind == "t":
                    phase = {"d": "down", "m": "move", "u": "up"}[event[1]]
                    touch = device.touch_as_mouse if mouse else device.touch_event
                    touch(phase, int(event[2]), number(event[3]),
                          number(event[4]))
                elif kind == "p":
                    phase = {"h": "hover", "d": "down", "m": "move", "u": "up",
                             "o": "out"}[event[1]]
                    if mouse:
                        device.pen_as_mouse(phase, number(event[2]),
                                            number(event[3]), bool(event[7]))
                    else:
                        device.pen_event(phase, number(event[2]),
                                         number(event[3]), number(event[4]),
                                         number(event[5]), number(event[6]),
                                         bool(event[7]), bool(event[8]))
                elif kind == "k":
                    device.key(int(event[1]), bool(event[2]))
                elif kind == "x":
                    device.type_text(str(event[1]))
                elif kind == "w":
                    device.scroll(max(-50.0, min(50.0, number(event[1]))),
                                  max(-50.0, min(50.0, number(event[2]))))
        except (ValueError, TypeError, IndexError, KeyError, OSError,
                OverflowError, RecursionError):
            pass

    # ------------------------------------------------------------ picture
    @property
    def has_video(self) -> bool:
        return self._video_ready

    def _start_video(self) -> None:
        if self.settings.picture != "fast" or not tabletvideo.available():
            return
        if self._capture is None:
            self._capture = tabletvideo.Capture(self._video_out, self)
            self._capture.ready.connect(self._video_started)
            self._capture.failed.connect(self._video_failed)
            self._capture.token.connect(self._video_token)
        self._capture.start("Virtual-" + OUTPUT, self.settings.video_token)

    def _stop_video(self) -> None:
        self._video_ready = False
        self._last_frame = b""
        if self._capture is not None:
            self._capture.stop()
        with self._lock:
            video, self._video = self._video, None
        if video is not None:
            video[0].close()

    def _video_started(self) -> None:
        self._video_ready = True
        self.notice.emit("")
        # A page showing the VNC picture reconnects and gets this one.
        with self._lock:
            bridges, self._bridges = self._bridges, []
        for bridge in bridges:
            bridge.close()

    def _video_failed(self, reason: str) -> None:
        was_ready, self._video_ready = self._video_ready, False
        if not self.running:
            return
        if reason == "":
            self.notice.emit(_("Recording the screen was not allowed, so the "
                               "slower picture is used."))
        elif reason == "wrong screen":
            self.notice.emit(_("Another screen was chosen; share the screen "
                               "named URDP-Tablet for the fast picture. The "
                               "slower picture is used for now."))
        else:
            self.notice.emit(_("The fast picture could not start ({reason}); "
                               "the slower picture is used.")
                             .format(reason=reason))
        if was_ready:
            with self._lock:
                video, self._video = self._video, None
            if video is not None:
                video[0].close()

    def _video_token(self, token: str) -> None:
        if token != self.settings.video_token:
            self.settings.video_token = token
            save_settings(self.settings)

    def video_opened(self, bridge: _Bridge) -> _Latest:
        frames = _Latest(self._last_frame)
        with self._lock:
            old, self._video = self._video, (bridge, frames)
        if old is not None:
            old[0].close()
        return frames

    def video_closed(self, bridge: _Bridge) -> None:
        with self._lock:
            if self._video is not None and self._video[0] is bridge:
                self._video = None

    def _video_out(self, frame: bytes) -> None:
        """From the capture's reader thread."""
        self._last_frame = frame
        video = self._video
        if video is not None:
            video[1].put(frame)

    # ------------------------------------------------------------- sound
    @property
    def has_audio(self) -> bool:
        return self._speaker is not None

    def _open_audio(self) -> None:
        if self.settings.sound == "tablet" and self._speaker is None:
            speaker = tabletaudio.Speaker(self._audio_out)
            self._speaker = speaker if speaker.start() else None

    def set_sound(self, sound: str) -> None:
        """Move the sound while sharing runs.  An open page learns about
        sound only when it connects, so the screen reconnects (a blink)."""
        if sound == self.settings.sound:
            return
        self.settings.sound = sound
        save_settings(self.settings)
        if not self.running:
            return
        if sound == "tablet":
            self._open_audio()
            with self._lock:
                bridges, self._bridges = self._bridges, []
            for bridge in bridges:
                bridge.close()
        else:
            self._close_audio()

    def _close_audio(self) -> None:
        with self._lock:
            audio, self._audio = self._audio, None
        if audio is not None:
            audio[0].close()
        speaker, self._speaker = self._speaker, None
        if speaker is not None:
            speaker.stop()

    def audio_opened(self, bridge: _Bridge) -> queue.Queue:
        """The queue the new listener's pieces go into; one listener at a
        time, the newest."""
        pieces: queue.Queue = queue.Queue(maxsize=20)      # 200 ms at most
        with self._lock:
            old, self._audio = self._audio, (bridge, pieces)
        if old is not None:
            old[0].close()
        return pieces

    def audio_closed(self, bridge: _Bridge) -> None:
        with self._lock:
            if self._audio is not None and self._audio[0] is bridge:
                self._audio = None

    def _audio_out(self, piece: bytes) -> None:
        """From the recorder's thread: a piece of sound for the tablet; a
        tablet that cannot keep up loses pieces instead of falling behind."""
        audio = self._audio
        if audio is not None:
            try:
                audio[1].put_nowait(piece)
            except queue.Full:
                pass

    # ------------------------------------------- called from web threads
    def key(self) -> bytes:
        """The key the code and salt make, worked out once for both."""
        code, salt = self.settings.code, self.salt
        cached = self._key
        if cached is None or cached[:2] != (code, salt):
            cached = (code, salt, derive_key(code, salt))
            self._key = cached
        return cached[2]

    def new_nonce(self) -> str:
        nonce = secrets.token_hex(16)
        now = time.monotonic()
        with self._lock:
            for old in [n for n, t in self._nonces.items() if t < now]:
                del self._nonces[old]
            while len(self._nonces) >= 256:            # oldest first
                del self._nonces[next(iter(self._nonces))]
            self._nonces[nonce] = now + NONCE_LIFE
        return nonce

    def check_proof(self, nonce: str, given: str, address: str) -> str:
        """"ok", or why not: "code" (wrong or missing), "wait" (this address
        guessed too often) or "locked" (too many wrong answers in all)."""
        now = time.monotonic()
        with self._lock:
            if self._bad_codes >= MAX_BAD_CODES:
                return "locked"
            count, until = self._bad_from.get(address, (0, 0.0))
            if until > now:
                return "wait"
            # Each challenge answers once, right or wrong.
            fresh = self._nonces.pop(nonce, 0.0) > now
        if not given or not fresh:
            return "code"                 # no answer is not a guess
        try:
            answer = bytes.fromhex(given)
        except ValueError:
            answer = b""
        if hmac.compare_digest(answer, proof(self.key(), "hello", nonce)):
            with self._lock:
                self._bad_from.pop(address, None)
            return "ok"
        with self._lock:
            count += 1
            if len(self._bad_from) >= 1024:
                self._bad_from.clear()
            self._bad_from[address] = (
                count, now + IP_WAIT if count >= IP_TRIES else 0.0)
            self._bad_codes += 1
            if self._bad_codes >= MAX_BAD_CODES:
                self._locked.emit()
        return "code"

    def new_ticket(self, address: str) -> str:
        """Lets *address* open the screen and the input channel once each,
        within TICKET_LIFE seconds."""
        ticket = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            for old in [t for t, v in self._tickets.items() if v[1] < now]:
                del self._tickets[old]
            while len(self._tickets) >= 64:
                del self._tickets[next(iter(self._tickets))]
            self._tickets[ticket] = (address, now + TICKET_LIFE, set())
        return ticket

    def use_ticket(self, ticket: str, use: str, address: str) -> bool:
        with self._lock:
            entry = self._tickets.get(ticket)
            if (entry is None or entry[0] != address
                    or entry[1] < time.monotonic() or use in entry[2]):
                return False
            entry[2].add(use)
            return True

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
        self._stop_video()
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
        # Where the screen lies on the desktop, for the absolute mouse.
        sizes = {o["name"]: _logical(o) for o in outputs
                 if o.get("enabled", True) and o.get("connected", True)}
        sizes[name] = (logical_w, logical_h)
        right = max(positions.get(n, (0, 0))[0] + w for n, (w, _) in sizes.items())
        bottom = max(positions.get(n, (0, 0))[1] + h for n, (_, h) in sizes.items())
        vx, vy = positions[name]
        with self._input_lock:
            if self._input is not None:
                self._input.map_to(name)
                self._input.set_area(vx, vy, logical_w, logical_h, right, bottom)
        with self._lock:
            self._restarting = False
        self._start_video()
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
