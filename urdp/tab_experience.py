"""The "Experience" tab: bandwidth preset and the visual effects it implies."""

from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout

from . import capabilities
from .i18n import _
from .profile import Profile
from .widgets import TabPage, body, hint, section

CONNECTIONS = [
    ("auto", "Detect connection quality automatically"),
    ("modem", "Modem (56 kbps)"),
    ("broadband-low", "Low-speed broadband (256 kbps – 2 Mbps)"),
    ("satellite", "Satellite (2 Mbps – 16 Mbps, high latency)"),
    ("broadband-high", "High-speed broadband (2 Mbps – 10 Mbps)"),
    ("wan", "WAN (10 Mbps and higher, high latency)"),
    ("lan", "LAN (10 Mbps and higher)"),
]

GFX_CODECS = [("auto", "Automatic (whatever the server supports)"),
              ("AVC444", "H.264 AVC444 (smoothest, needs a capable server)"),
              ("AVC420", "H.264 AVC420"),
              ("RFX", "RemoteFX"),
              ("progressive", "Progressive"),
              ("off", "Off (legacy RDP graphics)")]


class ExperienceTab(TabPage):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        box, content = section(_("Performance"),
                               ["speedometer",
                                "preferences-system-performance",
                                "network-wired"])
        content.addWidget(body(
            _("Choose your connection speed to optimize performance.")))
        self.connection = QComboBox()
        for value, label in CONNECTIONS:
            self.connection.addItem(_(label), value)
        content.addWidget(self.connection)

        content.addWidget(body(_("Features depending on connection quality:")))
        self.wallpaper = QCheckBox(_("Desktop background"))
        self.font_smoothing = QCheckBox(_("Font smoothing"))
        self.desktop_composition = QCheckBox(_("Desktop composition"))
        self.window_drag = QCheckBox(_("Show window contents while dragging"))
        self.menu_anims = QCheckBox(_("Menu and window animation"))
        self.themes = QCheckBox(_("Visual styles"))
        for widget in (self.wallpaper, self.font_smoothing,
                       self.desktop_composition, self.window_drag,
                       self.menu_anims, self.themes):
            content.addWidget(widget)
        root.addWidget(box)

        box2, content2 = section(_("Graphics and cache"),
                                 ["video-x-generic", "applications-graphics"])
        codec_row = QHBoxLayout()
        codec_row.addWidget(QLabel(_("Graphics pipeline:")))
        self.gfx_codec = QComboBox()
        for value, label in GFX_CODECS:
            self.gfx_codec.addItem(_(label), value)
        codec_row.addWidget(self.gfx_codec, 1)
        content2.addLayout(codec_row)
        content2.addWidget(hint(
            _("AVC444 gives the smoothest picture, but it can load the CPU "
              "when the server has no hardware encoder.")))
        self.codec_support = hint(capabilities.summary())
        content2.addWidget(self.codec_support)
        self._disable_missing_codecs()

        self.persistent_bitmap_cache = QCheckBox(_("Persistent bitmap caching"))
        self.compression = QCheckBox(_("Data compression"))
        self.auto_reconnect = QCheckBox(_("Reconnect if the connection is "
                                          "dropped"))
        for widget in (self.persistent_bitmap_cache, self.compression,
                       self.auto_reconnect):
            content2.addWidget(widget)
        root.addWidget(box2)
        root.addStretch(1)

        self.connection.activated.connect(self._apply_preset)

    def _disable_missing_codecs(self) -> None:
        """Grey out codecs this FreeRDP build was not compiled with.

        The ``/gfx`` parser ignores values it does not know instead of failing,
        so an unsupported choice would silently fall back to something else.
        """
        if capabilities.has_h264():
            return
        model = self.gfx_codec.model()
        for row in range(self.gfx_codec.count()):
            if self.gfx_codec.itemData(row) in ("AVC444", "AVC420"):
                model.item(row).setEnabled(False)
                self.gfx_codec.setItemText(
                    row, self.gfx_codec.itemText(row)
                    + _("  — not in this build"))

    def _apply_preset(self, _index: int) -> None:
        """Mirror mstsc: picking a speed re-ticks the six effect boxes."""
        probe = Profile()
        probe.apply_experience_preset(self.connection.currentData())
        self.wallpaper.setChecked(probe.wallpaper)
        self.font_smoothing.setChecked(probe.font_smoothing)
        self.desktop_composition.setChecked(probe.desktop_composition)
        self.window_drag.setChecked(probe.window_drag)
        self.menu_anims.setChecked(probe.menu_anims)
        self.themes.setChecked(probe.themes)

    # ------------------------------------------------------------- TabPage
    def load(self, p: Profile) -> None:
        index = self.connection.findData(p.network)
        self.connection.setCurrentIndex(index if index >= 0 else 0)
        self.wallpaper.setChecked(p.wallpaper)
        self.font_smoothing.setChecked(p.font_smoothing)
        self.desktop_composition.setChecked(p.desktop_composition)
        self.window_drag.setChecked(p.window_drag)
        self.menu_anims.setChecked(p.menu_anims)
        self.themes.setChecked(p.themes)
        self.persistent_bitmap_cache.setChecked(p.persistent_bitmap_cache)
        self.compression.setChecked(p.compression)
        self.auto_reconnect.setChecked(p.auto_reconnect)
        index = self.gfx_codec.findData(p.gfx_codec)
        if index >= 0 and not self.gfx_codec.model().item(index).isEnabled():
            index = 0
        self.gfx_codec.setCurrentIndex(index if index >= 0 else 0)

    def save(self, p: Profile) -> None:
        p.network = self.connection.currentData()
        p.wallpaper = self.wallpaper.isChecked()
        p.font_smoothing = self.font_smoothing.isChecked()
        p.desktop_composition = self.desktop_composition.isChecked()
        p.window_drag = self.window_drag.isChecked()
        p.menu_anims = self.menu_anims.isChecked()
        p.themes = self.themes.isChecked()
        p.persistent_bitmap_cache = self.persistent_bitmap_cache.isChecked()
        p.compression = self.compression.isChecked()
        p.auto_reconnect = self.auto_reconnect.isChecked()
        p.gfx_codec = self.gfx_codec.currentData()
