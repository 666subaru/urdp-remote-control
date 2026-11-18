"""Sound for the tablet screen: a virtual speaker whose sound plays on the
tablet.

While sharing runs, a PipeWire/PulseAudio null sink named "URDP Tablet" is the
default output, so programs play into it (the desktop's own sound settings
can still send a program elsewhere).  ``parec`` records what it gets as
48 kHz 16-bit stereo, and every 20 ms piece goes to the tablet over a
WebSocket.  When sharing stops, the previous default output comes back.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable

SINK = "urdp_tablet"
DESCRIPTION = "URDP Tablet"
RATE, CHANNELS = 48000, 2
CHUNK = RATE // 50 * CHANNELS * 2           # 20 ms of s16le


def available() -> bool:
    return bool(shutil.which("pactl") and shutil.which("parec"))


def _pactl(*arguments: str) -> str:
    try:
        result = subprocess.run(["pactl", *arguments], capture_output=True,
                                text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _our_modules() -> list[str]:
    """Null sinks of ours still loaded, also from a URDP that crashed."""
    found = []
    for line in _pactl("list", "short", "modules").splitlines():
        parts = line.split("\t")
        if (len(parts) >= 3 and parts[1] == "module-null-sink"
                and f"sink_name={SINK}" in parts[2].split()):
            found.append(parts[0])
    return found


class Speaker:
    """The virtual speaker and its recorder.  *listener* gets each piece of
    sound (bytes); silence is not passed on."""

    def __init__(self, listener: Callable[[bytes], None]) -> None:
        self.listener = listener
        self._module = ""
        self._previous = ""
        self._process: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self._process is not None

    def start(self) -> bool:
        if self.running or not available():
            return self.running
        for module in _our_modules():
            _pactl("unload-module", module)
        self._previous = _pactl("get-default-sink")
        self._module = _pactl(
            "load-module", "module-null-sink", f"sink_name={SINK}",
            f"rate={RATE}", f"channels={CHANNELS}",
            f'sink_properties="device.description=\\"{DESCRIPTION}\\""')
        if not self._module:
            return False
        _pactl("set-default-sink", SINK)
        try:
            self._process = subprocess.Popen(
                ["parec", "-d", f"{SINK}.monitor", "--format=s16le",
                 f"--rate={RATE}", f"--channels={CHANNELS}",
                 "--latency-msec=20", "--raw"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            self.stop()
            return False
        threading.Thread(target=self._read, args=(self._process,),
                         daemon=True).start()
        return True

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.terminate()
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._module:
            # Give the default back before the sink goes, so the desktop
            # does not keep remembering ours as the one the user chose.
            if (self._previous and self._previous != SINK
                    and _pactl("get-default-sink") == SINK):
                _pactl("set-default-sink", self._previous)
            _pactl("unload-module", self._module)
            self._module = ""

    def _read(self, process: subprocess.Popen) -> None:
        fd = process.stdout.fileno()
        silence = bytes(CHUNK)
        pending = b""
        while True:
            try:
                data = os.read(fd, CHUNK)
            except OSError:
                break
            if not data:
                break
            pending += data
            while len(pending) >= CHUNK:
                piece, pending = pending[:CHUNK], pending[CHUNK:]
                if piece != silence:
                    try:
                        self.listener(piece)
                    except Exception:                 # noqa: BLE001
                        pass
        process.stdout.close()
