"""The "General" tab: where to connect and as whom."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QSpinBox,
                             QVBoxLayout)

from .i18n import _
from .profile import Profile
from .widgets import TabPage, body, hint, section


class GeneralTab(TabPage):
    #: Emitted when the user asks for one of the profile file actions.
    save_requested = pyqtSignal()
    save_as_requested = pyqtSignal()
    open_requested = pyqtSignal()
    new_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    profile_selected = pyqtSignal(int)
    test_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # ------------------------------------------------------ logon settings
        box, content = section(_("Logon settings"),
                               ["computer", "network-server", "computer-symbolic"])
        content.addWidget(body(_("Enter the name of the remote computer.")))

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.host = QComboBox()
        self.host.setEditable(True)
        self.host.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.host.lineEdit().setPlaceholderText(_("Example: computer.fabrikam.com"))
        self.btn_test = QPushButton(_("Test"))
        self.btn_test.setObjectName("compact")
        self.btn_test.setToolTip(
            _("Checks whether the address resolves and port 3389 answers, "
              "before connecting."))
        host_row = QHBoxLayout()
        host_row.setSpacing(6)
        host_row.addWidget(self.host, 1)
        host_row.addWidget(self.btn_test)
        form.addRow(_("Computer:"), host_row)

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(3389)
        self.port.setFixedWidth(112)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port)
        port_row.addSpacing(8)
        port_row.addWidget(hint(_("(3389 by default)")))
        port_row.addStretch(1)
        form.addRow(_("Port:"), port_row)

        self.username = QLineEdit()
        form.addRow(_("User name:"), self.username)

        self.domain = QLineEdit()
        self.domain.setPlaceholderText(_("optional"))
        form.addRow(_("Domain:"), self.domain)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(_("asked when connecting"))
        form.addRow(_("Password:"), self.password)

        content.addLayout(form)

        self.save_password = QCheckBox(_("Store the password in the keyring"))
        self.save_password.setToolTip(
            _("The password is kept by libsecret (KDE Wallet / GNOME Keyring); "
              "it is never written to the configuration file."))
        content.addWidget(self.save_password)

        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        content.addWidget(self.status)
        root.addWidget(box)

        # ------------------------------------------------ connection settings
        box2, content2 = section(_("Connection settings"),
                                 ["folder", "document-save", "folder-symbolic"])
        content2.addWidget(body(
            _("Save the current settings as a profile or an .rdp file, or open "
              "a saved connection.")))

        self.profiles = QComboBox()
        self.profiles.setMinimumWidth(220)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel(_("Profile:")))
        profile_row.addWidget(self.profiles, 1)
        content2.addLayout(profile_row)

        buttons = QHBoxLayout()
        self.btn_save = QPushButton(_("Save"))
        self.btn_save_as = QPushButton(_("Save As…"))
        self.btn_open = QPushButton(_("Open…"))
        self.btn_new = QPushButton(_("New"))
        self.btn_delete = QPushButton(_("Delete"))
        for button in (self.btn_save, self.btn_save_as, self.btn_open,
                       self.btn_new, self.btn_delete):
            button.setObjectName("compact")
            buttons.addWidget(button)
        buttons.addStretch(1)
        content2.addLayout(buttons)
        content2.addWidget(hint(
            _("\"Save\" stores the profile in this application; \"Save As\" "
              "writes an .rdp file that Windows mstsc.exe can open.")))
        root.addWidget(box2)
        root.addStretch(1)

        self.btn_save.clicked.connect(self.save_requested)
        self.btn_save_as.clicked.connect(self.save_as_requested)
        self.btn_open.clicked.connect(self.open_requested)
        self.btn_new.clicked.connect(self.new_requested)
        self.btn_delete.clicked.connect(self.delete_requested)
        self.btn_test.clicked.connect(self.test_requested)
        self.profiles.activated.connect(self.profile_selected)

    # --------------------------------------------------------------- helpers
    def set_profiles(self, labels: list[str], current: int) -> None:
        blocked = self.profiles.blockSignals(True)
        self.profiles.clear()
        self.profiles.addItems(labels)
        if 0 <= current < len(labels):
            self.profiles.setCurrentIndex(current)
        self.profiles.blockSignals(blocked)

    def set_history(self, hosts: list[str]) -> None:
        text = self.host.currentText()
        blocked = self.host.blockSignals(True)
        self.host.clear()
        self.host.addItems(hosts)
        self.host.setCurrentText(text)
        self.host.blockSignals(blocked)

    # ------------------------------------------------------------- TabPage
    def load(self, profile: Profile) -> None:
        self.host.setCurrentText(profile.host)
        self.port.setValue(profile.port or 3389)
        self.username.setText(profile.username)
        self.domain.setText(profile.domain)
        self.save_password.setChecked(profile.save_password)

    def save(self, profile: Profile) -> None:
        profile.host = self.host.currentText().strip()
        profile.port = self.port.value()
        profile.username = self.username.text().strip()
        profile.domain = self.domain.text().strip()
        profile.save_password = self.save_password.isChecked()
