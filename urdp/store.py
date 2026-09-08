"""Profile storage in ``~/.config/urdp/profiles.json``."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .profile import Profile


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base) / "urdp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profiles_path() -> Path:
    return config_dir() / "profiles.json"


def load() -> tuple[list[Profile], str | None, str | None]:
    """Return ``(profiles, last_used_name, language)``.

    A corrupt file is reported as "no profiles" rather than crashing the app;
    the file is left on disk so the user can repair it by hand.
    """
    path = profiles_path()
    if not path.exists():
        return [], None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None, None
    items = data.get("profiles", []) if isinstance(data, dict) else data
    profiles = [Profile.from_dict(item) for item in items
                if isinstance(item, dict)]
    last = data.get("last") if isinstance(data, dict) else None
    language = data.get("language") if isinstance(data, dict) else None
    return profiles, last, language


def save(profiles: list[Profile], last: str | None = None,
         language: str | None = None) -> None:
    """Write the profile list atomically, so a crash cannot truncate it."""
    path = profiles_path()
    payload = {"version": 1,
               "last": last,
               "language": language,
               "profiles": [p.to_dict() for p in profiles]}
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".profiles-",
                                   suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
