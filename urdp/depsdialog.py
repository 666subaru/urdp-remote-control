"""The dialog that offers to install missing system packages."""

from __future__ import annotations

from PyQt6.QtCore import QProcess
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QVBoxLayout, QWidget)

from . import deps
from .i18n import _


class DepsDialog(QDialog):
    """Lists what is missing and runs the package manager through pkexec.

    Nothing is installed without the click, and pkexec asks for the password
    itself; this process never sees it.
    """

    def __init__(self, plan: deps.Plan, need_freerdp: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle(_("Missing packages"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        reasons = []
        if need_freerdp:
            reasons.append(_("FreeRDP 3 is required to connect."))
        if not deps.keyring.available():
            reasons.append(_("secret-tool is needed to save passwords."))
        intro = QLabel("\n".join(reasons))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if plan.argv:
            text = _("URDP can install these packages: {packages}").format(
                packages=", ".join(plan.packages))
        else:
            text = _("Your distribution was not recognised. Install FreeRDP 3 "
                     "and libsecret with its package manager.")
        info = QLabel(text)
        info.setWordWrap(True)
        layout.addWidget(info)

        if plan.command:
            line = QLineEdit(plan.command)
            line.setReadOnly(True)
            layout.addWidget(line)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_install = QPushButton(_("Install"))
        copy = QPushButton(_("Copy to Clipboard"))
        self.btn_close = QPushButton(_("Not now"))
        if plan.argv and deps.can_elevate():
            buttons.addWidget(self.btn_install)
            self.btn_install.setDefault(True)
        if plan.command:
            buttons.addWidget(copy)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)

        self.btn_install.clicked.connect(self._install)
        copy.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(plan.command))
        self.btn_close.clicked.connect(self.reject)

        self.process = QProcess(self)
        self.process.finished.connect(self._finished)

    def _install(self) -> None:
        self.btn_install.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.status.setText(_("Installing…"))
        self.process.start("pkexec", list(self.plan.argv))

    def _finished(self, code: int, _status) -> None:
        deps.refresh()
        self.btn_close.setEnabled(True)
        if code == 0:
            self.accept()
            return
        self.btn_install.setEnabled(True)
        self.status.setText(
            _("Installation failed (exit code {code}).").format(code=code))
