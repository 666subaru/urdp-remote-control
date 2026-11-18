"""The tablet's camera as a camera of this computer.

The page sends what the camera sees as JPEGs; ``gst-launch-1.0`` decodes
them, fits them into 1280 × 720 (black bars where the shape differs, as when
the tablet stands upright) and hands them on:

* to a ``v4l2loopback`` device when one is loaded and writable: every
  program sees that, browsers and video call apps included.  Loading the
  module needs root once, for example
  ``modprobe v4l2loopback exclusive_caps=1 card_label="URDP Tablet Camera"``;
* otherwise to a PipeWire camera named "URDP Tablet Camera", which programs
  that use PipeWire's camera support see (OBS, Firefox, KDE's own), and
  others do not.

The camera is there while sharing runs; before the first picture, and
while no tablet sends one, it shows the last picture (black at first).
"""

from __future__ import annotations

import fcntl
import glob
import os
import shutil
import subprocess
import termios
import threading
from pathlib import Path

from .tabletvideo import _has_element

DESCRIPTION = "URDP Tablet Camera"
WIDTH, HEIGHT = 1280, 720
#: A picture larger than this is not from the page.
MAX_PICTURE = 4 * 1024 * 1024
#: Nor one with more pixels than this: a few bytes can claim 65000 × 65000,
#: and the decoder would set aside gigabytes for it.
MAX_PIXELS = 4096 * 4096
PIPE_SIZE = 1024 * 1024
#: Pictures waiting for GStreamer: beyond about two, new ones are dropped,
#: so the camera never lags.
BACKLOG = 2


def available() -> bool:
    return (bool(shutil.which("gst-launch-1.0"))
            and all(_has_element(e) for e in
                    ("fdsrc", "jpegparse", "jpegdec", "videoconvert",
                     "videoscale"))
            and (_has_element("pipewiresink") or _has_element("v4l2sink")))


def loopback() -> str:
    """The writable v4l2loopback device named for URDP, or "".  Only that
    one: another loopback device may be some other program's camera."""
    for entry in sorted(glob.glob("/sys/class/video4linux/video*")):
        # Loopback devices hang off the virtual bus; cameras off USB or PCI.
        if "/devices/virtual/" not in os.path.realpath(entry):
            continue
        try:
            name = Path(entry, "name").read_text("utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if name != DESCRIPTION:
            continue
        device = "/dev/" + os.path.basename(entry)
        if os.access(device, os.W_OK):
            return device
    return ""


def picture_size(picture: bytes) -> tuple[int, int]:
    """Width and height a JPEG declares, or (0, 0) when it has none."""
    at = 2
    while at + 4 <= len(picture):
        if picture[at] != 0xFF:
            return 0, 0
        marker = picture[at + 1]
        if marker == 0xFF:                       # fill byte
            at += 1
            continue
        if marker in (0x01, *range(0xD0, 0xD8)):  # no length
            at += 2
            continue
        length = int.from_bytes(picture[at + 2:at + 4], "big")
        if length < 2:
            return 0, 0
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if at + 9 > len(picture):
                return 0, 0
            height = int.from_bytes(picture[at + 5:at + 7], "big")
            width = int.from_bytes(picture[at + 7:at + 9], "big")
            return width, height
        if marker == 0xDA:                       # the image data: no size
            return 0, 0
        at += 2 + length
    return 0, 0


def _black() -> bytes:
    """A black JPEG to show before the first picture."""
    try:
        result = subprocess.run(
            ["gst-launch-1.0", "-q", "videotestsrc", "pattern=black",
             "num-buffers=1", "!",
             f"video/x-raw,width={WIDTH},height={HEIGHT}", "!", "jpegenc",
             "!", "fdsink", "fd=1"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return b""
    return result.stdout if result.stdout.startswith(b"\xff\xd8") else b""


def pipeline(device: str) -> list[str]:
    """gst-launch arguments: JPEGs on standard input to *device* (a
    v4l2loopback node) or, without one, to a PipeWire camera."""
    head = ["fdsrc", "fd=0", "do-timestamp=true", "!", "jpegparse", "!",
            "jpegdec", "!", "videoconvert", "!", "videoscale",
            "add-borders=true", "!",
            f"video/x-raw,format=YUY2,width={WIDTH},height={HEIGHT},"
            "pixel-aspect-ratio=1/1", "!"]
    if device:
        return head + ["v4l2sink", f"device={device}", "sync=false"]
    # gst-launch joins its arguments and parses them again, so the value
    # is quoted as a whole and the name with spaces inside it.
    return head + [
        "pipewiresink", "mode=provide", "sync=false",
        'stream-properties="properties,media.class=Video/Source,'
        "media.role=Camera,node.name=urdp_tablet_camera,"
        f'node.description=\\"{DESCRIPTION}\\""']


class Camera:
    """The virtual camera; :meth:`write` feeds it."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        #: "v4l2" or "pipewire" while running
        self.kind = ""

    @property
    def running(self) -> bool:
        return self._process is not None

    def start(self) -> bool:
        if self.running or not available():
            return self.running
        device = loopback() if _has_element("v4l2sink") else ""
        if not device and not _has_element("pipewiresink"):
            return False
        try:
            process = subprocess.Popen(
                ["gst-launch-1.0", "-q", *pipeline(device)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except OSError:
            return False
        fd = process.stdin.fileno()
        try:
            fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, PIPE_SIZE)
        except (OSError, AttributeError):
            pass
        os.set_blocking(fd, False)
        self._process = process
        self.kind = "v4l2" if device else "pipewire"
        black = _black()
        if black:
            self.write(black)
        return True

    def write(self, picture: bytes) -> None:
        """One JPEG from the tablet (any thread).  Written whole or not at
        all: a piece of a picture would spoil the next ones."""
        if (not 4 <= len(picture) <= MAX_PICTURE
                or not picture.startswith(b"\xff\xd8")
                or not picture.rstrip(b"\0").endswith(b"\xff\xd9")):
            return
        width, height = picture_size(picture)
        if not (0 < width and 0 < height and width * height <= MAX_PIXELS):
            return
        with self._lock:
            process = self._process
            if process is None:
                return
            try:
                fd = process.stdin.fileno()
                size = fcntl.fcntl(fd, fcntl.F_GETPIPE_SZ)
                waiting = int.from_bytes(fcntl.ioctl(
                    fd, termios.FIONREAD, b"\0\0\0\0"), "little")
                # The margin: a pipe holds pages, not bytes, so a write of
                # exactly the free bytes might not fit whole.
                if (waiting + len(picture) + 65536 > size
                        or waiting > (BACKLOG - 1) * len(picture)):
                    return                              # behind: dropped
                view = memoryview(picture)
                while view:                      # fits, so this cannot block
                    view = view[os.write(fd, view):]
            except (BlockingIOError, OSError, ValueError):
                pass

    def stop(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        self.kind = ""
        if process is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            process.terminate()
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                process.kill()

