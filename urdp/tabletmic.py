"""The tablet's microphone as a microphone of this computer.

While sharing runs with the microphone on, a virtual source named
"URDP Tablet Microphone" is the default input, so programs record from it
(the desktop's sound settings can still pick another).  Behind it is a null
sink that ``pacat`` plays into: the page sends what the tablet hears as
10 ms pieces of 48 kHz 16-bit mono, and they go straight to ``pacat``.
When sharing stops, the previous default input comes back.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import threading

from .tabletaudio import _pactl

SOURCE = "urdp_tablet_mic"
SINK = "urdp_tablet_mic_in"          # what pacat plays into; not for programs
DESCRIPTION = "URDP Tablet Microphone"
RATE, CHANNELS = 48000, 1
#: The most one message may carry: 100 ms; the page sends 10 ms at a time.
MAX_PIECE = RATE // 10 * CHANNELS * 2
#: The pipe to pacat holds this much (about 85 ms); when the tablet sends
#: faster than the computer plays, what does not fit is dropped, so the
#: delay cannot grow.
PIPE_SIZE = 8192


def available() -> bool:
    return bool(shutil.which("pactl") and shutil.which("pacat"))


def _our_modules() -> list[str]:
    """Modules of ours still loaded, also from a URDP that crashed; the
    source first, as it sits on the sink."""
    sources, sinks = [], []
    for line in _pactl("list", "short", "modules").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        words = parts[2].split()
        if parts[1] == "module-remap-source" and f"source_name={SOURCE}" in words:
            sources.append(parts[0])
        elif parts[1] == "module-null-sink" and f"sink_name={SINK}" in words:
            sinks.append(parts[0])
    return sources + sinks


class Microphone:
    """The virtual microphone; :meth:`write` feeds it."""

    def __init__(self) -> None:
        self._modules: list[str] = []
        self._previous = ""
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None

    def start(self) -> bool:
        if self.running or not available():
            return self.running
        for module in _our_modules():
            _pactl("unload-module", module)
        self._previous = _pactl("get-default-source")
        sink = _pactl(
            "load-module", "module-null-sink", f"sink_name={SINK}",
            f"rate={RATE}", f"channels={CHANNELS}", "channel_map=mono",
            f'sink_properties="device.description=\\"{DESCRIPTION} (in)\\""')
        if not sink:
            return False
        self._modules = [sink]
        source = _pactl(
            "load-module", "module-remap-source", f"source_name={SOURCE}",
            f"master={SINK}.monitor", f"channels={CHANNELS}",
            "channel_map=mono",
            f'source_properties="device.description=\\"{DESCRIPTION}\\""')
        if not source:
            self.stop()
            return False
        self._modules.insert(0, source)
        _pactl("set-default-source", SOURCE)
        try:
            self._process = subprocess.Popen(
                ["pacat", "--playback", "-d", SINK, "--format=s16le",
                 f"--rate={RATE}", f"--channels={CHANNELS}",
                 "--latency-msec=20", "--raw"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except OSError:
            self.stop()
            return False
        fd = self._process.stdin.fileno()
        try:
            fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, PIPE_SIZE)
        except (OSError, AttributeError):
            pass
        os.set_blocking(fd, False)
        return True

    def write(self, piece: bytes) -> None:
        """A piece of sound from the tablet (any thread)."""
        if not piece or len(piece) > MAX_PIECE or len(piece) % 2:
            return
        with self._lock:
            process = self._process
            if process is None:
                return
            try:
                # Pieces are below PIPE_BUF, so each goes whole or not at all.
                os.write(process.stdin.fileno(), piece)
            except (BlockingIOError, OSError, ValueError):
                pass                                    # full: dropped

    def stop(self) -> None:
        with self._lock:
            process, self._process = self._process, None
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
        if self._modules:
            # Give the default back before the source goes, so the desktop
            # does not keep remembering ours as the one the user chose.
            if (self._previous and self._previous != SOURCE
                    and _pactl("get-default-source") == SOURCE):
                _pactl("set-default-source", self._previous)
            for module in self._modules:
                _pactl("unload-module", module)
            self._modules = []
