"""Find missing system packages and offer to install them.

FreeRDP is never bundled: it parses whatever a remote server sends, gets
security fixes often, and the distribution keeps it up to date.  Instead the
application recognises the distribution, names the packages, and -- with the
user's approval -- runs its package manager through ``pkexec``.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import capabilities, keyring
from .rdpcmd import find_client

#: Package names per distribution family: (FreeRDP 3 client, secret-tool).
PACKAGES: dict[str, tuple[str, str]] = {
    "arch": ("freerdp", "libsecret"),
    "debian": ("freerdp3-x11", "libsecret-tools"),
    "fedora": ("freerdp", "libsecret"),
    "suse": ("freerdp3", "libsecret-tools"),
}

#: The package manager invocation for each family, without the package names.
MANAGERS: dict[str, tuple[str, ...]] = {
    "arch": ("pacman", "-S", "--needed", "--noconfirm"),
    "debian": ("apt-get", "install", "-y"),
    "fedora": ("dnf", "install", "-y"),
    "suse": ("zypper", "--non-interactive", "install"),
}

_FAMILIES = {
    "arch": "arch", "cachyos": "arch", "manjaro": "arch", "endeavouros": "arch",
    "debian": "debian", "ubuntu": "debian", "linuxmint": "debian",
    "pop": "debian",
    "fedora": "fedora", "rhel": "fedora", "centos": "fedora",
    "opensuse": "suse", "suse": "suse", "opensuse-tumbleweed": "suse",
    "opensuse-leap": "suse",
}


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep or not key.strip() or key.strip().startswith("#"):
            continue
        try:
            parts = shlex.split(value)
        except ValueError:
            continue
        values[key.strip()] = parts[0] if parts else ""
    return values


def family(os_release: dict[str, str]) -> str | None:
    """``arch`` / ``debian`` / ``fedora`` / ``suse``, or None if unknown."""
    ids = [os_release.get("ID", "")] + os_release.get("ID_LIKE", "").split()
    for name in ids:
        found = _FAMILIES.get(name.lower())
        if found:
            return found
    return None


def detect_family() -> str | None:
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            return family(parse_os_release(Path(path).read_text("utf-8")))
        except OSError:
            continue
    return None


def freerdp_ok() -> bool:
    """A FreeRDP client exists and is not version 2."""
    if not find_client():
        return False
    major = capabilities.version().split(".")[0]
    return not (major.isdigit() and int(major) < 3)


@dataclass(frozen=True)
class Plan:
    family: str | None
    packages: tuple[str, ...]
    #: argv to run as root, or empty when the distribution is unknown.
    argv: tuple[str, ...]

    @property
    def command(self) -> str:
        """The same thing as one line a user could paste into a terminal."""
        return "sudo " + shlex.join(self.argv) if self.argv else ""


def plan(want_freerdp: bool, want_secret: bool,
         fam: str | None = None) -> Plan | None:
    """What to install; None when nothing is wanted."""
    if not (want_freerdp or want_secret):
        return None
    fam = fam if fam is not None else detect_family()
    if fam not in PACKAGES:
        return Plan(None, (), ())
    freerdp, secret = PACKAGES[fam]
    packages = tuple(p for p, wanted in ((freerdp, want_freerdp),
                                         (secret, want_secret)) if wanted)
    return Plan(fam, packages, MANAGERS[fam] + packages)


def missing_plan() -> Plan | None:
    """What this system lacks right now."""
    return plan(not freerdp_ok(), not keyring.available())


def can_elevate() -> bool:
    return shutil.which("pkexec") is not None


def refresh() -> None:
    """Forget cached probe results after an install."""
    capabilities._buildconfig.cache_clear()          # noqa: SLF001
