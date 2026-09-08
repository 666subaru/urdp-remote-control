"""The "Local Resources" tab: audio, keyboard and device redirection."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget,
                             QPushButton, QVBoxLayout)

from .i18n import _
from .profile import Profile
from .widgets import TabPage, body, hint, section

AUDIO_MODES = [("local", "Play on this computer"),
               ("remote", "Play on remote computer"),
               ("none", "Do not play")]

AUDIO_BACKENDS = [("auto", "Automatic"), ("pulse", "PulseAudio / PipeWire"),
                  ("alsa", "ALSA"), ("sdl", "SDL"), ("oss", "OSS")]

KEYBOARD_MODES = [("local", "On this computer"),
                  ("remote", "On the remote computer"),
                  ("fullscreen", "Only when using the full screen")]

# A short list is friendlier than FreeRDP's several hundred layout ids.  The
# ids are the full eight-digit form reported by "xfreerdp3 /list:kbd"; the
# short four-digit form is rejected for variants such as Turkish F.
LAYOUTS = [("", "Automatic (use the local layout)"),
           ("0x0000041F", "Turkish Q"),
           ("0x0001041F", "Turkish F"),
           ("0x00000409", "English (United States)"),
           ("0x00000809", "English (United Kingdom)"),
           ("0x00000407", "German"),
           ("0x0000040C", "French")]

#: How a redirected folder is shown in the list.
_DRIVE_SEPARATOR = "  →  "


class LocalResourcesTab(TabPage):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # -------------------------------------------------------- remote audio
        box, content = section(_("Remote audio"), ["audio-volume-high",
                                                   "audio-card"])
        row = QHBoxLayout()
        row.addWidget(QLabel(_("Remote audio playback:")))
        self.audio_mode = QComboBox()
        for value, label in AUDIO_MODES:
            self.audio_mode.addItem(_(label), value)
        row.addWidget(self.audio_mode, 1)
        content.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(_("Audio backend:")))
        self.audio_backend = QComboBox()
        for value, label in AUDIO_BACKENDS:
            self.audio_backend.addItem(_(label), value)
        row2.addWidget(self.audio_backend, 1)
        content.addLayout(row2)

        self.microphone = QCheckBox(_("Remote audio recording (redirect the "
                                      "microphone)"))
        content.addWidget(self.microphone)
        root.addWidget(box)

        # ------------------------------------------------------------ keyboard
        box2, content2 = section(_("Keyboard"), ["input-keyboard",
                                                 "preferences-desktop-keyboard"])
        row3 = QHBoxLayout()
        row3.addWidget(QLabel(_("Apply Windows key combinations:")))
        self.keyboard_mode = QComboBox()
        for value, label in KEYBOARD_MODES:
            self.keyboard_mode.addItem(_(label), value)
        row3.addWidget(self.keyboard_mode, 1)
        content2.addLayout(row3)
        content2.addWidget(hint(_("Example: ALT+TAB")))

        row4 = QHBoxLayout()
        row4.addWidget(QLabel(_("Keyboard layout:")))
        self.kbd_layout = QComboBox()
        for value, label in LAYOUTS:
            self.kbd_layout.addItem(_(label), value)
        row4.addWidget(self.kbd_layout, 1)
        content2.addLayout(row4)
        root.addWidget(box2)

        # -------------------------------------------- local devices, resources
        box3, content3 = section(_("Local devices and resources"),
                                 ["drive-harddisk", "computer"])
        content3.addWidget(body(
            _("Choose the devices and resources you want to use in the remote "
              "session.")))

        grid = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()
        self.printers = QCheckBox(_("Printers"))
        self.clipboard = QCheckBox(_("Clipboard"))
        self.smartcard = QCheckBox(_("Smart cards"))
        self.serial = QCheckBox(_("Serial ports"))
        self.parallel = QCheckBox(_("Parallel ports"))
        self.home_drive = QCheckBox(_("Home folder"))
        for widget in (self.printers, self.clipboard, self.smartcard):
            left.addWidget(widget)
        for widget in (self.serial, self.parallel, self.home_drive):
            right.addWidget(widget)
        grid.addLayout(left, 1)
        grid.addLayout(right, 1)
        content3.addLayout(grid)

        usb_row = QHBoxLayout()
        self.usb = QCheckBox(_("USB devices"))
        self.usb_filter = QLineEdit()
        self.usb_filter.setPlaceholderText(_("auto  or  id:04f2:b2da"))
        usb_row.addWidget(self.usb)
        usb_row.addWidget(self.usb_filter, 1)
        content3.addLayout(usb_row)

        content3.addWidget(QLabel(_("Folders to redirect:")))
        self.drives = QListWidget()
        self.drives.setMaximumHeight(88)
        content3.addWidget(self.drives)

        drive_buttons = QHBoxLayout()
        self.btn_add_drive = QPushButton(_("Add Folder…"))
        self.btn_remove_drive = QPushButton(_("Remove"))
        for button in (self.btn_add_drive, self.btn_remove_drive):
            button.setObjectName("compact")
            drive_buttons.addWidget(button)
        drive_buttons.addStretch(1)
        content3.addLayout(drive_buttons)
        root.addWidget(box3)
        root.addStretch(1)

        self.usb.toggled.connect(self.usb_filter.setEnabled)
        self.usb_filter.setEnabled(False)
        self.btn_add_drive.clicked.connect(self._add_drive)
        self.btn_remove_drive.clicked.connect(self._remove_drive)

    # --------------------------------------------------------------- helpers
    def _add_drive(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, _("Choose the folder to redirect"), str(Path.home()))
        if not path:
            return
        suggestion = Path(path).name or "share"
        name, ok = QInputDialog.getText(
            self, _("Share name"),
            _("Name shown on the Windows side:"), text=suggestion)
        if not ok or not name.strip():
            return
        self.drives.addItem(f"{name.strip()}{_DRIVE_SEPARATOR}{path}")

    def _remove_drive(self) -> None:
        for item in self.drives.selectedItems():
            self.drives.takeItem(self.drives.row(item))

    def _drive_entries(self) -> list[list[str]]:
        entries: list[list[str]] = []
        for row in range(self.drives.count()):
            text = self.drives.item(row).text()
            name, _sep, path = text.partition(_DRIVE_SEPARATOR)
            if name and path:
                entries.append([name, path])
        return entries

    # ------------------------------------------------------------- TabPage
    def load(self, p: Profile) -> None:
        def select(combo: QComboBox, value) -> None:
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)

        select(self.audio_mode, p.audio_mode)
        select(self.audio_backend, p.audio_backend)
        select(self.keyboard_mode, p.keyboard_mode)
        select(self.kbd_layout, p.kbd_layout)

        self.microphone.setChecked(p.microphone)
        self.printers.setChecked(p.printers)
        self.clipboard.setChecked(p.clipboard)
        self.smartcard.setChecked(p.smartcard)
        self.serial.setChecked(p.serial)
        self.parallel.setChecked(p.parallel)
        self.home_drive.setChecked(p.home_drive)
        self.usb.setChecked(p.usb)
        self.usb_filter.setText(p.usb_filter)
        self.usb_filter.setEnabled(p.usb)

        self.drives.clear()
        for entry in p.drives:
            if len(entry) == 2:
                self.drives.addItem(f"{entry[0]}{_DRIVE_SEPARATOR}{entry[1]}")

    def save(self, p: Profile) -> None:
        p.audio_mode = self.audio_mode.currentData()
        p.audio_backend = self.audio_backend.currentData()
        p.microphone = self.microphone.isChecked()
        p.keyboard_mode = self.keyboard_mode.currentData()
        p.kbd_layout = self.kbd_layout.currentData()
        p.printers = self.printers.isChecked()
        p.clipboard = self.clipboard.isChecked()
        p.smartcard = self.smartcard.isChecked()
        p.serial = self.serial.isChecked()
        p.parallel = self.parallel.isChecked()
        p.home_drive = self.home_drive.isChecked()
        p.usb = self.usb.isChecked()
        p.usb_filter = self.usb_filter.text().strip() or "auto"
        p.drives = self._drive_entries()
