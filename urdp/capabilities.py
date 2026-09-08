"""What the installed FreeRDP can actually do.

Two of FreeRDP's option parsers accept unknown values silently -- ``/gfx:ZZZZ``
produces no error and simply does nothing.  So the interface must not offer a
codec the local build cannot use; it would look like it worked.  ``/buildconfig``
tells us what was compiled in.

On the protocol side the ceiling is fixed: the RDP graphics pipeline
(MS-RDPEGFX) defines UNCOMPRESSED, CAVIDEO, CLEARCODEC, PLANAR, ALPHA,
progressive, AVC420, AVC444 and AVC444v2.  There is no HEVC/H.265 codec in RDP
at all, and the AV1 id FreeRDP carries is its own extension between FreeRDP
peers, disabled in most distribution builds -- see ``docs/kodekler.md``.
"""

from __future__ import annotations

import functools
import re
import subprocess

from .i18n import _
from .rdpcmd import find_client

_VERSION = re.compile(r"version\s+(?:\[[^\]]+\]\s+)?(\d+\.\d+\.\d+)")


@functools.cache
def _buildconfig() -> tuple[str, dict[str, str]]:
    """Return ``(version, flags)``; empty values when FreeRDP cannot be run."""
    client = find_client()
    if not client:
        return "", {}
    try:
        done = subprocess.run([client, "/buildconfig"], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "", {}

    text = done.stdout + done.stderr
    match = _VERSION.search(text)
    version = match.group(1) if match else ""

    flags: dict[str, str] = {}
    for token in text.split():
        if token.startswith("WITH_") and "=" in token:
            name, _, value = token.partition("=")
            flags[name] = value
    return version, flags


def version() -> str:
    return _buildconfig()[0]


def flag(name: str, default: bool = False) -> bool:
    value = _buildconfig()[1].get(name)
    if value is None:
        return default
    return value.upper() in ("ON", "1", "TRUE", "YES")


def has_h264() -> bool:
    """True when AVC420/AVC444 will actually decode."""
    # Assume yes when FreeRDP could not be queried, so the options stay usable.
    if not _buildconfig()[1]:
        return True
    return flag("WITH_GFX_H264", True)


def has_av1() -> bool:
    """FreeRDP's own AV1 extension -- not something Windows speaks."""
    return flag("WITH_GFX_AV1", False)


def summary() -> str:
    """One line for the interface, naming what this build supports."""
    build_version, flags = _buildconfig()
    if not flags:
        return _("Could not read the FreeRDP version.")
    codecs = ["RemoteFX", "Progressive"]
    if has_h264():
        codecs[:0] = ["H.264 (AVC420/AVC444)"]
    if has_av1():
        codecs.append(_("AV1 (FreeRDP extension)"))
    return _("FreeRDP {version} — available codecs: {codecs}. The RDP protocol "
             "has no H.265/HEVC.").format(version=build_version,
                                          codecs=", ".join(codecs))
