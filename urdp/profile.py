"""Connection profile model and (de)serialisation.

A :class:`Profile` holds everything the UI can configure.  It maps onto two
different on-disk formats:

* ``profiles.json`` -- our own store, keeps every field verbatim.
* ``*.rdp``         -- the Microsoft format, so profiles can be exchanged with
  ``mstsc.exe``.  Only the subset of keys mstsc understands survives a
  round-trip; see :mod:`urdp.rdpfile`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


# Connection-type presets, mirroring the "Deneyim" tab of mstsc.  Each entry is
# (network, wallpaper, font_smoothing, desktop_composition, window_drag,
#  menu_anims, themes).
EXPERIENCE_PRESETS: dict[str, tuple[str, bool, bool, bool, bool, bool, bool]] = {
    "modem":          ("modem",          False, False, False, False, False, True),
    "broadband-low":  ("broadband-low",  False, False, False, False, False, True),
    "satellite":      ("satellite",      True,  False, False, False, False, True),
    "broadband-high": ("broadband-high", True,  True,  False, True,  False, True),
    "wan":            ("wan",            True,  True,  True,  True,  True,  True),
    "lan":            ("lan",            True,  True,  True,  True,  True,  True),
    "auto":           ("auto",           True,  True,  True,  True,  True,  True),
}


@dataclass
class Profile:
    """One saved connection."""

    # -------------------------------------------------------------- General
    name: str = "New connection"
    host: str = ""
    port: int = 3389
    username: str = ""
    domain: str = ""
    save_password: bool = False

    # --------------------------------------------------------------- Display
    # "fullscreen" uses every pixel of the selected monitors, "custom" opens a
    # window of width x height, "workarea" fits the desktop work area.
    display_mode: str = "fullscreen"
    width: int = 1920
    height: int = 1080
    multimon: bool = True
    multimon_force: bool = False
    monitors: list[int] = field(default_factory=list)   # empty == all monitors
    span: bool = False
    color_depth: int = 32
    floatbar: bool = True
    scale: int = 100                                    # 100 | 140 | 180
    dynamic_resolution: bool = False
    smart_sizing: bool = False
    decorations: bool = True

    # ------------------------------------------------------ Local resources
    audio_mode: str = "local"          # local | remote | none
    audio_backend: str = "auto"        # auto | pulse | alsa | oss | sdl
    microphone: bool = False
    keyboard_mode: str = "fullscreen"  # local | remote | fullscreen
    kbd_layout: str = ""               # "" == autodetect, else e.g. "0x041F"
    printers: bool = True
    clipboard: bool = True
    smartcard: bool = False
    serial: bool = False
    parallel: bool = False
    home_drive: bool = False
    drives: list[list[str]] = field(default_factory=list)   # [[share, path], ...]
    usb: bool = False
    usb_filter: str = "auto"

    # ------------------------------------------------------------ Experience
    network: str = "auto"
    wallpaper: bool = True
    font_smoothing: bool = True
    desktop_composition: bool = True
    window_drag: bool = True
    menu_anims: bool = True
    themes: bool = True
    persistent_bitmap_cache: bool = True
    auto_reconnect: bool = True
    gfx_codec: str = "auto"            # auto | AVC444 | AVC420 | RFX | progressive | off
    compression: bool = True

    # -------------------------------------------------------------- Advanced
    cert_policy: str = "warn"          # warn | connect | refuse
    gateway_enabled: bool = False
    gateway_host: str = ""
    gateway_port: int = 443
    gateway_username: str = ""
    gateway_domain: str = ""
    gateway_same_creds: bool = True
    gateway_type: str = "auto"         # auto | rpc | http
    gateway_bypass_local: bool = True
    admin_session: bool = False
    restricted_admin: bool = False
    security: str = "auto"             # auto | nla | tls | rdp
    remote_app: bool = False
    remote_app_program: str = ""
    remote_app_cmdline: str = ""
    timeout_ms: int = 0                # 0 == leave FreeRDP's default alone
    extra_args: str = ""

    # ------------------------------------------------------------------ API
    @property
    def label(self) -> str:
        """Human readable one-liner used in the profile drop-down."""
        if self.host and self.username:
            return f"{self.name} ({self.username}@{self.host})"
        if self.host:
            return f"{self.name} ({self.host})"
        return self.name

    @property
    def secret_key(self) -> str:
        """Identifier under which the password lives in the keyring."""
        return f"{self.username}@{self.host}:{self.port}"

    def apply_experience_preset(self, key: str) -> None:
        """Set the six experience toggles the way mstsc does for *key*."""
        preset = EXPERIENCE_PRESETS.get(key)
        if preset is None:
            return
        (self.network, self.wallpaper, self.font_smoothing,
         self.desktop_composition, self.window_drag, self.menu_anims,
         self.themes) = preset

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        """Build a profile, ignoring unknown keys and repairing bad types.

        Profiles are user-editable JSON, and older versions of the app may have
        written fewer fields, so anything unparseable falls back to the default.
        """
        known = {f.name: f for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in (data or {}).items():
            spec = known.get(key)
            if spec is None:
                continue
            try:
                if spec.type in ("int", int):
                    kwargs[key] = int(value)
                elif spec.type in ("bool", bool):
                    kwargs[key] = bool(value)
                elif spec.type in ("str", str):
                    kwargs[key] = str(value)
                else:
                    kwargs[key] = value
            except (TypeError, ValueError):
                continue
        return cls(**kwargs)
