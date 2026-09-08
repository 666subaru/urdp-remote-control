"""Translation for the interface.

English is the source language: every user-visible string in the code is
English and doubles as the lookup key, so a missing translation degrades to
readable English rather than to a key name.  Translations live in
``urdp/locale/<code>.json`` -- plain files a contributor can add without
touching any code or running a build step.
"""

from __future__ import annotations

import json
import locale as _locale
import os
from pathlib import Path

#: Native names, shown in the language picker.  English needs no file.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "tr": "Türkçe",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "ru": "Русский",
}

_active = "en"
_table: dict[str, str] = {}


def locale_dir() -> Path:
    return Path(__file__).resolve().parent / "locale"


def available() -> list[tuple[str, str]]:
    """``[(code, native name)]`` for every language that has a file."""
    found = [("en", LANGUAGES["en"])]
    for path in sorted(locale_dir().glob("*.json")):
        code = path.stem
        if code != "en":
            found.append((code, LANGUAGES.get(code, code)))
    return found


def detect() -> str:
    """Guess the language from the environment, falling back to English."""
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key)
        if value:
            code = value.split(":")[0].split(".")[0].split("_")[0].lower()
            if code in dict(available()):
                return code
    try:
        system = _locale.getlocale()[0] or ""
    except ValueError:
        system = ""
    code = system.split("_")[0].lower()
    return code if code in dict(available()) else "en"


def set_language(code: str) -> None:
    """Load *code*; unknown or unreadable languages fall back to English."""
    global _active, _table
    _active = code if code in dict(available()) else "en"
    if _active == "en":
        _table = {}
        return
    try:
        data = json.loads((locale_dir() / f"{_active}.json").read_text("utf-8"))
    except (OSError, ValueError):
        _table = {}
        _active = "en"
        return
    _table = {k: v for k, v in data.items() if isinstance(v, str) and v}


def language() -> str:
    return _active


def gettext(text: str) -> str:
    return _table.get(text, text)


#: Short alias, used everywhere in the interface code.
_ = gettext
