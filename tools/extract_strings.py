"""Collect every translatable string in the source into ``keys.json``.

Run it after touching any user-visible text::

    QT_QPA_PLATFORM=offscreen python3 tools/extract_strings.py

The test suite runs it too, and fails when a locale file drifts away from what
the code actually asks for.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def from_calls(package: Path) -> dict[str, None]:
    """Strings passed to ``_()`` / ``gettext()`` with a literal argument."""
    found: dict[str, None] = {}
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(
                func, "attr", "")
            if name not in ("_", "gettext") or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found[first.value] = None
    return found


def from_tables() -> dict[str, None]:
    """Combo-box labels and error messages kept as data, translated on use."""
    from urdp.tab_advanced import CERT_POLICIES, GATEWAY_TYPES, SECURITY
    from urdp.tab_display import COLOR_DEPTHS
    from urdp.tab_experience import CONNECTIONS, GFX_CODECS
    from urdp.tab_local import (AUDIO_BACKENDS, AUDIO_MODES, KEYBOARD_MODES,
                                LAYOUTS)
    from urdp.session import _MESSAGES

    found: dict[str, None] = {}
    for table in (COLOR_DEPTHS, AUDIO_MODES, AUDIO_BACKENDS, KEYBOARD_MODES,
                  LAYOUTS, CONNECTIONS, GFX_CODECS, CERT_POLICIES, SECURITY,
                  GATEWAY_TYPES):
        for _value, label in table:
            found[label] = None
    for message in _MESSAGES.values():
        found[message] = None
    return found


def collect() -> list[str]:
    sys.path.insert(0, str(ROOT))
    found = from_calls(ROOT / "urdp")
    found.update(from_tables())
    return list(found)


if __name__ == "__main__":
    keys = collect()
    (ROOT / "keys.json").write_text(
        json.dumps(keys, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(len(keys), "strings")
