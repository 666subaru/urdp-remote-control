"""Launch and supervise the FreeRDP child process."""

from __future__ import annotations

import datetime
import os
import re
import shlex
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

from .i18n import _
from .profile import Profile
from .rdpcmd import build_command, find_client

# FreeRDP names its connection failures in the log, e.g.
#   [ERROR][com.freerdp.core] - ... ERRCONNECT_LOGON_FAILURE [0x00020014]
# Reading the name is more reliable than guessing at exit codes, which differ
# between client backends.
_ERRCONNECT = re.compile(r"ERRCONNECT_[A-Z_]+")

_MESSAGES = {
    "ERRCONNECT_LOGON_FAILURE":
        "The user name or password is wrong.",
    "ERRCONNECT_WRONG_PASSWORD":
        "The password is wrong.",
    "ERRCONNECT_ACCOUNT_LOCKED_OUT":
        "The account is locked out.",
    "ERRCONNECT_ACCOUNT_DISABLED":
        "The account is disabled.",
    "ERRCONNECT_ACCOUNT_EXPIRED":
        "The account has expired.",
    "ERRCONNECT_PASSWORD_EXPIRED":
        "The password has expired and must be changed on Windows.",
    "ERRCONNECT_PASSWORD_MUST_CHANGE":
        "The password must be changed before signing in.",
    "ERRCONNECT_CONNECT_TRANSPORT_FAILED":
        "Could not reach the computer. Check the address, the port and the "
        "firewall.",
    "ERRCONNECT_CONNECT_FAILED":
        "Could not reach the computer: the address did not answer.",
    "ERRCONNECT_DNS_NAME_NOT_FOUND":
        "The computer name could not be resolved.",
    "ERRCONNECT_AUTHENTICATION_FAILED":
        "Authentication failed.",
    "ERRCONNECT_INSUFFICIENT_PRIVILEGES":
        "This user is not allowed to use Remote Desktop.",
    "ERRCONNECT_LOGON_TYPE_NOT_GRANTED":
        "The user has not been granted the right to sign in remotely.",
    "ERRCONNECT_NO_OR_MISSING_CREDENTIALS":
        "Credentials are missing.",
    "ERRCONNECT_TLS_CONNECT_FAILED":
        "The TLS handshake failed. Check the server certificate.",
    "ERRCONNECT_SECURITY_NEGO_CONNECT_FAILED":
        "Security negotiation failed. Try NLA or TLS on the Advanced tab.",
}


