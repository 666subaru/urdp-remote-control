"""Enumerate the monitors FreeRDP will see.

FreeRDP reports the X11/XWayland screen geometry, which is *not* the same as the
logical layout the compositor shows in system settings: on a HiDPI setup KDE may
report 1920x1080 where FreeRDP sees 2880x1620.  Asking the client itself is the
only way to get ids that ``/monitors:`` will accept.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from .i18n import _
from .rdpcmd import find_client

# Example line:  "      * [0] 2880x1620\t+0+0"
_LINE = re.compile(
    r"^\s*(?P<primary>\*)?\s*\[(?P<index>\d+)\]\s+"
    r"(?P<width>\d+)x(?P<height>\d+)\s+"
    r"\+(?P<x>-?\d+)\+(?P<y>-?\d+)"
)


@dataclass(frozen=True)
class Monitor:
    index: int
    width: int
    height: int
    x: int
    y: int
    primary: bool

    @property
    def label(self) -> str:
        tag = _("  (primary)") if self.primary else ""
        return (f"{_('Monitor')} {self.index + 1} — "
                f"{self.width}x{self.height} @ {self.x},{self.y}{tag}")


def list_monitors(timeout: float = 5.0) -> list[Monitor]:
    """Return the monitors, or an empty list if the client cannot be queried."""
    client = find_client()
    if not client:
        return []
    try:
        out = subprocess.run([client, "/list:monitor"], capture_output=True,
                             text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    found: list[Monitor] = []
    for line in out.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        found.append(Monitor(
            index=int(m["index"]),
            width=int(m["width"]),
            height=int(m["height"]),
            x=int(m["x"]),
            y=int(m["y"]),
            primary=bool(m["primary"]),
        ))
    return found
