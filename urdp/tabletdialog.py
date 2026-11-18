"""The window that starts the tablet screen and shows its QR code."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout,
                             QLabel, QPushButton, QSpinBox, QVBoxLayout,
                             QWidget)

from . import qr, tablet, tabletaudio, tabletvideo
from .i18n import _
from .widgets import body, hint

_screen: tablet.TabletScreen | None = None


def shared() -> tablet.TabletScreen:
    """One tablet screen per process; it outlives a window rebuilt for a
    language change."""
    global _screen                                       # noqa: PLW0603
    if _screen is None:
        from PyQt6.QtWidgets import QApplication
        _screen = tablet.TabletScreen(QApplication.instance())
    return _screen


def shutdown() -> None:
    if _screen is not None:
        _screen.stop()


def qr_pixmap(text: str, size: int) -> QPixmap:
    modules = qr.encode(text)
    count = len(modules) + 8                       # 4-module quiet zone
    image = QImage(count, count, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    for y, row in enumerate(modules):
        for x, dark in enumerate(row):
            if dark:
                image.setPixel(x + 4, y + 4, 0xFF000000)
    scale = max(1, size // count)
    return QPixmap.fromImage(image.scaled(
        count * scale, count * scale, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation))


class TabletDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.screen_ = shared()
        self.setWindowTitle(_("Second Screen"))
        self.addresses = tablet.addresses()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(body(_(
            "Use a tablet or phone on the same network as an extra screen. "
            "Scan the code with its camera, or open the address in its "
            "browser.")))

        form = QFormLayout()
        self.position_box = QComboBox()
        for value, label in tablet.POSITIONS:
            self.position_box.addItem(_(label), value)
        self.size_box = QComboBox()
        for value, label in tablet.SIZES:
            self.size_box.addItem(_(label) if value in tablet.FOLLOW else label,
                              value)
        self.input_box = QComboBox()
        for value, label in tablet.INPUTS:
            self.input_box.addItem(_(label), value)
        self.picture_box = QComboBox()
        for value, label in tablet.PICTURES:
            self.picture_box.addItem(_(label), value)
        self.sound_box = QComboBox()
        for value, label in tablet.SOUNDS:
            self.sound_box.addItem(_(label), value)
        self.mic_box = QComboBox()
        for value, label in tablet.MICS:
            self.mic_box.addItem(_(label), value)
        self.port_box = QSpinBox()
        self.port_box.setRange(1024, 65535)
        form.addRow(_("Position:"), self.position_box)
        form.addRow(_("Size:"), self.size_box)
        form.addRow(_("Input:"), self.input_box)
        if tabletvideo.available():
            form.addRow(_("Picture:"), self.picture_box)
        if tabletaudio.available():
            form.addRow(_("Sound:"), self.sound_box)
        if self.screen_.mic_possible():
            form.addRow(_("Microphone:"), self.mic_box)
        form.addRow(_("Port:"), self.port_box)
        layout.addLayout(form)

        self.code_image = QLabel()
        self.code_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.code_image)
        self.address = QLabel()
        self.address.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.address.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.address.setFont(mono)
        layout.addWidget(self.address)
        self.others = hint("")
        self.others.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.others)
        self.tls_note = hint(_(
            "The first time, the tablet's browser warns that the connection "
            "is not private: this computer made its own certificate. Choose "
            "Advanced, then continue to the address."))
        self.tls_note.setVisible(False)
        layout.addWidget(self.tls_note)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        self.warning = hint("")
        layout.addWidget(self.warning)
        self.picture_note = hint("")
        self.picture_note.setTextFormat(Qt.TextFormat.PlainText)
        self.picture_note.setVisible(False)
        layout.addWidget(self.picture_note)
        layout.addWidget(hint(_("Sharing continues while URDP is open.")))

        buttons = QHBoxLayout()
        self.btn_copy = QPushButton(_("Copy to Clipboard"))
        self.btn_code = QPushButton(_("New Code"))
        self.btn_start = QPushButton()
        self.btn_start.setObjectName("primary")
        buttons.addWidget(self.btn_copy)
        buttons.addWidget(self.btn_code)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_start)
        layout.addLayout(buttons)

        self._load()
        self.position_box.currentIndexChanged.connect(self._save)
        self.size_box.currentIndexChanged.connect(self._save)
        self.input_box.currentIndexChanged.connect(self._save)
        self.picture_box.currentIndexChanged.connect(self._save)
        self.sound_box.currentIndexChanged.connect(
            lambda: self.screen_.set_sound(self.sound_box.currentData()))
        self.mic_box.currentIndexChanged.connect(
            lambda: self.screen_.set_mic(self.mic_box.currentData()))
        self.port_box.valueChanged.connect(self._save)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_code.clicked.connect(self._new_code)
        self.btn_start.clicked.connect(self._toggle)
        self.screen_.state_changed.connect(self._show_state)
        self.screen_.notice.connect(self._show_notice)
        self._show_state(self.screen_.state, self.screen_.detail)
        self.setMinimumWidth(440)

    # ------------------------------------------------------------- data
    def _load(self) -> None:
        s = self.screen_.settings
        self.position_box.setCurrentIndex(max(0, self.position_box.findData(s.position)))
        self.size_box.setCurrentIndex(max(0, self.size_box.findData(s.size)))
        self.input_box.setCurrentIndex(max(0, self.input_box.findData(s.input)))
        self.sound_box.setCurrentIndex(max(0, self.sound_box.findData(s.sound)))
        self.mic_box.setCurrentIndex(max(0, self.mic_box.findData(s.mic)))
        self.picture_box.setCurrentIndex(
            max(0, self.picture_box.findData(s.picture)))
        self.port_box.setValue(s.port)
        self._show_address()

    def _save(self) -> None:
        s = self.screen_.settings
        s.position = self.position_box.currentData()
        s.size = self.size_box.currentData()
        s.input = self.input_box.currentData()
        s.picture = self.picture_box.currentData()
        s.port = self.port_box.value()
        tablet.save_settings(s)
        self._show_address()

    def _url(self) -> str:
        host = self.addresses[0].address if self.addresses else "127.0.0.1"
        return self.screen_.url(host)

    def _show_address(self) -> None:
        url = self._url()
        self.code_image.setPixmap(qr_pixmap(url, 240))
        # The code is shown apart from the address: the page asks for it when
        # the address is typed by hand.
        self.address.setText(url.split("#", 1)[0] + "\n"
                             + self.screen_.settings.code)
        self.others.setText("  ".join(
            f"{a.address} ({a.interface})" for a in self.addresses[1:]))
        self.others.setVisible(self.screen_.running and len(self.addresses) > 1)
        trouble = ""
        if self.addresses:
            via = tablet.misrouted(self.addresses[0], self.port_box.value())
            if via:
                trouble = _(
                    "Replies to this network leave through {interface}, so "
                    "the tablet may not reach this computer. If it cannot "
                    "connect, this is why.").format(interface=via)
        self.warning.setText(trouble)
        self.warning.setVisible(bool(trouble))

    # ---------------------------------------------------------- actions
    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._url())

    def _new_code(self) -> None:
        self.screen_.new_code()
        self._show_address()

    def _toggle(self) -> None:
        if self.screen_.running:
            self.screen_.stop()
        else:
            self._save()
            self.screen_.start()

    def _show_notice(self, text: str) -> None:
        self.picture_note.setText(text)
        self.picture_note.setVisible(bool(text))
        self.adjustSize()

    def _show_state(self, state: str, detail: str) -> None:
        running = self.screen_.running
        self.btn_start.setText(_("Stop") if running else _("Start"))
        for widget in (self.position_box, self.size_box, self.picture_box,
                       self.port_box):
            widget.setEnabled(not running)
        # The code is only worth scanning while something answers it.
        for widget in (self.code_image, self.address, self.btn_copy):
            widget.setVisible(running)
        self.tls_note.setVisible(running and self._url().startswith("https:"))
        self.others.setVisible(running and len(self.addresses) > 1)
        self.adjustSize()
        texts = {"stopped": _("Stopped"),
                 "starting": _("Starting…"),
                 "waiting": _("Waiting for the tablet…"),
                 "connected": _("Connected: {address}").format(address=detail)}
        self.status.setText(detail if state == "error"
                            else texts.get(state, ""))
        self.status.setStyleSheet("color: #d93025;" if state == "error" else "")
