"""Sound for the tablet screen: a virtual speaker whose sound plays on the
tablet.

While sharing runs, a PipeWire/PulseAudio null sink named "URDP Tablet" is the
default output, so programs play into it (the desktop's own sound settings
can still send a program elsewhere).  ``parec`` records what it gets as
48 kHz 16-bit stereo, and every 10 ms piece goes to the tablet over a
WebSocket.  When sharing stops, the previous default output comes back.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable

from .store import config_dir

SINK = "urdp_tablet"
DESCRIPTION = "URDP Tablet"
RATE, CHANNELS = 48000, 2
CHUNK = RATE // 100 * CHANNELS * 2          # 10 ms of s16le


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


# A URDP that crashed cannot give the default back, so the one it replaced is
# noted in a file while ours is the default; the next URDP puts it back.
def _note(kind: str):
    return config_dir() / f"tablet-{kind}-previous"


def remember(kind: str, previous: str) -> None:
    try:
        path = _note(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{previous}\n{os.getpid()}\n", "utf-8")
    except OSError:
        pass


def forget(kind: str) -> None:
    try:
        _note(kind).unlink(missing_ok=True)
    except OSError:
        pass


def recall(kind: str) -> tuple[str, bool]:
    """The noted default, and whether the URDP that noted it still runs
    (then it is not ours to undo)."""
    try:
        previous, pid = _note(kind).read_text("utf-8").split("\n")[:2]
        pid_number = int(pid)
    except (OSError, ValueError):
        return "", False
    alive = False
    if pid_number != os.getpid():
        try:
            os.kill(pid_number, 0)
            alive = True
        except ProcessLookupError:
            pass
        except PermissionError:
            alive = True
    return previous.strip(), alive


def undo(kind: str, ours: str, modules: list[str]) -> None:
    """Unloads *modules* left behind and gives back the default *kind*
    ("sink" or "source") they took, unless another URDP still uses them."""
    previous, alive = recall(kind)
    if alive:
        return
    if modules:
        if previous and previous != ours and _pactl(f"get-default-{kind}") == ours:
            _pactl(f"set-default-{kind}", previous)
        for module in modules:
            _pactl("unload-module", module)
    forget(kind)


def recover() -> None:
    """What a URDP that crashed left behind: our speaker, maybe still the
    default output."""
    if available():
        undo("sink", SINK, _our_modules())


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
        undo("sink", SINK, _our_modules())
        self._previous = _pactl("get-default-sink")
        self._module = _pactl(
            "load-module", "module-null-sink", f"sink_name={SINK}",
            f"rate={RATE}", f"channels={CHANNELS}",
            f'sink_properties="device.description=\\"{DESCRIPTION}\\""')
        if not self._module:
            return False
        if self._previous and self._previous != SINK:
            remember("sink", self._previous)
        _pactl("set-default-sink", SINK)
        try:
            self._process = subprocess.Popen(
                ["parec", "-d", f"{SINK}.monitor", "--format=s16le",
                 f"--rate={RATE}", f"--channels={CHANNELS}",
                 "--latency-msec=10", "--raw"],
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
            forget("sink")

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
