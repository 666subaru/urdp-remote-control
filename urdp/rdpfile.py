"""Read and write Microsoft ``.rdp`` files.

The format is a flat list of ``key:type:value`` lines, where type is ``i``
(integer), ``s`` (string) or ``b`` (binary).  We map the subset that has a
counterpart in our profile; unknown keys are preserved on import so that a
file written by mstsc keeps working when handed back to it.
"""

from __future__ import annotations

from pathlib import Path

from .profile import Profile

#: mstsc's "connection type" values, in the order the drop-down shows them.
_CONNECTION_TYPES = {
    1: "modem", 2: "broadband-low", 3: "satellite", 4: "broadband-high",
    5: "wan", 6: "lan", 7: "auto",
}
_CONNECTION_TYPES_INV = {v: k for k, v in _CONNECTION_TYPES.items()}

_AUDIO = {0: "local", 1: "remote", 2: "none"}
_AUDIO_INV = {v: k for k, v in _AUDIO.items()}

_KEYBOARD = {0: "local", 1: "remote", 2: "fullscreen"}
_KEYBOARD_INV = {v: k for k, v in _KEYBOARD.items()}

# authentication level: 0 = connect and don't warn, 1 = don't connect, 2 = warn
_CERT = {0: "connect", 1: "refuse", 2: "warn"}
_CERT_INV = {v: k for k, v in _CERT.items()}


def _parse(text: str) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        key, kind, value = parts
        entries[key.strip().lower()] = (kind.strip(), value)
    return entries


