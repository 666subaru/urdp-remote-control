"""Password storage through libsecret's ``secret-tool``.

We deliberately shell out instead of linking against libsecret: the tool is a
hard dependency of most desktops, it keeps the app free of compiled bindings,
and the secret never touches our own config files.
"""

from __future__ import annotations

import shutil
import subprocess

SERVICE = "urdp"


def available() -> bool:
    return shutil.which("secret-tool") is not None


def store(key: str, password: str, label: str | None = None) -> bool:
    """Save *password* under *key*.  Returns False if the keyring refused."""
    if not available() or not key:
        return False
    cmd = ["secret-tool", "store", "--label", label or f"urdp: {key}",
           "service", SERVICE, "target", key]
    try:
        done = subprocess.run(cmd, input=password, text=True,
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def lookup(key: str) -> str | None:
    if not available() or not key:
        return None
    try:
        done = subprocess.run(
            ["secret-tool", "lookup", "service", SERVICE, "target", key],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    # secret-tool prints the secret verbatim, without a trailing newline.
    return done.stdout or None


def clear(key: str) -> bool:
    if not available() or not key:
        return False
    try:
        done = subprocess.run(
            ["secret-tool", "clear", "service", SERVICE, "target", key],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0
