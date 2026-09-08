"""Dark colour scheme.

We force the Fusion style and paint our own palette instead of following the
desktop theme: the layout imitates ``mstsc.exe`` closely, and a light GTK or
Breeze palette leaking in makes the group boxes and the header banner clash.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QPainter, QPainterPath, QPalette,
                         QPen, QPixmap)
from PyQt6.QtWidgets import QApplication

WINDOW = "#1e1f22"
BASE = "#141518"
ALT_BASE = "#26282c"
SURFACE = "#2b2d31"
BORDER = "#3a3d42"
TEXT = "#e6e6e6"
DIM_TEXT = "#9aa0a6"
ACCENT = "#3d8bfd"
ACCENT_DARK = "#2f6fd0"
BANNER_TOP = "#1c3f66"
BANNER_BOTTOM = "#132a45"

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-size: 10pt;
}}
QMainWindow, QDialog {{
    background: {WINDOW};
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    background: {SURFACE};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {DIM_TEXT};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    top: -1px;
    background: {WINDOW};
}}
QTabBar::tab {{
    background: {ALT_BASE};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 14px;
    margin-right: 2px;
    color: {DIM_TEXT};
}}
QTabBar::tab:selected {{
    background: {WINDOW};
    color: {TEXT};
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT};
}}
QComboBox {{ padding-right: 22px; }}
QSpinBox {{ padding-right: 20px; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit, QListWidget, QTableWidget {{
    background: {BASE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {DIM_TEXT};
    background: {ALT_BASE};
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
}}
QPushButton {{
    background: {ALT_BASE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 16px;
    min-width: 74px;
}}
QPushButton:hover {{ background: #32353a; }}
QPushButton:pressed {{ background: #202226; }}
QPushButton:disabled {{ color: {DIM_TEXT}; }}
QPushButton#primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT_DARK};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: #4b96ff; }}
QPushButton#primary:pressed {{ background: {ACCENT_DARK}; }}
QPushButton#compact {{ min-width: 0; padding: 6px 10px; }}
QPushButton#link {{
    background: transparent;
    border: none;
    color: {ACCENT};
    padding: 4px 2px;
    min-width: 0;
    text-align: left;
}}
QPushButton#link:hover {{ color: #6aa9ff; text-decoration: underline; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked {{
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BASE};
}}
QCheckBox::indicator:checked {{
    border: none;
    background: transparent;
    image: url("__CHECK__");
}}
QCheckBox::indicator:unchecked:disabled {{
    border-color: #2f3237; background: {ALT_BASE};
}}
QCheckBox::indicator:checked:disabled {{
    border: none;
    background: transparent;
    image: url("__CHECK_OFF__");
}}
QRadioButton::indicator:unchecked {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: {BASE};
}}
QRadioButton::indicator:checked {{
    border: 4px solid {ACCENT};
    border-radius: 8px;
    background: {BASE};
}}
QRadioButton::indicator:checked:disabled {{
    border: 4px solid #33517f;
}}
QSlider::groove:horizontal {{
    height: 4px; background: {BORDER}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: #dfe3e8; width: 12px; margin: -5px 0; border-radius: 6px;
}}
QLabel#hint {{ color: {DIM_TEXT}; }}
QLabel#warning {{ color: #e8b339; }}
QLabel#error {{ color: #ef6b6b; }}
QFrame#separator {{ background: {BORDER}; max-height: 1px; border: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: #45484e; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: #5a5e66; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QToolTip {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {BORDER}; padding: 4px;
}}
"""


def _draw_check(size: int, box: str, tick: str) -> QPixmap:
    """A rounded box with a check mark, drawn at *size* pixels."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    inset = size * 0.04
    painter.setPen(QPen(QColor(box), max(1.0, size * 0.06)))
    painter.setBrush(QBrush(QColor(box)))
    painter.drawRoundedRect(
        QRectF(inset, inset, size - 2 * inset, size - 2 * inset),
        size * 0.19, size * 0.19)

    path = QPainterPath(QPointF(size * 0.26, size * 0.53))
    path.lineTo(QPointF(size * 0.43, size * 0.70))
    path.lineTo(QPointF(size * 0.75, size * 0.31))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(tick), max(1.4, size * 0.13),
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(path)
    painter.end()
    return pixmap


def _indicator_files() -> dict[str, str]:
    """Write the check-mark images and return their paths.

    Qt picks the ``@2x`` variant on HiDPI screens on its own, so both sizes are
    written next to each other.  They are regenerated on every start, which
    keeps them in step with the colours above.
    """
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    folder = base / "urdp"
    folder.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    for key, (box, tick) in {"CHECK": (ACCENT, "#ffffff"),
                             "CHECK_OFF": ("#33517f", "#9fb4d0")}.items():
        name = "check" if key == "CHECK" else "check-disabled"
        for scale, suffix in ((16, ""), (32, "@2x")):
            _draw_check(scale, box, tick).save(
                str(folder / f"{name}{suffix}.png"), "PNG")
        files[key] = (folder / f"{name}.png").as_posix()
    return files


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(WINDOW))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(BASE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(ALT_BASE))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(ALT_BASE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(DIM_TEXT))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.Text, QColor(DIM_TEXT))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.ButtonText, QColor(DIM_TEXT))
    app.setPalette(palette)
    sheet = STYLESHEET
    for name, path in _indicator_files().items():
        sheet = sheet.replace(f"__{name}__", path)
    app.setStyleSheet(sheet)
