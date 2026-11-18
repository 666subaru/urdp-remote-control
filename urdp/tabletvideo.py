"""A faster picture for the tablet screen.

``krfb-virtualmonitor`` creates the virtual screen, but sends it at most ten
times a second and a tenth of a second late.  KWin itself can deliver the
screen at its refresh rate, so this module asks for it through the
ScreenCast portal (the desktop asks the user once; the answer is remembered
with a restore token), and ``gst-launch-1.0`` turns each frame into a JPEG,
on the GPU when VA-API can, else on the CPU.  The tablet page draws the
JPEGs itself.  Without GStreamer, or when the user says no, the page keeps
using VNC.

Only the system's GStreamer tools are used, not its Python bindings, so this
works from the AppImage too.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import threading
from collections.abc import Callable

from PyQt6.QtCore import QMetaType, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusArgument, QDBusConnection, QDBusInterface,
                          QDBusMessage, QDBusObjectPath)

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
BOUNDARY = b"urdpframe"
QUALITY = 80
_SOURCE_MONITOR, _CURSOR_EMBEDDED, _PERSIST_ALWAYS = 1, 2, 2

_checked: dict[str, bool] = {}


def _has_element(name: str) -> bool:
    if name not in _checked:
        try:
            _checked[name] = subprocess.run(
                ["gst-inspect-1.0", "--exists", name], timeout=10,
                capture_output=True).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _checked[name] = False
    return _checked[name]


def available() -> bool:
    return (bool(shutil.which("gst-launch-1.0"))
            and all(_has_element(e) for e in
                    ("pipewiresrc", "jpegenc", "videoconvert", "multipartmux")))


def pipelines(fd: int, node: int, quality: int = QUALITY) -> list[list[str]]:
    """gst-launch arguments to try in turn: the GPU one first."""
    tail = ["!", "multipartmux", f"boundary={BOUNDARY.decode()}",
            "!", "fdsink", "fd=1", "sync=false"]
    source = ["pipewiresrc", f"fd={fd}", f"path={node}"]
    found = []
    if _has_element("vapostproc") and _has_element("vajpegenc"):
        found.append(source + [
            "!", "vapostproc", "!", "video/x-raw(memory:VAMemory),format=NV12",
            "!", "vajpegenc", f"quality={quality}"] + tail)
    found.append(source + [
        "always-copy=true", "!", "videoconvert",
        "!", "video/x-raw,format=I420", "!", "jpegenc", f"quality={quality}"]
        + tail)
    return found


class FrameReader:
    """Splits gst-launch's multipart output into JPEGs."""

    def __init__(self) -> None:
        self.buffer = b""

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer += data
        frames = []
        while True:
            head = self.buffer.find(b"Content-Length: ")
            if head < 0:
                break
            end = self.buffer.find(b"\r\n\r\n", head)
            if end < 0:
                break
            try:
                size = int(self.buffer[head + 16:end].split(b"\r\n")[0])
            except ValueError:
                self.buffer = self.buffer[end + 4:]
                continue
            start = end + 4
            if len(self.buffer) < start + size:
                break
            frames.append(self.buffer[start:start + size])
            self.buffer = self.buffer[start + size:]
        if len(self.buffer) > 64 * 1024 * 1024:       # garbage; start over
            self.buffer = b""
        return frames


def _u32(value: int) -> QDBusArgument:
    return QDBusArgument(value, QMetaType.Type.UInt.value)


