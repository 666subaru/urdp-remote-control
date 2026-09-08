"""Translation for the interface.

English is the source language: every user-visible string in the code is
English and doubles as the lookup key, so a missing translation degrades to
readable English rather than to a key name.  Translations live in
``urdp/locale/<code>.json`` -- plain files a contributor can add without
touching any code or running a build step.

Codes follow the locale naming Qt and gettext use: a language code, optionally
with a region (``pt_BR``).  A region file wins over the plain language file
when the environment asks for it, and falls back to it when it does not exist.
"""

from __future__ import annotations

import json
import locale as _locale
import os
from pathlib import Path

#: Native names, shown in the language picker.  English needs no file.
#: The set mirrors the display languages Windows itself ships.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "ar": "العربية",
    "bg": "Български",
    "cs": "Čeština",
    "da": "Dansk",
    "de": "Deutsch",
    "el": "Ελληνικά",
    "es": "Español",
    "et": "Eesti",
    "fi": "Suomi",
    "fr": "Français",
    "he": "עברית",
    "hr": "Hrvatski",
    "hu": "Magyar",
    "id": "Indonesia",
    "it": "Italiano",
    "ja": "日本語",
    "ko": "한국어",
    "lt": "Lietuvių",
    "lv": "Latviešu",
    "nb": "Norsk bokmål",
    "nl": "Nederlands",
    "pl": "Polski",
    "pt": "Português",
    "pt_BR": "Português (Brasil)",
    "ro": "Română",
    "ru": "Русский",
    "sk": "Slovenčina",
    "sl": "Slovenščina",
    "sr": "Srpski",
    "sv": "Svenska",
    "th": "ไทย",
    "tr": "Türkçe",
    "uk": "Українська",
    "vi": "Tiếng Việt",
    "zh_CN": "简体中文",
    "zh_TW": "繁體中文",
}

#: Languages written right to left; the whole interface has to be mirrored.
RTL = frozenset({"ar", "he"})

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
    found.sort(key=lambda item: (item[0] != "en", LANGUAGES.get(item[0],
                                                                item[0])))
    return found


def _normalise(value: str) -> str:
    """``tr_TR.UTF-8`` -> ``tr_TR``; ``pt-br`` -> ``pt_BR``."""
    value = value.split(":")[0].split(".")[0].split("@")[0]
    value = value.replace("-", "_")
    if "_" in value:
        language, _, region = value.partition("_")
        return f"{language.lower()}_{region.upper()}"
    return value.lower()


def _resolve(value: str, known: set[str]) -> str | None:
    """Prefer an exact region match, then the bare language."""
    code = _normalise(value)
    if code in known:
        return code
    base = code.split("_")[0]
    if base in known:
        return base
    # "pt" asked for, only "pt_BR" shipped: take the first region we have.
    for candidate in sorted(known):
        if candidate.split("_")[0] == base:
            return candidate
    return None


def detect() -> str:
    """Guess the language from the environment, falling back to English."""
    known = {code for code, _name in available()}
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key)
        if value:
            match = _resolve(value, known)
            if match:
                return match
    try:
        system = _locale.getlocale()[0] or ""
    except ValueError:
        system = ""
    return _resolve(system, known) or "en" if system else "en"


def set_language(code: str) -> None:
    """Load *code*; unknown or unreadable languages fall back to English."""
    global _active, _table
    known = {name for name, _native in available()}
    _active = code if code in known else "en"
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


def is_rtl(code: str | None = None) -> bool:
    return (code or _active).split("_")[0] in RTL


def gettext(text: str) -> str:
    return _table.get(text, text)


#: Short alias, used everywhere in the interface code.
_ = gettext
