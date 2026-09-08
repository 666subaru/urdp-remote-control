"""Launch and supervise the FreeRDP child process."""

from __future__ import annotations

import re

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

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

        self.started.emit()
        return None

    def is_running(self) -> bool:
        return (self._process is not None
                and self._process.state() != QProcess.ProcessState.NotRunning)

    def stop(self) -> None:
        if self.is_running() and self._process is not None:
            self._process.terminate()
            if not self._process.waitForFinished(3000):
                self._process.kill()

    # ----------------------------------------------------------- housekeeping
    def _drain_stderr(self) -> None:
        if self._process is None:
            return
        chunk = bytes(self._process.readAllStandardError()).decode(
            "utf-8", "replace")
        if chunk:
            self._stderr.append(chunk)
            self.log.emit(chunk)

    def _drain_stdout(self) -> None:
        if self._process is None:
            return
        chunk = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", "replace")
        if chunk:
            self._stderr.append(chunk)
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
