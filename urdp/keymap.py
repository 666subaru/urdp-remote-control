"""Which keys type a character on this desktop's keyboard layout.

The tablet's on-screen keyboard sends text, not keys, but a virtual keyboard
can only press keys.  xkbcommon (always present under KDE) knows, for the
active layout, which key and which Shift / AltGr combination gives each
character; this module asks it through ctypes.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import re
import subprocess

KEY_LEFTSHIFT = 42
KEY_RIGHTALT = 100          # AltGr
KEY_ENTER = 28
KEY_TAB = 15


class _RuleNames(ctypes.Structure):
    _fields_ = [("rules", ctypes.c_char_p), ("model", ctypes.c_char_p),
                ("layout", ctypes.c_char_p), ("variant", ctypes.c_char_p),
                ("options", ctypes.c_char_p)]


def kde_layout() -> tuple[str, str]:
    """(layout, variant) KDE is using right now; ("us", "") if unknown."""
    def call(method: str) -> str:
        try:
            return subprocess.run(
                ["busctl", "--user", "call", "org.kde.keyboard", "/Layouts",
                 "org.kde.KeyboardLayouts", method],
                capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    layouts = re.findall(r'"([^"]*)" "([^"]*)" "[^"]*"', call("getLayoutsList"))
    index = re.search(r"u (\d+)", call("getLayout"))
    if not layouts:
        return "us", ""
    pick = int(index.group(1)) if index else 0
    return layouts[pick] if pick < len(layouts) else layouts[0]


def _rank(key: int, mods: tuple[int, ...]) -> tuple:
    """Smaller is better: the main block of keys (not the keypad or media
    keys), then no modifier, Shift, AltGr, then the lower key code."""
    main = key <= 58 or key == 86
    weight = sum(2 if m == KEY_RIGHTALT else 1 for m in mods)
    return (not main, weight, key)


def build(layout: str, variant: str = "") -> dict[str, tuple[int, tuple[int, ...]]]:
    """character -> (evdev key code, modifier key codes to hold)."""
    path = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
    try:
        lib = ctypes.CDLL(path)
    except OSError:
        return {}
    vp, u32 = ctypes.c_void_p, ctypes.c_uint32
    lib.xkb_context_new.restype = vp
    lib.xkb_context_new.argtypes = [ctypes.c_int]
    lib.xkb_keymap_new_from_names.restype = vp
    lib.xkb_keymap_new_from_names.argtypes = [vp, ctypes.POINTER(_RuleNames),
                                             ctypes.c_int]
    for name in ("xkb_keymap_min_keycode", "xkb_keymap_max_keycode"):
        getattr(lib, name).restype = u32
        getattr(lib, name).argtypes = [vp]
    lib.xkb_keymap_num_levels_for_key.restype = u32
    lib.xkb_keymap_num_levels_for_key.argtypes = [vp, u32, u32]
    lib.xkb_keymap_key_get_syms_by_level.restype = ctypes.c_int
    lib.xkb_keymap_key_get_syms_by_level.argtypes = [
        vp, u32, u32, u32, ctypes.POINTER(ctypes.POINTER(u32))]
    lib.xkb_keymap_key_get_mods_for_level.restype = ctypes.c_size_t
    lib.xkb_keymap_key_get_mods_for_level.argtypes = [
        vp, u32, u32, u32, ctypes.POINTER(u32), ctypes.c_size_t]
    lib.xkb_keymap_mod_get_index.restype = u32
    lib.xkb_keymap_mod_get_index.argtypes = [vp, ctypes.c_char_p]
    lib.xkb_keysym_to_utf32.restype = u32
    lib.xkb_keysym_to_utf32.argtypes = [u32]
    lib.xkb_keymap_unref.argtypes = [vp]
    lib.xkb_context_unref.argtypes = [vp]

    context = lib.xkb_context_new(0)
    if not context:
        return {}
    names = _RuleNames(b"evdev", b"pc105", layout.encode(), variant.encode(),
                       b"")
    keymap = lib.xkb_keymap_new_from_names(context, ctypes.byref(names), 0)
    if not keymap:
        lib.xkb_context_unref(context)
        return {}
    try:
        shift = 1 << lib.xkb_keymap_mod_get_index(keymap, b"Shift")
        altgr = 1 << lib.xkb_keymap_mod_get_index(keymap, b"Mod5")
        found: dict[str, tuple[int, tuple[int, ...]]] = {}
        ranks: dict[str, tuple] = {}
        for code in range(lib.xkb_keymap_min_keycode(keymap),
                          lib.xkb_keymap_max_keycode(keymap) + 1):
            for level in range(lib.xkb_keymap_num_levels_for_key(keymap, code, 0)):
                syms = ctypes.POINTER(u32)()
                count = lib.xkb_keymap_key_get_syms_by_level(
                    keymap, code, 0, level, ctypes.byref(syms))
                if count != 1:
                    continue
                point = lib.xkb_keysym_to_utf32(syms[0])
                if not point or point < 0x20 or point == 0x7F:
                    continue
                masks = (u32 * 8)()
                n = lib.xkb_keymap_key_get_mods_for_level(keymap, code, 0,
                                                          level, masks, 8)
                options = [m for m in masks[:n] if not m & ~(shift | altgr)]
                if not options:
                    continue
                mask = min(options, key=lambda m: bin(m).count("1"))
                mods = tuple(k for bit, k in ((shift, KEY_LEFTSHIFT),
                                              (altgr, KEY_RIGHTALT))
                             if mask & bit)
                char = chr(point)
                rank = _rank(code - 8, mods)
                if char not in ranks or rank < ranks[char]:
                    ranks[char] = rank
                    found[char] = (code - 8, mods)
        return found
    finally:
        lib.xkb_keymap_unref(keymap)
        lib.xkb_context_unref(context)
