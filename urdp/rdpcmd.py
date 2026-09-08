"""Translate a :class:`~urdp.profile.Profile` into an ``xfreerdp3`` command line.

Every flag emitted here was checked against the help output of FreeRDP 3.x
(``xfreerdp3 /?``).  Where FreeRDP's default is version dependent we always
write the flag explicitly, so a profile behaves the same on any machine.

The password is never placed in ``argv`` -- it would show up in ``ps`` for every
user on the box.  Instead we pass ``/from-stdin`` and feed the password through
the process' standard input; see :meth:`urdp.session.Session.start`.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass

from .i18n import _
from .profile import Profile

#: Binary names to try, in order of preference.
CANDIDATES = ("xfreerdp3", "xfreerdp", "sdl-freerdp3", "sdl-freerdp")

#: mstsc offers connection types FreeRDP has no name for; map them onto the
#: closest value ``/network:`` accepts.
NETWORK_MAP = {"satellite": "broadband-low"}
VALID_NETWORKS = {"modem", "broadband", "broadband-low", "broadband-high",
                  "wan", "lan", "auto"}


def find_client() -> str | None:
    """Return the path of the first FreeRDP client we can find."""
    for name in CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _audio_args(p: Profile) -> list[str]:
    backend = "" if p.audio_backend == "auto" else f":sys:{p.audio_backend}"
    if p.audio_mode == "local":
        args = [f"/sound{backend}", "/audio-mode:0"]
    elif p.audio_mode == "remote":
        args = ["/audio-mode:1"]
    else:
        args = ["/audio-mode:2"]
    if p.microphone:
        args.append(f"/microphone{backend}")
    return args


def _display_args(p: Profile) -> list[str]:
    args: list[str] = [f"/bpp:{p.color_depth}"]

    if p.multimon:
        # Multi-monitor is inherently fullscreen; adding /f as well confuses
        # FreeRDP's layout code, so we never combine them.
        args.append("/multimon:force" if p.multimon_force else "/multimon")
        if p.monitors:
            args.append("/monitors:" + ",".join(str(i) for i in p.monitors))
    elif p.span:
        args.append("+span")
        if p.monitors:
            args.append("/monitors:" + ",".join(str(i) for i in p.monitors))
    elif p.display_mode == "fullscreen":
        args.append("/f")
        if p.monitors:
            args.append("/monitors:" + ",".join(str(i) for i in p.monitors))
    elif p.display_mode == "workarea":
        args.append("+workarea")
    else:
        args.append(f"/size:{p.width}x{p.height}")

    if p.scale in (140, 180):
        args += [f"/scale:{p.scale}", f"/scale-desktop:{p.scale}"]

    # Resolution updates on window resize only make sense for a single windowed
    # monitor -- the server rejects the combination with multimon.
    if p.dynamic_resolution and not p.multimon and not p.span:
        args.append("+dynamic-resolution")
    if p.smart_sizing:
        args.append("/smart-sizing")
    if not p.decorations:
        args.append("-decorations")

    if p.floatbar and (p.multimon or p.span or p.display_mode == "fullscreen"):
        args.append("/floatbar:sticky:on,default:visible,show:always")
    return args


def _local_resource_args(p: Profile) -> list[str]:
    args = _audio_args(p)

    if p.keyboard_mode == "remote":
        args.append("+grab-keyboard")
    elif p.keyboard_mode == "local":
        args.append("-grab-keyboard")
    # "fullscreen" is FreeRDP's own default: grab only while fullscreen.

    if p.kbd_layout:
        args.append(f"/kbd:layout:{p.kbd_layout}")

    args.append("/clipboard" if p.clipboard else "-clipboard")
    if p.printers:
        args.append("/printer")
    if p.smartcard:
        args.append("/smartcard")
    if p.serial:
        args.append("/serial")
    if p.parallel:
        args.append("/parallel")
    if p.home_drive:
        args.append("+home-drive")
    for entry in p.drives:
        if len(entry) == 2 and entry[0] and entry[1]:
            args.append(f"/drive:{entry[0]},{entry[1]}")
    if p.usb:
        args.append(f"/usb:{p.usb_filter or 'auto'}")
    return args


def _experience_args(p: Profile) -> list[str]:
    network = NETWORK_MAP.get(p.network, p.network)
    if network not in VALID_NETWORKS:
        network = "auto"
    args = [f"/network:{network}"]
    for enabled, flag in (
        (p.wallpaper, "wallpaper"),
        (p.font_smoothing, "fonts"),
        (p.desktop_composition, "aero"),
        (p.window_drag, "window-drag"),
        (p.menu_anims, "menu-anims"),
        (p.themes, "themes"),
    ):
        args.append(("+" if enabled else "-") + flag)

    args.append("/cache:bitmap:on,persist:on" if p.persistent_bitmap_cache
                else "/cache:bitmap:on")
    args.append("+auto-reconnect" if p.auto_reconnect else "-auto-reconnect")
    args.append("+compression" if p.compression else "-compression")

    if p.gfx_codec == "off":
        args.append("-gfx")
    elif p.gfx_codec == "auto":
        args.append("/gfx")
    else:
        args.append(f"/gfx:{p.gfx_codec}")
    return args


def _advanced_args(p: Profile) -> list[str]:
    args: list[str] = []

    # FreeRDP would normally ask about an unknown certificate on the terminal,
    # but we are launched from a desktop file with no terminal attached, so the
    # policy has to be decided up front.
    args.append({"connect": "/cert:ignore",
                 "refuse": "/cert:deny"}.get(p.cert_policy, "/cert:tofu"))

    if p.security != "auto":
        args.append(f"/sec:{p.security}")
    if p.admin_session:
        args.append("+admin")
    if p.restricted_admin:
        args.append("+restricted-admin")
    if p.timeout_ms:
        args.append(f"/timeout:{p.timeout_ms}")

    if p.gateway_enabled and p.gateway_host:
        parts = [f"g:{p.gateway_host}:{p.gateway_port}"]
        if not p.gateway_same_creds:
            if p.gateway_username:
                parts.append(f"u:{p.gateway_username}")
            if p.gateway_domain:
                parts.append(f"d:{p.gateway_domain}")
        if p.gateway_type != "auto":
            parts.append(f"type:{p.gateway_type}")
        parts.append("usage-method:" + ("detect" if p.gateway_bypass_local
                                        else "direct"))
        args.append("/gateway:" + ",".join(parts))

    if p.remote_app and p.remote_app_program:
        spec = f"program:{p.remote_app_program}"
        if p.remote_app_cmdline:
            spec += f",cmd:{p.remote_app_cmdline}"
        args.append(f"/app:{spec}")
    return args


def build_command(p: Profile, *, with_password: bool = True,
                  client: str | None = None) -> list[str]:
    """Return the full argv for *p*.

    ``with_password`` only decides whether ``/from-stdin`` is appended; the
    secret itself is written to the child's stdin later.
    """
    argv = [client or find_client() or "xfreerdp3"]

    argv.append(f"/v:{p.host}")
    if p.port and p.port != 3389:
        argv.append(f"/port:{p.port}")
    if p.username:
        argv.append(f"/u:{p.username}")
    if p.domain:
        argv.append(f"/d:{p.domain}")

    argv += _display_args(p)
    argv += _local_resource_args(p)
    argv += _experience_args(p)
    argv += _advanced_args(p)

    if p.extra_args.strip():
        argv += shlex.split(p.extra_args)
    if with_password:
        argv.append("/from-stdin")
    return argv


def command_string(p: Profile, **kwargs) -> str:
    """Shell-quoted command, for the "Show Command" dialog."""
    return " ".join(shlex.quote(a) for a in build_command(p, **kwargs))


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a profile.

    ``blocking`` means the connection cannot work at all, so the interface
    refuses instead of asking.  Severity is carried in the data rather than
    inferred from the wording, which would break as soon as the message is
    translated.
    """

    message: str
    blocking: bool = False


