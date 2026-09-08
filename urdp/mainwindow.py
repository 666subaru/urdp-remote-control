"""The main window: the five tabs, the profile list and the connect button."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                             QFileDialog, QFormLayout, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QMainWindow,
                             QMessageBox, QPlainTextEdit, QPushButton,
                             QScrollArea, QStatusBar, QTabWidget, QVBoxLayout,
                             QWidget)

from . import capabilities, i18n, keyring, probe, rdpfile, store
from .i18n import _
from .profile import Profile
from .rdpcmd import command_string, find_client, validate
from .session import Session
from .tab_advanced import AdvancedTab
from .tab_display import DisplayTab
from .tab_experience import ExperienceTab
from .tab_general import GeneralTab
from .tab_local import LocalResourcesTab
from .widgets import Banner, app_icon, separator

#: Exit code that asks ``__main__`` to build the window again, which is how a
#: language change takes effect without restarting the process.
RESTART_CODE = 77

APP_NAME = "URDP Remote Control"


class CommandDialog(QDialog):
    """Shows the generated FreeRDP command so it can be reused in a script."""

    def __init__(self, command: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("FreeRDP command"))
        self.resize(760, 260)
        layout = QVBoxLayout(self)
        label = QLabel(_("This profile runs the command below. The password is "
                         "not put on the command line; it is fed through the "
                         "process' standard input."))
        label.setWordWrap(True)
        layout.addWidget(label)
        view = QPlainTextEdit(command)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(view, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy = QPushButton(_("Copy to Clipboard"))
        close = QPushButton(_("Close"))
        buttons.addWidget(copy)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        copy.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(command))
        close.clicked.connect(self.accept)


class CredentialDialog(QDialog):
    """Asks for the password, with the "remember me" box Windows shows.

    ``QInputDialog`` cannot carry the checkbox, and the checkbox is the point:
    without it there is no way to start saving a password from the prompt
    itself, only from the General tab before connecting.
    """

    def __init__(self, profile: Profile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Credentials"))
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel(_("Enter your credentials"))
        font = title.font()
        font.setPointSizeF(font.pointSizeF() + 2)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        target = QLabel(_("These credentials will be used to connect to "
                          "<b>{host}</b>.").format(host=profile.host))
        target.setObjectName("hint")
        target.setWordWrap(True)
        layout.addWidget(target)

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self._username = QLineEdit(profile.username)
        self._username.setPlaceholderText(_("user  or  DOMAIN\\user"))
        form.addRow(_("User name:"), self._username)
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_("Password:"), self._password)
        layout.addLayout(form)

        self._remember = QCheckBox(_("Remember me (store the password in the "
                                     "keyring)"))
        self._remember.setChecked(profile.save_password)
        layout.addWidget(self._remember)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(_("Cancel"))
        accept = QPushButton(_("OK"))
        accept.setObjectName("primary")
        accept.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(accept)
        layout.addLayout(buttons)

        cancel.clicked.connect(self.reject)
        accept.clicked.connect(self._try_accept)
        self._password.returnPressed.connect(self._try_accept)
        self._username.returnPressed.connect(self._password.setFocus)
        (self._password if profile.username else self._username).setFocus()

    def _try_accept(self) -> None:
        if not self.username():
            self._username.setFocus()
            QMessageBox.warning(self, _("Credentials"),
                                _("The user name cannot be empty."))
            return
        self.accept()

    def username(self) -> str:
        return self._username.text().strip()

    def password(self) -> str:
        return self._password.text()

    def remember(self) -> bool:
        return self._remember.isChecked()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())

        self.profiles: list[Profile] = []
        self.current = Profile()
        self.session = Session(self)
        self._options_visible = True

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(Banner())

        # ------------------------------------------------- collapsed view
        self.compact = QWidget()
        compact_layout = QHBoxLayout(self.compact)
        compact_layout.setContentsMargins(16, 14, 16, 6)
        compact_layout.setSpacing(8)
        compact_layout.addWidget(QLabel(_("Computer:")))
        self.compact_host = QComboBox()
        self.compact_host.setEditable(True)
        self.compact_host.lineEdit().setPlaceholderText(
            _("Example: computer.fabrikam.com"))
        compact_layout.addWidget(self.compact_host, 1)
        compact_layout.addWidget(QLabel(_("User:")))
        self.compact_user = QLineEdit()
        self.compact_user.setFixedWidth(150)
        compact_layout.addWidget(self.compact_user)
        self.compact.hide()
        root.addWidget(self.compact)

        # ------------------------------------------------------- the tabs
        self.tabs = QTabWidget()
        self.general = GeneralTab()
        self.display = DisplayTab()
        self.local = LocalResourcesTab()
        self.experience = ExperienceTab()
        self.advanced = AdvancedTab()
        self.pages = (self.general, self.display, self.local,
                      self.experience, self.advanced)
        titles = (_("General"), _("Display"), _("Local Resources"),
                  _("Experience"), _("Advanced"))
        for page, title in zip(self.pages, titles):
            self.tabs.addTab(self._scrollable(page), title)

        tab_holder = QWidget()
        holder_layout = QVBoxLayout(tab_holder)
        holder_layout.setContentsMargins(12, 12, 12, 6)
        holder_layout.addWidget(self.tabs)
        self.tab_holder = tab_holder
        root.addWidget(tab_holder, 1)

        root.addWidget(separator())

        # ------------------------------------------------------ bottom bar
        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 10)
        bar_layout.setSpacing(8)

        self.btn_options = QPushButton("▲  " + _("Hide Options"))
        self.btn_options.setObjectName("link")
        bar_layout.addWidget(self.btn_options)

        self.language = QComboBox()
        self.language.setToolTip(_("Interface language"))
        for code, native in i18n.available():
            self.language.addItem(native, code)
        index = self.language.findData(i18n.language())
        self.language.setCurrentIndex(index if index >= 0 else 0)
        bar_layout.addWidget(self.language)
        bar_layout.addStretch(1)

        self.btn_command = QPushButton(_("Show Command"))
        self.btn_help = QPushButton(_("Help"))
        self.btn_connect = QPushButton(_("Connect"))
        self.btn_connect.setObjectName("primary")
        self.btn_connect.setDefault(True)
        for button in (self.btn_command, self.btn_help, self.btn_connect):
            bar_layout.addWidget(button)
        root.addWidget(bar)

        self.setStatusBar(QStatusBar())
        self._check_client()

        # -------------------------------------------------------- plumbing
        self.btn_options.clicked.connect(self.toggle_options)
        self.btn_command.clicked.connect(self.show_command)
        self.btn_help.clicked.connect(self.show_help)
        self.btn_connect.clicked.connect(self.connect_now)
        self.language.activated.connect(self.change_language)

        self.general.save_requested.connect(self.save_profile)
        self.general.save_as_requested.connect(self.export_rdp)
        self.general.open_requested.connect(self.import_rdp)
        self.general.new_requested.connect(self.new_profile)
        self.general.delete_requested.connect(self.delete_profile)
        self.general.profile_selected.connect(self.switch_profile)
        self.general.test_requested.connect(self.test_connection)

        self.session.started.connect(self._on_session_started)
        self.session.finished.connect(self._on_session_finished)

        QShortcut(QKeySequence("Ctrl+S"), self, self.save_profile)
        QShortcut(QKeySequence("Ctrl+O"), self, self.import_rdp)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.connect_now)

        self._load_profiles()
        self.setMinimumWidth(600)
        self.resize(680, 820)

    # ------------------------------------------------------------- plumbing
    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(page)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        return area

    def _check_client(self) -> None:
        if not find_client():
            self.statusBar().showMessage(
                _("FreeRDP was not found — install it, e.g. "
                  "sudo pacman -S freerdp"))
            self.btn_connect.setEnabled(False)
            return

        # FreeRDP 2 spells half of these options differently (/cert-tofu,
        # /monitor-list, ...), so it would fail in ways that look like our bug.
        version = capabilities.version()
        major = version.split(".")[0] if version else ""
        if major.isdigit() and int(major) < 3:
            self.statusBar().showMessage(
                _("Found FreeRDP {version}; this application needs FreeRDP 3."
                  ).format(version=version))
        elif version:
            self.statusBar().showMessage(
                _("FreeRDP {version} ready.").format(version=version), 6000)

    # -------------------------------------------------------------- profiles
    def _load_profiles(self) -> None:
        self.profiles, last, _language = store.load()
        if not self.profiles:
            self.profiles = [Profile()]
        index = 0
        for position, profile in enumerate(self.profiles):
            if profile.name == last:
                index = position
                break
        self.current = self.profiles[index]
        self._refresh_profile_list(index)
        self._load_into_ui(self.current)

    def _current_index(self) -> int:
        for position, profile in enumerate(self.profiles):
            if profile is self.current:
                return position
        return 0

    def _refresh_profile_list(self, index: int) -> None:
        self.general.set_profiles([p.label for p in self.profiles], index)
        hosts = sorted({p.host for p in self.profiles if p.host})
        self.general.set_history(hosts)
        typed = self.compact_host.currentText()
        self.compact_host.clear()
        self.compact_host.addItems(hosts)
        self.compact_host.setCurrentText(typed)

    def _load_into_ui(self, profile: Profile) -> None:
        for page in self.pages:
            page.load(profile)
        self.general.password.clear()
        if profile.save_password and profile.host:
            stored = keyring.lookup(profile.secret_key)
            if stored:
                self.general.password.setText(stored)
        self.compact_host.setCurrentText(profile.host)
        self.compact_user.setText(profile.username)
        self._update_hint(profile)

    def _collect(self) -> Profile:
        """Read every tab into the current profile and return it."""
        if not self._options_visible:
            # The collapsed bar is the only thing the user can edit there.
            self.general.host.setCurrentText(self.compact_host.currentText())
            self.general.username.setText(self.compact_user.text())
        for page in self.pages:
            page.save(self.current)
        # The drop-down shows host and user, so it goes stale as soon as either
        # is edited; keep the current row in step with the fields.
        row = self._current_index()
        if 0 <= row < self.general.profiles.count():
            label = self.current.label
            if self.general.profiles.itemText(row) != label:
                self.general.profiles.setItemText(row, label)
        self._update_hint(self.current)
        return self.current

    def _update_hint(self, profile: Profile) -> None:
        if not profile.host.strip():
            self.general.status.setText(
                _("The computer name field is empty. Enter the full remote "
                  "computer name."))
            return
        monitors = (_("all monitors") if not profile.monitors
                    else ", ".join(str(i + 1) for i in profile.monitors))
        mode = (_("multiple monitors") if profile.multimon
                else _("spanned screen") if profile.span
                else _("full screen") if profile.display_mode == "fullscreen"
                else f"{profile.width}x{profile.height}")
        self.general.status.setText(
            _("Target: {host}:{port} • {mode} • {monitors}").format(
                host=profile.host, port=profile.port, mode=mode,
                monitors=monitors))

    def switch_profile(self, index: int) -> None:
        if not (0 <= index < len(self.profiles)):
            return
        self._collect()
        # The password lives in a field, not in the profile; hand it to the
        # keyring before the field is overwritten by the next profile.
        self._store_password(self.current)
        self.current = self.profiles[index]
        self._load_into_ui(self.current)
        self.statusBar().showMessage(
            _("Profile loaded: {name}").format(name=self.current.name), 4000)

    def new_profile(self) -> None:
        self._collect()
        profile = Profile()
        self.profiles.append(profile)
        self.current = profile
        self._refresh_profile_list(len(self.profiles) - 1)
        self._load_into_ui(profile)

    def delete_profile(self) -> None:
        if len(self.profiles) <= 1:
            QMessageBox.information(self, _("Delete"),
                                    _("The last profile cannot be deleted."))
            return
        answer = QMessageBox.question(
            self, _("Delete profile"),
            _("Delete the profile \"{name}\"?").format(name=self.current.name))
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.current.save_password:
            keyring.clear(self.current.secret_key)
        self.profiles.pop(self._current_index())
        self.current = self.profiles[0]
        self._refresh_profile_list(0)
        self._load_into_ui(self.current)
        self._persist()

    def save_profile(self) -> None:
        profile = self._collect()
        if profile.name in ("", Profile().name):
            suggestion = profile.host or _("Connection")
            name, ok = QInputDialog.getText(self, _("Save profile"),
                                            _("Profile name:"),
                                            text=suggestion)
            if not ok or not name.strip():
                return
            profile.name = name.strip()
        self._store_password(profile)
        self._persist()
        self._refresh_profile_list(self._current_index())
        self.statusBar().showMessage(
            _("Saved: {name}").format(name=profile.name), 4000)

    def _store_password(self, profile: Profile) -> None:
        password = self.general.password.text()
        if profile.save_password and password and profile.host:
            if not keyring.store(profile.secret_key, password,
                                 f"urdp — {profile.label}"):
                QMessageBox.warning(
                    self, _("Keyring"),
                    _("The password could not be written to the keyring. Is "
                      "KDE Wallet or GNOME Keyring running?"))
        elif not profile.save_password and profile.host:
            keyring.clear(profile.secret_key)

    def _persist(self) -> None:
        try:
            store.save(self.profiles, self.current.name, i18n.language())
        except OSError as error:
            QMessageBox.warning(self, _("Could not save"), str(error))

    # ------------------------------------------------------------ .rdp files
    def export_rdp(self) -> None:
        profile = self._collect()
        suggestion = str(Path.home() / f"{profile.host or 'connection'}.rdp")
        path, _filter = QFileDialog.getSaveFileName(
            self, _("Save As"), suggestion,
            _("Remote Desktop files (*.rdp)"))
        if not path:
            return
        if not path.lower().endswith(".rdp"):
            path += ".rdp"
        try:
            rdpfile.save(profile, path)
        except OSError as error:
            QMessageBox.warning(self, _("Could not save"), str(error))
            return
        self.statusBar().showMessage(
            _("Written: {path}").format(path=path), 5000)

    def import_rdp(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Open"), str(Path.home()),
            _("Remote Desktop files (*.rdp)"))
        if not path:
            return
        try:
            profile = rdpfile.load(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, _("Could not open"), str(error))
            return
        self.profiles.append(profile)
        self.current = profile
        self._refresh_profile_list(len(self.profiles) - 1)
        self._load_into_ui(profile)
        self.statusBar().showMessage(
            _("Opened: {name}").format(name=Path(path).name), 5000)

    # --------------------------------------------------------------- actions
    def change_language(self, _index: int) -> None:
        """Apply a new language by rebuilding the window from scratch.

        Every label was translated when it was constructed, so re-running the
        constructor is both the simplest and the most complete way to switch.
        """
        code = self.language.currentData()
        if code == i18n.language():
            return
        self._collect()
        self._store_password(self.current)
        i18n.set_language(code)
        self._persist()
        QApplication.instance().exit(RESTART_CODE)

    def _probe(self, profile: Profile) -> probe.Result:
        """Run the reachability check with a wait cursor (it blocks briefly)."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return probe.check(profile.host, profile.port, timeout=3.0)
        finally:
            QApplication.restoreOverrideCursor()

    def test_connection(self) -> None:
        profile = self._collect()
        if not profile.host.strip():
            QMessageBox.warning(self, _("Test"),
                                _("Enter a computer name first."))
            return
        result = self._probe(profile)
        self.statusBar().showMessage(result.message, 12000)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information if result.reachable
                    else QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Connection test"))
        box.setText(result.message)
        if len(result.addresses) > 1:
            box.setDetailedText(_("Resolved addresses:") + "\n"
                                + "\n".join(result.addresses))
        box.exec()

    def show_command(self) -> None:
        profile = self._collect()
        CommandDialog(command_string(profile), self).exec()

    def show_help(self) -> None:
        QMessageBox.information(
            self, _("Help"),
            f"<b>{APP_NAME}</b><br><br>"
            + _("Connects to the Windows Remote Desktop service; FreeRDP does "
                "the actual work.")
            + "<br><br><b>" + _("Dual monitors:") + "</b> "
            + _("tick \"Use all my monitors for the remote session\" on the "
                "Display tab. If the server rejects the layout, turn on the "
                "force option next to it.")
            + "<br><br><b>" + _("Leave full screen:") + "</b> Ctrl+Alt+Enter"
            + "<br><b>" + _("Windows side:") + "</b> "
            + _("a Pro/Enterprise edition with Remote Desktop enabled and "
                "port 3389 reachable."))

    def connect_now(self) -> None:
        profile = self._collect()
        problems = validate(profile)
        blocking = [p.message for p in problems if p.blocking]
        if blocking:
            QMessageBox.warning(self, _("Missing information"),
                                "\n".join(blocking))
            return
        if problems:
            answer = QMessageBox.question(
                self, _("Review the settings"),
                "\n".join(p.message for p in problems) + "\n\n"
                + _("Connect anyway?"))
            if answer != QMessageBox.StandardButton.Yes:
                return

        password = self.general.password.text()
        if not password and profile.save_password:
            password = keyring.lookup(profile.secret_key) or ""
        if not password:
            dialog = CredentialDialog(profile, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            password = dialog.password()
            if dialog.username() != profile.username:
                profile.username = dialog.username()
                self.general.username.setText(profile.username)
            profile.save_password = dialog.remember()
            self.general.save_password.setChecked(profile.save_password)
            self.general.password.setText(password)
        self._store_password(profile)
        if profile.save_password:
            # Remember the choice itself, not just the secret.
            self._persist()

        error = self.session.start(profile, password or None)
        if error:
            QMessageBox.critical(self, _("Could not connect"), error)

    # -------------------------------------------------------------- session
    def _on_session_started(self) -> None:
        self.statusBar().showMessage(
            _("Connecting to {host}…").format(host=self.current.host))
        self.btn_connect.setEnabled(False)
        self.showMinimized()

    def _on_session_finished(self, ok: bool, message: str) -> None:
        self.btn_connect.setEnabled(bool(find_client()))
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.statusBar().showMessage(message, 8000)
        if ok:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Session ended"))
        box.setText(message)

        # FreeRDP says "could not connect" for every unreachable host; a probe
        # separates a wrong name from a sleeping machine from a broken route.
        if _("Could not reach the computer") in message or "reach" in message:
            box.setInformativeText(self._probe(self.current).message)

        tail = "\n".join(self.session.output.splitlines()[-25:])
        if tail:
            box.setDetailedText(tail)
        box.exec()

    # ------------------------------------------------------------ view state
    def toggle_options(self) -> None:
        self._collect()
        self._options_visible = not self._options_visible
        self.tab_holder.setVisible(self._options_visible)
        self.compact.setVisible(not self._options_visible)
        if self._options_visible:
            self.btn_options.setText("▲  " + _("Hide Options"))
            self.general.host.setCurrentText(self.compact_host.currentText())
            self.general.username.setText(self.compact_user.text())
            self.resize(self.width(), 820)
        else:
            self.btn_options.setText("▼  " + _("Show Options"))
            self.compact_host.setCurrentText(self.current.host)
            self.compact_user.setText(self.current.username)
            self.adjustSize()
            self.resize(self.width(), self.minimumSizeHint().height())

    # ---------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802  (Qt naming)
        self._collect()
        if self.session.is_running():
            answer = QMessageBox.question(
                self, _("Quit"),
                _("A session is still running. Close it?"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.session.stop()
        self._persist()
        super().closeEvent(event)