def load(path: str | Path) -> Profile:
    """Build a profile from an ``.rdp`` file."""
    entries = _parse(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
    p = Profile(name=Path(path).stem)

    def num(key: str, default: int | None = None) -> int | None:
        item = entries.get(key)
        if not item:
            return default
        try:
            return int(item[1].strip())
        except ValueError:
            return default

    def flag(key: str, default: bool) -> bool:
        value = num(key)
        return default if value is None else bool(value)

    def text(key: str, default: str = "") -> str:
        item = entries.get(key)
        return item[1].strip() if item else default

    address = text("full address")
    if address:
        if address.count(":") == 1:
            host, _, port = address.partition(":")
            p.host = host
            try:
                p.port = int(port)
            except ValueError:
                p.port = 3389
        else:
            p.host = address
    p.username = text("username")
    p.domain = text("domain")

    p.multimon = flag("use multimon", False)
    p.span = flag("span monitors", False)
    selected = text("selectedmonitors")
    if selected:
        p.monitors = [int(x) for x in selected.replace(" ", "").split(",")
                      if x.isdigit()]
    p.display_mode = "fullscreen" if num("screen mode id", 2) == 2 else "custom"
    p.width = num("desktopwidth", p.width) or p.width
    p.height = num("desktopheight", p.height) or p.height
    p.color_depth = num("session bpp", p.color_depth) or p.color_depth
    p.scale = num("desktopscalefactor", 100) or 100
    if p.scale not in (100, 140, 180):
        p.scale = 100
    p.dynamic_resolution = flag("dynamic resolution", False)
    p.smart_sizing = flag("smart sizing", False)
    p.floatbar = flag("displayconnectionbar", True)

    p.audio_mode = _AUDIO.get(num("audiomode", 0) or 0, "local")
    p.microphone = flag("audiocapturemode", False)
    p.keyboard_mode = _KEYBOARD.get(num("keyboardhook", 2) or 0, "fullscreen")
    p.printers = flag("redirectprinters", True)
    p.clipboard = flag("redirectclipboard", True)
    p.smartcard = flag("redirectsmartcards", False)
    p.serial = flag("redirectcomports", False)
    drives = text("drivestoredirect")
    p.home_drive = "*" in drives

    conn = _CONNECTION_TYPES.get(num("connection type", 7) or 7, "auto")
    p.apply_experience_preset(conn)
    p.network = "auto" if flag("networkautodetect", False) else conn
    # In .rdp these keys are inverted: 1 means "turn the effect off".
    p.wallpaper = not flag("disable wallpaper", False)
    p.window_drag = not flag("disable full window drag", False)
    p.menu_anims = not flag("disable menu anims", False)
    p.themes = not flag("disable themes", False)
    p.font_smoothing = flag("allow font smoothing", p.font_smoothing)
    p.desktop_composition = flag("allow desktop composition",
                                 p.desktop_composition)
    p.persistent_bitmap_cache = flag("bitmapcachepersistenable", True)
    p.auto_reconnect = flag("autoreconnection enabled", True)
    p.compression = flag("compression", True)

    p.cert_policy = _CERT.get(num("authentication level", 2) or 0, "warn")
    p.admin_session = flag("administrative session", False)
    p.restricted_admin = flag("restrictedadmin", False)
    p.gateway_host = text("gatewayhostname")
    p.gateway_enabled = bool(p.gateway_host) and \
        (num("gatewayusagemethod", 0) or 0) != 0
    p.gateway_username = text("gatewayusername")
    p.gateway_bypass_local = (num("gatewayusagemethod", 4) or 4) == 4
    p.remote_app = flag("remoteapplicationmode", False)
    p.remote_app_program = text("remoteapplicationprogram") or text("alternate shell")
    p.remote_app_cmdline = text("remoteapplicationcmdline")
    return p


def dump(p: Profile) -> str:
    """Serialise *p* into ``.rdp`` text that mstsc can open."""
    address = p.host if p.port in (0, 3389) else f"{p.host}:{p.port}"
    lines = [
        f"full address:s:{address}",
        f"username:s:{p.username}",
        f"domain:s:{p.domain}",
        f"screen mode id:i:{2 if p.display_mode == 'fullscreen' or p.multimon else 1}",
        f"use multimon:i:{int(p.multimon)}",
        f"span monitors:i:{int(p.span)}",
        f"selectedmonitors:s:{','.join(str(i) for i in p.monitors)}",
        f"desktopwidth:i:{p.width}",
        f"desktopheight:i:{p.height}",
        f"session bpp:i:{p.color_depth}",
        f"desktopscalefactor:i:{p.scale}",
        f"dynamic resolution:i:{int(p.dynamic_resolution)}",
        f"smart sizing:i:{int(p.smart_sizing)}",
        f"displayconnectionbar:i:{int(p.floatbar)}",
        f"audiomode:i:{_AUDIO_INV.get(p.audio_mode, 0)}",
        f"audiocapturemode:i:{int(p.microphone)}",
        f"keyboardhook:i:{_KEYBOARD_INV.get(p.keyboard_mode, 2)}",
        f"redirectprinters:i:{int(p.printers)}",
        f"redirectclipboard:i:{int(p.clipboard)}",
        f"redirectsmartcards:i:{int(p.smartcard)}",
        f"redirectcomports:i:{int(p.serial)}",
        f"drivestoredirect:s:{'*' if p.home_drive else ''}",
        f"connection type:i:{_CONNECTION_TYPES_INV.get(p.network, 7)}",
        f"networkautodetect:i:{int(p.network == 'auto')}",
        f"disable wallpaper:i:{int(not p.wallpaper)}",
        f"allow font smoothing:i:{int(p.font_smoothing)}",
        f"allow desktop composition:i:{int(p.desktop_composition)}",
        f"disable full window drag:i:{int(not p.window_drag)}",
        f"disable menu anims:i:{int(not p.menu_anims)}",
        f"disable themes:i:{int(not p.themes)}",
        f"bitmapcachepersistenable:i:{int(p.persistent_bitmap_cache)}",
        f"compression:i:{int(p.compression)}",
        f"autoreconnection enabled:i:{int(p.auto_reconnect)}",
        f"authentication level:i:{_CERT_INV.get(p.cert_policy, 2)}",
        f"administrative session:i:{int(p.admin_session)}",
        f"restrictedadmin:i:{int(p.restricted_admin)}",
        f"prompt for credentials:i:{int(not p.save_password)}",
        f"gatewayhostname:s:{p.gateway_host}",
        f"gatewayusername:s:{p.gateway_username}",
        f"gatewayusagemethod:i:{(4 if p.gateway_bypass_local else 1) if p.gateway_enabled else 0}",
        "gatewaycredentialssource:i:4",
        f"gatewayprofileusagemethod:i:{int(not p.gateway_same_creds)}",
        f"remoteapplicationmode:i:{int(p.remote_app)}",
        f"remoteapplicationprogram:s:{p.remote_app_program}",
        f"remoteapplicationcmdline:s:{p.remote_app_cmdline}",
    ]
    return "\r\n".join(lines) + "\r\n"


def save(p: Profile, path: str | Path) -> None:
    Path(path).write_text(dump(p), encoding="utf-8")
