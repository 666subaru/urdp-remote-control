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


class Repairer:
    """Watch *clipboard* and repair it once the dust settles.

    Windows announces a screenshot several times within half a second (the
    image first, then again with its clipboard-history formats), and each
    announcement makes xfreerdp take the selection back.  Reacting to every
    change therefore repairs the first announcement only to lose it to the
    next.  Instead changes restart a short timer, the check runs once they
    stop, and after a repair a second look confirms the selection is still
    ours -- repairing again if xfreerdp took it back, a bounded number of
    times so two owners can never fight forever.
    """

    SETTLE_MS = 500
    VERIFY_MS = 1500
    MAX_REPAIRS = 4          # per burst of activity
    BURST_S = 10.0

    def __init__(self, clipboard, log=_log, now=None) -> None:
        import time
        self.clipboard = clipboard
        self.log = log
        self.now = now or time.monotonic
        self.running = False
        self.pending = False
        self.repairs: list[float] = []
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(self.SETTLE_MS)
        self.timer.timeout.connect(self.check)
        self.verify = QTimer()
        self.verify.setSingleShot(True)
        self.verify.setInterval(self.VERIFY_MS)
        self.verify.timeout.connect(self._verify)
        clipboard.changed.connect(self.on_change)

    def on_change(self, mode) -> None:
        if mode != QClipboard.Mode.Clipboard:
            return
        if self.clipboard.ownsClipboard():
            return                       # our own replacement
        self.timer.start()               # restart: wait for the burst to end

    def _verify(self) -> None:
        if not self.clipboard.ownsClipboard():
            self.timer.start()

    def check(self) -> None:
        # Reading another client's selection spins the event loop, so the
        # timer can fire again while we are still in here.
        if self.running:
            self.pending = True
            return
        self.running = True
        try:
            if self.clipboard.ownsClipboard():
                return
            image = repaired_image(self.clipboard.mimeData())
            if image is None:
                return
            now = self.now()
            self.repairs = [t for t in self.repairs if now - t < self.BURST_S]
            if len(self.repairs) >= self.MAX_REPAIRS:
                self.log("giving up for now: the clipboard keeps being taken "
                         "back")
                return
            self.repairs.append(now)
            self.clipboard.setImage(image)
            self.log(f"repaired a {image.width()}x{image.height()} image "
                     f"whose PNG form FreeRDP could not produce")
            self.verify.start()
        except Exception as error:       # noqa: BLE001 - never kill the session
            self.log(f"could not repair the clipboard: {error}")
        finally:
            self.running = False
            if self.pending:
                self.pending = False
                self.timer.start()


def main() -> int:
    parent = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    app = QGuiApplication(sys.argv[:1])
    repairer = Repairer(app.clipboard())

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
    code = app.exec()
    del repairer
    return code


if __name__ == "__main__":
    raise SystemExit(main())