def log_path() -> Path:
    """Where the output of the most recent session is kept."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "urdp" / "last-session.log"


class Session(QObject):
    """A single ``xfreerdp3`` run."""

    started = pyqtSignal()
    #: (success, human readable message)
    finished = pyqtSignal(bool, str)
    log = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._stderr: list[str] = []
        self.command: list[str] = []
        # QProcess can report both errorOccurred and finished for one failed
        # start; without this the user would get two error dialogs.
        self._reported = True
        self._clipfix: QProcess | None = None

    # ---------------------------------------------------------------- launch
    def start(self, profile: Profile, password: str | None = None) -> str | None:
        """Start the client.  Returns an error string, or None on success."""
        if self.is_running():
            return _("A session is already running for this profile.")
        client = find_client()
        if not client:
            return _("FreeRDP was not found. Install it, e.g. "
                     "sudo pacman -S freerdp")
        if password and not profile.username.strip():
            # See validate(): the lone stdin line would land in the user name
            # field, not the password field.
            return _("The user name is empty. A user name is required "
                     "before a password can be sent.")

        self._stderr = []
        self._reported = False
        self.command = build_command(profile, with_password=bool(password),
                                     client=client)

        process = QProcess(self)
        process.setProgram(self.command[0])
        process.setArguments(self.command[1:])
        process.readyReadStandardError.connect(self._drain_stderr)
        process.readyReadStandardOutput.connect(self._drain_stdout)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process

        self._open_log()
        process.start()
        if not process.waitForStarted(5000):
            self._process = None
            self._reported = True
            return _("FreeRDP could not be started.")

        if password:
            # /from-stdin makes FreeRDP read the missing credential here, which
            # keeps the secret out of the process list.
            process.write((password + "\n").encode("utf-8"))
            process.waitForBytesWritten(1000)
        process.closeWriteChannel()

        if profile.clipboard:
            self._start_clipfix()
        self.started.emit()
        return None

    def is_running(self) -> bool:
        return (self._process is not None
                and self._process.state() != QProcess.ProcessState.NotRunning)

    def stop(self) -> None:
        self._stop_clipfix()
        if self.is_running() and self._process is not None:
            self._process.terminate()
            if not self._process.waitForFinished(3000):
                self._process.kill()

    # -------------------------------------------------------------- clipfix
    def _start_clipfix(self) -> None:
        """Run :mod:`urdp.clipfix` next to FreeRDP for this session.

        It needs an X display, which XWayland provides on a Wayland desktop;
        without one there is nothing to repair, because xfreerdp would not be
        running either.
        """
        if not os.environ.get("DISPLAY"):
            return
        helper = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("QT_QPA_PLATFORM", "xcb")
        root = str(Path(__file__).resolve().parent.parent)
        old_path = env.value("PYTHONPATH")
        env.insert("PYTHONPATH", root + (os.pathsep + old_path if old_path else ""))
        helper.setProcessEnvironment(env)
        helper.setProgram(sys.executable)
        helper.setArguments(["-m", "urdp.clipfix", str(os.getpid())])
        helper.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        helper.readyReadStandardOutput.connect(
            lambda: self._log_write(bytes(helper.readAllStandardOutput())
                                    .decode("utf-8", "replace")))
        helper.start()
        if helper.waitForStarted(3000):
            self._clipfix = helper
        else:
            self._log_write("[urdp-clipfix] could not be started\n")

    def _stop_clipfix(self) -> None:
        helper, self._clipfix = self._clipfix, None
        if helper is None:
            return
        if helper.state() != QProcess.ProcessState.NotRunning:
            helper.terminate()
            if not helper.waitForFinished(2000):
                helper.kill()
                helper.waitForFinished(1000)
        self._log_write(bytes(helper.readAllStandardOutput())
                        .decode("utf-8", "replace"))

    # ------------------------------------------------------------------ log
    def _open_log(self) -> None:
        """Start a fresh log, keeping the previous one as ``.prev``.

        FreeRDP's output is the only record of what went wrong once the
        window has closed; keeping it on disk means a problem can be read
        after the fact instead of being reproduced with a terminal open.
        The password never appears here: it travels over stdin, and the
        command line carries none.
        """
        self._log = None
        path = log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.replace(path.with_suffix(".prev.log"))
            handle = open(path, "w", encoding="utf-8")
            os.chmod(path, 0o600)
        except OSError:
            return
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        handle.write(f"# urdp session {stamp}\n")
        handle.write("# " + " ".join(shlex.quote(a) for a in self.command)
                     + "\n\n")
        handle.flush()
        self._log = handle

    def _log_write(self, text: str) -> None:
        if getattr(self, "_log", None) is not None:
            try:
                self._log.write(text)
                self._log.flush()
            except OSError:
                self._log = None

    def _close_log(self, exit_code: int, message: str) -> None:
        if getattr(self, "_log", None) is None:
            return
        self._log_write(f"\n# exit code {exit_code}: {message}\n")
        try:
            self._log.close()
        except OSError:
            pass
        self._log = None

    # ----------------------------------------------------------- housekeeping
    def _drain_stderr(self) -> None:
        if self._process is None:
            return
        chunk = bytes(self._process.readAllStandardError()).decode(
            "utf-8", "replace")
        if chunk:
            self._stderr.append(chunk)
            self._log_write(chunk)
            self.log.emit(chunk)

    def _drain_stdout(self) -> None:
        if self._process is None:
            return
        chunk = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", "replace")
        if chunk:
            self._stderr.append(chunk)
            self._log_write(chunk)
            self.log.emit(chunk)

    def _diagnose(self, exit_code: int) -> tuple[bool, str]:
        text = "".join(self._stderr)
        names = _ERRCONNECT.findall(text)
        for name in reversed(names):
            if name in _MESSAGES:
                return False, _(_MESSAGES[name])
        if names:
            return False, _("Connection error: {code}").format(code=names[-1])
        if exit_code == 0:
            return True, _("The session was closed.")
        tail = [line for line in text.splitlines() if "[ERROR]" in line]
        if tail:
            return False, tail[-1].strip()
        return False, _("FreeRDP exited with code {code}."
                        ).format(code=exit_code)

    def _on_finished(self, exit_code: int, _status) -> None:
        self._drain_stderr()
        self._drain_stdout()
        ok, message = self._diagnose(exit_code)
        self._stop_clipfix()
        self._close_log(exit_code, message)
        self._process = None
        if not self._reported:
            self._reported = True
            self.finished.emit(ok, message)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart:
            return
        self._process = None
        if not self._reported:
            self._reported = True
            self.finished.emit(False, _("FreeRDP could not be run."))

    @property
    def output(self) -> str:
        return "".join(self._stderr)
