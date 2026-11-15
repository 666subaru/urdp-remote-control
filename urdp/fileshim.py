"""Build the Windows -> Linux file copy fix that xfreerdp3 preloads.

winpr 3.32 cannot turn the file list Windows sends into text/uri-list, so a
file copied on Windows pastes as nothing on Linux.  ``native/filelist.c``
wraps ``ClipboardGetData`` and builds the list itself when winpr fails; see
the comment at the top of that file.

The library is compiled on first use with the system C compiler and cached
under a name derived from the source, so an edited source is rebuilt and a
machine without a compiler simply runs without the fix.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "native" / "filelist.c"
#: Built from SOURCE at packaging time (the AppImage ships one), so machines
#: without a C compiler still get the fix.  Never present in a source checkout.
PREBUILT = SOURCE.with_name("libfilelist.so")


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "urdp"


def library_path(source: Path = SOURCE) -> Path:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    return cache_dir() / f"libfilelist-{digest}.so"


def _private(path: Path) -> bool:
    """Owned by us and writable by nobody else -- or not there yet.

    The cached library is preloaded into xfreerdp3, so whoever can write it
    runs code as this user.  That only matters when XDG_CACHE_HOME points
    somewhere shared, but then it matters a lot.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
        return False
    return not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def ensure(source: Path = SOURCE) -> tuple[Path | None, str]:
    """Return (library, note).  The library is None when it cannot be built."""
    if source == SOURCE and PREBUILT.is_file():
        return PREBUILT, "bundled"
    try:
        target = library_path(source)
    except OSError as exc:
        return None, f"source missing: {exc}"
    if not _private(target.parent):
        return None, "cache directory is not private"
    if target.exists():
        if not _private(target):
            return None, "cached library is not private"
        return target, "cached"
    compiler = next((c for c in ("cc", "gcc", "clang") if shutil.which(c)), None)
    if compiler is None:
        return None, "no C compiler"
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        partial = target.with_suffix(f".{os.getpid()}.tmp")
        result = subprocess.run(
            [compiler, "-O2", "-shared", "-fPIC", "-o", str(partial),
             str(source), "-ldl"],
            capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            return None, "build failed: " + result.stderr.strip()[-300:]
        partial.replace(target)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"build failed: {exc}"
    for old in target.parent.glob("libfilelist-*.so"):
        if old != target:
            old.unlink(missing_ok=True)
    return target, "built"


def preload_value(library: Path, current: str | None) -> str:
    """LD_PRELOAD with *library* first, keeping whatever was already there."""
    parts = [p for p in (current or "").replace(":", " ").split() if p]
    if str(library) in parts:
        return " ".join(parts)
    return " ".join([str(library)] + parts)
