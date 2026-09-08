"""Entry point: ``python -m urdp``."""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from . import i18n, store, theme
from .mainwindow import RESTART_CODE, MainWindow
from .widgets import app_icon


def _initial_language() -> str:
    """The saved choice, otherwise whatever the environment suggests."""
    try:
        _profiles, _last, saved = store.load()
    except Exception:                                   # noqa: BLE001
        saved = None
    return saved or i18n.detect()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("urdp")
    app.setApplicationDisplayName("URDP Remote Control")
    app.setDesktopFileName("urdp")
    theme.apply(app)
    app.setWindowIcon(app_icon())

    i18n.set_language(_initial_language())

    # Changing the language rebuilds the window: every label is translated when
    # it is constructed, so re-running the constructor is the complete fix.
    while True:
        # Arabic and Hebrew mirror the whole interface, not just the text.
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl()
                               else Qt.LayoutDirection.LeftToRight)
        window = MainWindow()
        window.show()
        code = app.exec()
        if code != RESTART_CODE:
            return code
        window.deleteLater()


if __name__ == "__main__":
    raise SystemExit(main())