def validate(p: Profile) -> list[Problem]:
    """Return warnings about missing or contradictory settings."""
    problems: list[Problem] = []

    if not p.host.strip():
        problems.append(Problem(
            _("The computer name is empty. Enter the full remote computer "
              "name."), blocking=True))
    if not p.username.strip():
        # /from-stdin feeds FreeRDP whatever credential is missing, in order.
        # With no /u: it asks for the user name first and would swallow the
        # password as the user name -- and the prompt itself is invisible,
        # because the application has no terminal attached.
        problems.append(Problem(
            _("The user name is empty. The application has no terminal, so "
              "FreeRDP cannot ask for it; enter a user name."), blocking=True))
    if p.multimon and p.span:
        problems.append(Problem(
            _("Multiple monitors and screen spanning cannot be used together; "
              "multiple monitors wins.")))
    if p.multimon and p.dynamic_resolution:
        problems.append(Problem(
            _("Dynamic resolution does not work with multiple monitors and is "
              "ignored.")))
    if p.multimon and len(p.monitors) == 1:
        problems.append(Problem(
            _("Multiple monitors is on but only one monitor is selected.")))
    if p.remote_app and not p.remote_app_program.strip():
        problems.append(Problem(
            _("RemoteApp is on but the program path is empty.")))
    if p.gateway_enabled and not p.gateway_host.strip():
        problems.append(Problem(
            _("The gateway is on but its server address is empty.")))
    return problems
