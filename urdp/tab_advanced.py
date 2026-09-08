"""The "Advanced" tab: certificate policy, gateway, session type, RemoteApp."""

from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QSpinBox, QVBoxLayout)

from .i18n import _
from .profile import Profile
from .widgets import TabPage, body, hint, section

CERT_POLICIES = [
    ("warn", "Warn me (remember the certificate on first connect)"),
    ("connect", "Connect and don't warn me"),
    ("refuse", "Do not connect"),
]

SECURITY = [("auto", "Negotiate automatically"),
            ("nla", "NLA (Network Level Authentication)"),
            ("tls", "TLS"),
            ("rdp", "Legacy RDP security")]

GATEWAY_TYPES = [("auto", "Automatic"), ("rpc", "RPC over HTTP"),
                 ("http", "HTTP")]


class AdvancedTab(TabPage):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # ------------------------------------------- server authentication
        box, content = section(_("Server authentication"),
                               ["security-high", "document-encrypted"])
        content.addWidget(body(
            _("Server authentication verifies that you are connecting to the "
              "intended remote computer.")))
        content.addWidget(body(_("If server authentication fails:")))
        self.cert_policy = QComboBox()
        for value, label in CERT_POLICIES:
            self.cert_policy.addItem(_(label), value)
        content.addWidget(self.cert_policy)
        content.addWidget(hint(
            _("The application has no terminal, so the certificate question "
              "cannot be shown on screen and the answer has to be decided up "
              "front. \"Warn me\" stores the certificate on the first connect "
              "and refuses the connection if it changes later.")))

        security_row = QHBoxLayout()
        security_row.addWidget(QLabel(_("Security layer:")))
        self.security = QComboBox()
        for value, label in SECURITY:
            self.security.addItem(_(label), value)
        security_row.addWidget(self.security, 1)
        content.addLayout(security_row)
        root.addWidget(box)

        # ---------------------------------------------------- connect anywhere
        box2, content2 = section(_("Connect from anywhere"),
                                 ["network-vpn", "network-server"])
        self.gateway_enabled = QCheckBox(
            _("Use a Remote Desktop Gateway server"))
        content2.addWidget(self.gateway_enabled)

        gateway_form = QFormLayout()
        gateway_form.setHorizontalSpacing(10)
        self.gateway_host = QLineEdit()
        self.gateway_host.setPlaceholderText("gateway.fabrikam.com")
        gateway_form.addRow(_("Server name:"), self.gateway_host)

        self.gateway_port = QSpinBox()
        self.gateway_port.setRange(1, 65535)
        self.gateway_port.setValue(443)
        self.gateway_port.setFixedWidth(112)
        gateway_form.addRow(_("Port:"), self.gateway_port)

        self.gateway_type = QComboBox()
        for value, label in GATEWAY_TYPES:
            self.gateway_type.addItem(_(label), value)
        gateway_form.addRow(_("Transport:"), self.gateway_type)

        self.gateway_same_creds = QCheckBox(
            _("Use the same credentials as the remote computer"))
        gateway_form.addRow("", self.gateway_same_creds)

        self.gateway_username = QLineEdit()
        gateway_form.addRow(_("User name:"), self.gateway_username)
        self.gateway_domain = QLineEdit()
        gateway_form.addRow(_("Domain:"), self.gateway_domain)

        self.gateway_bypass_local = QCheckBox(
            _("Bypass the gateway for local addresses"))
        gateway_form.addRow("", self.gateway_bypass_local)
        content2.addLayout(gateway_form)
        self._gateway_fields = (
            self.gateway_host, self.gateway_port, self.gateway_type,
            self.gateway_same_creds, self.gateway_username,
            self.gateway_domain, self.gateway_bypass_local)
        root.addWidget(box2)

        # ------------------------------------------------------------- session
        box3, content3 = section(_("Session"), ["system-run",
                                                "utilities-terminal"])
        self.admin_session = QCheckBox(
            _("Connect to the administrative (console) session"))
        self.restricted_admin = QCheckBox(
            _("Restricted admin mode (the password is not sent to the server)"))
        content3.addWidget(self.admin_session)
        content3.addWidget(self.restricted_admin)

        timeout_row = QHBoxLayout()
        timeout_row.addWidget(QLabel(_("Connection timeout:")))
        self.timeout_ms = QSpinBox()
        self.timeout_ms.setRange(0, 600000)
        self.timeout_ms.setSingleStep(1000)
        self.timeout_ms.setSuffix(" ms")
        self.timeout_ms.setSpecialValueText(_("default"))
        self.timeout_ms.setFixedWidth(150)
        timeout_row.addWidget(self.timeout_ms)
        timeout_row.addStretch(1)
        content3.addLayout(timeout_row)
        root.addWidget(box3)

        # ---------------------------------------------------------- RemoteApp
        box4, content4 = section("RemoteApp", ["application-x-executable",
                                               "applications-other"])
        self.remote_app = QCheckBox(
            _("Start a single remote application instead of the desktop"))
        content4.addWidget(self.remote_app)
        app_form = QFormLayout()
        self.remote_app_program = QLineEdit()
        self.remote_app_program.setPlaceholderText(
            r"C:\Windows\System32\notepad.exe")
        app_form.addRow(_("Program:"), self.remote_app_program)
        self.remote_app_cmdline = QLineEdit()
        app_form.addRow(_("Arguments:"), self.remote_app_cmdline)
        content4.addLayout(app_form)
        root.addWidget(box4)

        # ------------------------------------------------- extra FreeRDP flags
        box5, content5 = section(_("Extra FreeRDP options"),
                                 ["utilities-terminal", "text-x-script"])
        self.extra_args = QLineEdit()
        self.extra_args.setPlaceholderText("/log-level:INFO +fipsmode")
        content5.addWidget(self.extra_args)
        content5.addWidget(hint(
            _("This text is appended to the command line verbatim.")))
        root.addWidget(box5)
        root.addStretch(1)

        self.gateway_enabled.toggled.connect(self._update_gateway)
        self.gateway_same_creds.toggled.connect(self._update_gateway)
        self.remote_app.toggled.connect(self._update_remote_app)
        self._update_gateway()
        self._update_remote_app()

    # --------------------------------------------------------------- helpers
    def _update_gateway(self) -> None:
        on = self.gateway_enabled.isChecked()
        for widget in self._gateway_fields:
            widget.setEnabled(on)
        own_creds = on and not self.gateway_same_creds.isChecked()
        self.gateway_username.setEnabled(own_creds)
        self.gateway_domain.setEnabled(own_creds)

    def _update_remote_app(self) -> None:
        on = self.remote_app.isChecked()
        self.remote_app_program.setEnabled(on)
        self.remote_app_cmdline.setEnabled(on)

    # ------------------------------------------------------------- TabPage
    def load(self, p: Profile) -> None:
        def select(combo: QComboBox, value) -> None:
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)

        select(self.cert_policy, p.cert_policy)
        select(self.security, p.security)
        select(self.gateway_type, p.gateway_type)

        self.gateway_enabled.setChecked(p.gateway_enabled)
        self.gateway_host.setText(p.gateway_host)
        self.gateway_port.setValue(p.gateway_port or 443)
        self.gateway_same_creds.setChecked(p.gateway_same_creds)
        self.gateway_username.setText(p.gateway_username)
        self.gateway_domain.setText(p.gateway_domain)
        self.gateway_bypass_local.setChecked(p.gateway_bypass_local)

        self.admin_session.setChecked(p.admin_session)
        self.restricted_admin.setChecked(p.restricted_admin)
        self.timeout_ms.setValue(p.timeout_ms)

        self.remote_app.setChecked(p.remote_app)
        self.remote_app_program.setText(p.remote_app_program)
        self.remote_app_cmdline.setText(p.remote_app_cmdline)
        self.extra_args.setText(p.extra_args)

        self._update_gateway()
        self._update_remote_app()

    def save(self, p: Profile) -> None:
        p.cert_policy = self.cert_policy.currentData()
        p.security = self.security.currentData()
        p.gateway_enabled = self.gateway_enabled.isChecked()
        p.gateway_host = self.gateway_host.text().strip()
        p.gateway_port = self.gateway_port.value()
        p.gateway_type = self.gateway_type.currentData()
        p.gateway_same_creds = self.gateway_same_creds.isChecked()
        p.gateway_username = self.gateway_username.text().strip()
        p.gateway_domain = self.gateway_domain.text().strip()
        p.gateway_bypass_local = self.gateway_bypass_local.isChecked()
        p.admin_session = self.admin_session.isChecked()
        p.restricted_admin = self.restricted_admin.isChecked()
        p.timeout_ms = self.timeout_ms.value()
        p.remote_app = self.remote_app.isChecked()
        p.remote_app_program = self.remote_app_program.text().strip()
        p.remote_app_cmdline = self.remote_app_cmdline.text().strip()
        p.extra_args = self.extra_args.text().strip()