class Capture(QObject):
    """One screen through the ScreenCast portal, as JPEGs.  Lives in the
    GUI thread; *on_frame* is called from a reader thread."""

    #: frames are flowing
    ready = pyqtSignal()
    #: it did not work; the reason, for the user ("" when they declined)
    failed = pyqtSignal(str)
    #: the portal's restore token, to be kept for the next time
    token = pyqtSignal(str)
    _ended = pyqtSignal(object, int)

    def __init__(self, on_frame: Callable[[bytes], None],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.on_frame = on_frame
        self.bus = QDBusConnection.sessionBus()
        self.portal = QDBusInterface(PORTAL, PORTAL_PATH,
                                     "org.freedesktop.portal.ScreenCast",
                                     self.bus)
        self.output = ""
        self.session = ""
        self.node = 0
        self._waiting: dict[str, Callable[[int, dict], None]] = {}
        self._process: subprocess.Popen | None = None
        self._tries: list[list[str]] = []
        self._got_frame = False
        self.running = False
        self._ended.connect(self._on_ended)

    # ----------------------------------------------------------- portal
    def start(self, output: str, restore_token: str) -> None:
        """Share *output* (a KWin output name)."""
        self.stop()
        self.output = output
        self.running = True
        self._restore = restore_token
        self._request("CreateSession", [], {
            "session_handle_token": "urdp" + secrets.token_hex(6)},
            self._created)

    def _request(self, method: str, arguments: list, options: dict,
                 then: Callable[[int, dict], None]) -> None:
        handle = "urdp" + secrets.token_hex(6)
        sender = self.bus.baseService()[1:].replace(".", "_")
        path = f"{PORTAL_PATH}/request/{sender}/{handle}"
        self._waiting[path] = then
        self.bus.connect(PORTAL, path, "org.freedesktop.portal.Request",
                         "Response", self._response)
        options["handle_token"] = handle
        reply = self.portal.call(method, *arguments, options)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            self._waiting.pop(path, None)
            self._fail(reply.errorMessage())

    @pyqtSlot(QDBusMessage)
    def _response(self, message: QDBusMessage) -> None:
        then = self._waiting.pop(message.path(), None)
        self.bus.disconnect(PORTAL, message.path(),
                            "org.freedesktop.portal.Request", "Response",
                            self._response)
        if then is None or not self.running:
            return
        arguments = message.arguments()
        code = arguments[0] if arguments else 2
        results = arguments[1] if len(arguments) > 1 else {}
        then(int(code), results if isinstance(results, dict) else {})

    def _created(self, code: int, results: dict) -> None:
        if code != 0:
            self._fail("" if code == 1 else "CreateSession")
            return
        self.session = str(results.get("session_handle", ""))
        options = {"types": _u32(_SOURCE_MONITOR),
                   "cursor_mode": _u32(_CURSOR_EMBEDDED),
                   "persist_mode": _u32(_PERSIST_ALWAYS),
                   "multiple": False}
        if self._restore:
            options["restore_token"] = self._restore
        self._request("SelectSources", [QDBusObjectPath(self.session)],
                      options, self._selected)

    def _selected(self, code: int, _results: dict) -> None:
        if code != 0:
            self._fail("" if code == 1 else "SelectSources")
            return
        self._request("Start", [QDBusObjectPath(self.session), ""], {},
                      self._started)

    def _started(self, code: int, results: dict) -> None:
        if code != 0:
            self._fail("" if code == 1 else "Start")
            return
        token = results.get("restore_token")
        if isinstance(token, str) and token:
            self.token.emit(token)
        streams = results.get("streams") or []
        node = 0
        for stream in streams:
            try:
                ident, props = stream[0], stream[1]
            except (TypeError, IndexError, KeyError):
                continue
            if isinstance(props, dict) and props.get("mapping_id") == self.output:
                node = int(ident)
        if not node:
            self._fail("wrong screen")
            return
        self.node = node
        self._tries = []
        self._launch()

    # ---------------------------------------------------------- encoder
    def _remote(self) -> int:
        reply = self.portal.call("OpenPipeWireRemote",
                                 QDBusObjectPath(self.session), {})
        arguments = reply.arguments()
        if (reply.type() == QDBusMessage.MessageType.ErrorMessage
                or not arguments or not hasattr(arguments[0], "fileDescriptor")):
            return -1
        return os.dup(arguments[0].fileDescriptor())

    def _launch(self) -> None:
        fd = self._remote()
        if fd < 0:
            self._fail("OpenPipeWireRemote")
            return
        if not self._tries:
            self._tries = pipelines(fd, self.node)
        else:   # a retry: the same pipeline with this new descriptor
            self._tries = [[f"fd={fd}" if a.startswith("fd=") and i == 1 else a
                            for i, a in enumerate(t)] for t in self._tries]
        arguments = self._tries[0]
        self._got_frame = False
        try:
            process = subprocess.Popen(
                ["gst-launch-1.0", "-q", *arguments], pass_fds=(fd,),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            os.close(fd)
            self._fail("gst-launch-1.0")
            return
        os.close(fd)
        self._process = process
        threading.Thread(target=self._read, args=(process,),
                         daemon=True).start()

    def _read(self, process: subprocess.Popen) -> None:
        reader = FrameReader()
        fd = process.stdout.fileno()
        while True:
            try:
                data = os.read(fd, 1 << 20)
            except OSError:
                break
            if not data:
                break
            for frame in reader.feed(data):
                if not self._got_frame:
                    self._got_frame = True
                    self.ready.emit()
                try:
                    self.on_frame(frame)
                except Exception:                     # noqa: BLE001
                    pass
        process.stdout.close()
        self._ended.emit(process, process.wait())

    def _on_ended(self, process: subprocess.Popen, _status: int) -> None:
        if process is not self._process or not self.running:
            return
        self._process = None
        if not self._got_frame and len(self._tries) > 1:
            self._tries.pop(0)                # the GPU could not; use the CPU
            self._launch()
            return
        self._fail("stream ended")

    # ------------------------------------------------------------- stop
    def _fail(self, reason: str) -> None:
        self.stop()
        self.failed.emit(reason)

    def stop(self) -> None:
        self.running = False
        process, self._process = self._process, None
        if process is not None:
            process.terminate()
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                process.kill()
        if self.session:
            QDBusInterface(PORTAL, self.session,
                           "org.freedesktop.portal.Session",
                           self.bus).call("Close")
            self.session = ""
        self._waiting.clear()
