"""The "Display" tab: size, monitors, colour depth, scaling."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem, QPushButton,
                             QSlider, QVBoxLayout)

from .i18n import _
from .monitors import list_monitors
from .profile import Profile
from .widgets import TabPage, body, hint, section

#: Slider stops.  The last position means "use the whole screen".
RESOLUTIONS: list[tuple[int, int]] = [
    (800, 600), (1024, 768), (1280, 720), (1280, 800), (1366, 768),
    (1440, 900), (1600, 900), (1680, 1050), (1920, 1080), (1920, 1200),
    (2560, 1440), (2560, 1600), (3440, 1440), (3840, 2160),
]
FULLSCREEN_POS = len(RESOLUTIONS)

COLOR_DEPTHS = [(32, "Highest Quality (32 bit)"),
                (24, "High Quality (24 bit)"),
                (16, "High Color (16 bit)"),
                (15, "High Color (15 bit)"),
                (8, "256 Colors (8 bit)")]


class DisplayTab(TabPage):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # ---------------------------------------------- display configuration
        box, content = section(_("Display configuration"),
                               ["video-display", "preferences-desktop-display"])
        content.addWidget(body(
            _("Choose the size of your remote desktop. Drag the slider all the "
              "way to the right to go full screen.")))

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, FULLSCREEN_POS)
        self.slider.setValue(FULLSCREEN_POS)
        self.slider.setPageStep(1)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)

        scale_row = QHBoxLayout()
        scale_row.addWidget(hint(_("Small")))
        scale_row.addWidget(self.slider, 1)
        scale_row.addWidget(hint(_("Large")))
        content.addLayout(scale_row)

        self.size_label = QLabel(_("Full Screen"))
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        content.addWidget(self.size_label)

        self.multimon = QCheckBox(_("Use all my monitors for the remote session"))
        self.span = QCheckBox(_("Span the desktop across monitors"))
        self.span.setToolTip(
            _("Windows sees one huge monitor. With multiple monitors each "
              "screen appears as its own monitor, which is usually what you "
              "want."))
        self.multimon_force = QCheckBox(
            _("Force the layout if the server rejects it (multimon:force)"))
        self.multimon_force.setToolTip(
            _("Turn this on when monitors of different heights make the server "
              "fall back to a single screen."))
        content.addWidget(self.multimon)
        content.addWidget(self.span)
        content.addWidget(self.multimon_force)

        monitor_header = QHBoxLayout()
        monitor_header.addWidget(QLabel(_("Monitors to use:")))
        monitor_header.addStretch(1)
        self.btn_refresh = QPushButton(_("Refresh"))
        self.btn_refresh.setObjectName("compact")
        monitor_header.addWidget(self.btn_refresh)
        content.addLayout(monitor_header)

        self.monitor_list = QListWidget()
        self.monitor_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.monitor_list.setMaximumHeight(96)
        content.addWidget(self.monitor_list)
        content.addWidget(hint(
            _("With nothing ticked every monitor is used. Sizes are the "
              "physical pixels FreeRDP sees.")))
        root.addWidget(box)

        # ------------------------------------------------------------- colors
        box2, content2 = section(_("Colors"), ["preferences-desktop-color",
                                               "color-management"])
        content2.addWidget(body(_("Choose the color depth of the remote session.")))
        self.color_depth = QComboBox()
        for value, label in COLOR_DEPTHS:
            self.color_depth.addItem(_(label), value)
        content2.addWidget(self.color_depth)
        root.addWidget(box2)

        # ---------------------------------------------------- scaling, window
        box3, content3 = section(_("Scaling and window"),
                                 ["zoom-fit-best", "transform-scale"])
        scale_line = QHBoxLayout()
        scale_line.addWidget(QLabel(_("Remote desktop scale:")))
        self.scale = QComboBox()
        for value in (100, 140, 180):
            self.scale.addItem(f"{value}%", value)
        scale_line.addWidget(self.scale)
        scale_line.addStretch(1)
        content3.addLayout(scale_line)
        content3.addWidget(hint(
            _("On HiDPI screens, pick 140% or 180% when text on the Windows "
              "side comes out too small.")))

        self.floatbar = QCheckBox(
            _("Display the connection bar when I use the full screen"))
        self.dynamic_resolution = QCheckBox(
            _("Update the resolution when the window is resized"))
        self.dynamic_resolution.setToolTip(
            _("Only works for a single monitor in windowed mode."))
        self.smart_sizing = QCheckBox(
            _("Fit the remote desktop to the window (smart sizing)"))
        self.decorations = QCheckBox(_("Show the window frame"))
        for widget in (self.floatbar, self.dynamic_resolution,
                       self.smart_sizing, self.decorations):
            content3.addWidget(widget)
        root.addWidget(box3)
        root.addStretch(1)

        self.slider.valueChanged.connect(self._update_size_label)
        self.multimon.toggled.connect(self._update_enabled)
        self.span.toggled.connect(self._update_enabled)
        self.btn_refresh.clicked.connect(self.refresh_monitors)

        self.refresh_monitors()
        self._update_enabled()

    # --------------------------------------------------------------- helpers
    def refresh_monitors(self) -> None:
        checked = set(self.selected_monitors())
        self.monitor_list.clear()
        found = list_monitors()
        if not found:
            item = QListWidgetItem(
                _("Could not read the monitor list (FreeRDP did not run)."))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.monitor_list.addItem(item)
            return
        for monitor in found:
            item = QListWidgetItem(monitor.label)
            item.setData(Qt.ItemDataRole.UserRole, monitor.index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if monitor.index in checked
                               else Qt.CheckState.Unchecked)
            self.monitor_list.addItem(item)

    def selected_monitors(self) -> list[int]:
        result: list[int] = []
        for row in range(self.monitor_list.count()):
            item = self.monitor_list.item(row)
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is not None and item.checkState() == Qt.CheckState.Checked:
                result.append(int(index))
        return result

    def _check_monitors(self, indexes: list[int]) -> None:
        wanted = set(indexes)
        for row in range(self.monitor_list.count()):
            item = self.monitor_list.item(row)
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is None:
                continue
            item.setCheckState(Qt.CheckState.Checked if int(index) in wanted
                               else Qt.CheckState.Unchecked)

    def _update_size_label(self, value: int) -> None:
        if value >= FULLSCREEN_POS:
            self.size_label.setText(_("Full Screen"))
        else:
            width, height = RESOLUTIONS[value]
            self.size_label.setText(_("{w} x {h} pixels").format(w=width,
                                                                 h=height))

    def _update_enabled(self) -> None:
        multi = self.multimon.isChecked()
        if multi and self.span.isChecked():
            self.span.setChecked(False)
        spanning = self.span.isChecked()
        self.multimon_force.setEnabled(multi)
        self.slider.setEnabled(not (multi or spanning))
        self.size_label.setEnabled(not (multi or spanning))
        self.dynamic_resolution.setEnabled(not (multi or spanning))
        self.smart_sizing.setEnabled(not (multi or spanning))

    # ------------------------------------------------------------- TabPage
    def load(self, p: Profile) -> None:
        if p.display_mode == "fullscreen":
            self.slider.setValue(FULLSCREEN_POS)
        else:
            try:
                self.slider.setValue(RESOLUTIONS.index((p.width, p.height)))
            except ValueError:
                self.slider.setValue(FULLSCREEN_POS - 1)
        self._update_size_label(self.slider.value())

        self.multimon.setChecked(p.multimon)
        self.span.setChecked(p.span)
        self.multimon_force.setChecked(p.multimon_force)
        self._check_monitors(p.monitors)

        index = self.color_depth.findData(p.color_depth)
        self.color_depth.setCurrentIndex(index if index >= 0 else 0)
        index = self.scale.findData(p.scale)
        self.scale.setCurrentIndex(index if index >= 0 else 0)

        self.floatbar.setChecked(p.floatbar)
        self.dynamic_resolution.setChecked(p.dynamic_resolution)
        self.smart_sizing.setChecked(p.smart_sizing)
        self.decorations.setChecked(p.decorations)
        self._update_enabled()

    def save(self, p: Profile) -> None:
        value = self.slider.value()
        if value >= FULLSCREEN_POS:
            p.display_mode = "fullscreen"
        else:
            p.display_mode = "custom"
            p.width, p.height = RESOLUTIONS[value]

        p.multimon = self.multimon.isChecked()
        p.span = self.span.isChecked()
        p.multimon_force = self.multimon_force.isChecked()
        p.monitors = self.selected_monitors()
        p.color_depth = self.color_depth.currentData()
        p.scale = self.scale.currentData()
        p.floatbar = self.floatbar.isChecked()
        p.dynamic_resolution = self.dynamic_resolution.isChecked()
        p.smart_sizing = self.smart_sizing.isChecked()
        p.decorations = self.decorations.isChecked()
