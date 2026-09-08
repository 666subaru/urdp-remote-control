"""Small reusable pieces of the interface."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter,
                         QPen, QPixmap)
from PyQt6.QtWidgets import (QFrame, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout,
                             QWidget)

from . import theme


def draw_monitor_pixmap(size: int = 48) -> QPixmap:
    """A monitor with a connection arrow, used when no theme icon is found."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    screen = QRectF(size * 0.08, size * 0.14, size * 0.84, size * 0.56)
    gradient = QLinearGradient(screen.topLeft(), screen.bottomLeft())
    gradient.setColorAt(0.0, QColor("#eaf2fb"))
    gradient.setColorAt(1.0, QColor("#b9cde4"))
    painter.setPen(QPen(QColor("#5c7ea6"), max(1.0, size * 0.03)))
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(screen, size * 0.06, size * 0.06)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#8fa8c4"))
    painter.drawRect(QRectF(size * 0.40, size * 0.70, size * 0.20, size * 0.12))
    painter.drawRoundedRect(
        QRectF(size * 0.26, size * 0.82, size * 0.48, size * 0.09),
        size * 0.04, size * 0.04)

    painter.setBrush(QColor("#4caf50"))
    painter.drawEllipse(QRectF(size * 0.02, size * 0.30, size * 0.42, size * 0.42))
    painter.setPen(QPen(QColor("#ffffff"), max(1.5, size * 0.06),
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    y = size * 0.51
    painter.drawLine(int(size * 0.11), int(y), int(size * 0.34), int(y))
    painter.drawLine(int(size * 0.26), int(y - size * 0.08),
                     int(size * 0.35), int(y))
    painter.drawLine(int(size * 0.26), int(y + size * 0.08),
                     int(size * 0.35), int(y))
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon.fromTheme("preferences-desktop-remote-desktop")
    if icon.isNull():
        icon = QIcon.fromTheme("krdc")
    if icon.isNull():
        icon = QIcon(draw_monitor_pixmap(128))
    return icon


def themed_pixmap(names: list[str], size: int = 22) -> QPixmap | None:
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            pixmap = icon.pixmap(size, size)
            if not pixmap.isNull():
                return pixmap
    return None


class Banner(QWidget):
    """The blue strip at the top, echoing the mstsc window header."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(64)
        self._pixmap = draw_monitor_pixmap(44)

    def paintEvent(self, event) -> None:  # noqa: N802  (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(theme.BANNER_TOP))
        gradient.setColorAt(1.0, QColor(theme.BANNER_BOTTOM))
        painter.fillRect(self.rect(), QBrush(gradient))
        painter.setPen(QPen(QColor(theme.BORDER)))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        painter.drawPixmap(14, (self.height() - 44) // 2, self._pixmap)

        light = QFont(self.font())
        light.setPointSizeF(self.font().pointSizeF() + 1.5)
        painter.setPen(QColor("#cfe0f2"))
        painter.setFont(light)
        painter.drawText(72, 27, "URDP")

        heavy = QFont(self.font())
        heavy.setPointSizeF(self.font().pointSizeF() + 5)
        heavy.setBold(True)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(heavy)
        painter.drawText(72, 53, "Remote Control")
        painter.end()


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def body(text: str) -> QLabel:
    """A descriptive label that wraps instead of widening the window."""
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def section(title: str, icon_names: list[str] | None = None
            ) -> tuple[QGroupBox, QVBoxLayout]:
    """A group box with an optional icon column, like mstsc's panels.

    Returns the box and the layout callers should fill.
    """
    box = QGroupBox(title)
    outer = QHBoxLayout(box)
    outer.setContentsMargins(10, 6, 10, 8)
    outer.setSpacing(12)

    pixmap = themed_pixmap(icon_names or [], 24)
    if pixmap is not None:
        icon_label = QLabel()
        icon_label.setPixmap(pixmap)
        icon_label.setFixedWidth(28)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop
                                | Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(icon_label)

    content = QVBoxLayout()
    content.setSpacing(7)
    outer.addLayout(content, 1)
    return box, content


class TabPage(QWidget):
    """Base class for the five settings tabs.

    Subclasses move data between the widgets and a :class:`Profile`; the main
    window never touches individual controls.
    """

    def load(self, profile) -> None:      # pragma: no cover - overridden
        raise NotImplementedError

    def save(self, profile) -> None:      # pragma: no cover - overridden
        raise NotImplementedError
