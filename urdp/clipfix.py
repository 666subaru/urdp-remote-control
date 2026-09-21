"""Repair images that FreeRDP puts on the Linux clipboard in a broken state.

FreeRDP 3.31 cannot turn a Windows ``CF_DIB`` with ``BI_BITFIELDS`` compression
into PNG -- winpr's BMP reader stops at the 12 bytes of colour masks that sit
between the header and the pixels (``pos=54, expected 66, offset=12`` in the
log).  Windows uses exactly that layout for screenshots, so copying one from the
remote side leaves an empty ``image/png`` on the clipboard, and since Linux
applications ask for PNG first, pasting does nothing.

The same image *is* offered intact as ``image/bmp``.  This helper runs for the
lifetime of a session, notices a clipboard whose PNG is unreadable while its BMP
is fine, converts the BMP with Qt, and puts a proper image back.  It never acts
on a clipboard whose PNG already works, so it goes quiet on its own once FreeRDP
is fixed.

It runs as a separate X11 client (``QT_QPA_PLATFORM=xcb``) because xfreerdp owns
the selection on the X side, and an X client may read it without having focus --
something Wayland does not allow an unfocused window.
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QClipboard, QGuiApplication, QImage


def repaired_image(mime) -> QImage | None:
    """Return a replacement image for *mime*, or None when nothing is wrong."""
    if mime is None:
        return None
    formats = set(mime.formats())
    if "image/bmp" not in formats:
        return None
    if "image/png" in formats:
        if not QImage.fromData(mime.data("image/png"), "PNG").isNull():
            return None                  # the normal case: nothing to repair
    image = QImage.fromData(mime.data("image/bmp"), "BMP")
    return None if image.isNull() else image


def _log(message: str) -> None:
    print(f"[urdp-clipfix] {message}", file=sys.stderr, flush=True)


def main() -> int:
    parent = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    app = QGuiApplication(sys.argv[:1])
    clipboard = app.clipboard()
    busy = False

    def on_change(mode: QClipboard.Mode) -> None:
        nonlocal busy
        if mode != QClipboard.Mode.Clipboard or busy:
            return
        if clipboard.ownsClipboard():
            return                       # our own replacement: nothing to do
        busy = True
        try:
            image = repaired_image(clipboard.mimeData())
            if image is not None:
                clipboard.setImage(image)
                _log(f"repaired a {image.width()}x{image.height()} image "
                     f"whose PNG form FreeRDP could not produce")
        except Exception as error:       # noqa: BLE001 - never kill the session
            _log(f"could not repair the clipboard: {error}")
        finally:
            busy = False

    clipboard.changed.connect(on_change)

    # Belt and braces: the session stops us, but if the app itself dies the
    # helper must not linger and keep owning the clipboard.
    def check_parent() -> None:
        if parent:
            try:
                os.kill(parent, 0)
            except OSError:
                app.quit()

    watchdog = QTimer()
    watchdog.timeout.connect(check_parent)
    watchdog.start(2000)
    _log("watching the clipboard for broken images")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
