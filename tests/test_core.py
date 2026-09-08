"""Tests for the non-GUI core: command building, .rdp files, parsing."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urdp import capabilities, i18n, monitors, probe, rdpfile  # noqa: E402
from urdp.profile import Profile                        # noqa: E402
from urdp.rdpcmd import build_command, validate         # noqa: E402


def args(profile: Profile) -> list[str]:
    """Command without the client path, so tests do not depend on it."""
    return build_command(profile, with_password=False, client="xfreerdp3")[1:]


class CommandTest(unittest.TestCase):
    def test_multimon_selects_monitors_and_skips_fullscreen(self):
        got = args(Profile(host="pc", multimon=True, monitors=[0, 1]))
        self.assertIn("/multimon", got)
        self.assertIn("/monitors:0,1", got)
        self.assertNotIn("/f", got)

    def test_multimon_force(self):
        got = args(Profile(host="pc", multimon=True, multimon_force=True))
        self.assertIn("/multimon:force", got)
        self.assertNotIn("/multimon", got)

    def test_span_is_separate_from_multimon(self):
        got = args(Profile(host="pc", multimon=False, span=True))
        self.assertIn("+span", got)
        self.assertNotIn("/multimon", got)

    def test_windowed_size(self):
        got = args(Profile(host="pc", multimon=False, display_mode="custom",
                           width=1600, height=900))
        self.assertIn("/size:1600x900", got)

    def test_dynamic_resolution_dropped_with_multimon(self):
        got = args(Profile(host="pc", multimon=True, dynamic_resolution=True))
        self.assertNotIn("+dynamic-resolution", got)
        got = args(Profile(host="pc", multimon=False, display_mode="custom",
                           dynamic_resolution=True))
        self.assertIn("+dynamic-resolution", got)

    def test_port_only_when_not_default(self):
        self.assertNotIn("/port:3389", args(Profile(host="pc")))
        self.assertIn("/port:3390", args(Profile(host="pc", port=3390)))

    def test_audio_modes(self):
        local = args(Profile(host="pc", audio_mode="local"))
        self.assertIn("/audio-mode:0", local)
        self.assertIn("/sound", local)
        self.assertIn("/audio-mode:1", args(Profile(host="pc",
                                                   audio_mode="remote")))
        self.assertIn("/audio-mode:2", args(Profile(host="pc",
                                                   audio_mode="none")))

    def test_keyboard_modes(self):
        self.assertIn("+grab-keyboard",
                      args(Profile(host="pc", keyboard_mode="remote")))
        self.assertIn("-grab-keyboard",
                      args(Profile(host="pc", keyboard_mode="local")))
        fullscreen = args(Profile(host="pc", keyboard_mode="fullscreen"))
        self.assertNotIn("+grab-keyboard", fullscreen)
        self.assertNotIn("-grab-keyboard", fullscreen)

    def test_experience_flags_are_always_explicit(self):
        got = args(Profile(host="pc", wallpaper=False, themes=True))
        self.assertIn("-wallpaper", got)
        self.assertIn("+themes", got)

    def test_satellite_is_translated_for_freerdp(self):
        self.assertIn("/network:broadband-low",
                      args(Profile(host="pc", network="satellite")))

    def test_unknown_network_falls_back_to_auto(self):
        self.assertIn("/network:auto", args(Profile(host="pc", network="???")))

    def test_certificate_policies(self):
        self.assertIn("/cert:tofu", args(Profile(host="pc", cert_policy="warn")))
        self.assertIn("/cert:ignore",
                      args(Profile(host="pc", cert_policy="connect")))
        self.assertIn("/cert:deny",
                      args(Profile(host="pc", cert_policy="refuse")))

    def test_gateway(self):
        got = args(Profile(host="pc", gateway_enabled=True,
                           gateway_host="gw.example", gateway_port=443,
                           gateway_same_creds=False, gateway_username="u",
                           gateway_type="rpc", gateway_bypass_local=True))
        gateway = [a for a in got if a.startswith("/gateway:")]
        self.assertEqual(len(gateway), 1)
        self.assertIn("g:gw.example:443", gateway[0])
        self.assertIn("u:u", gateway[0])
        self.assertIn("type:rpc", gateway[0])
        self.assertIn("usage-method:detect", gateway[0])

    def test_drives_and_extra_args(self):
        got = args(Profile(host="pc", drives=[["Belgeler", "/home/x/Belgeler"]],
                           extra_args="/log-level:INFO +fipsmode"))
        self.assertIn("/drive:Belgeler,/home/x/Belgeler", got)
        self.assertIn("/log-level:INFO", got)
        self.assertIn("+fipsmode", got)

    def test_password_flag_only_when_requested(self):
        self.assertIn("/from-stdin",
                      build_command(Profile(host="pc"), client="x"))
        self.assertNotIn("/from-stdin",
                         build_command(Profile(host="pc"), with_password=False,
                                       client="x"))


class ValidateTest(unittest.TestCase):
    def test_empty_host(self):
        problems = validate(Profile())
        self.assertTrue(any(p.blocking for p in problems))

    def test_clean_profile(self):
        self.assertEqual(validate(Profile(host="pc", username="u",
                                          multimon=True, monitors=[0, 1])), [])

    def test_missing_username_is_blocking(self):
        # /from-stdin would take the password as the user name, so this can
        # never be a mere warning.
        problems = validate(Profile(host="pc"))
        self.assertTrue(any(p.blocking and "user name" in p.message
                            for p in problems))

    def test_session_refuses_password_without_username(self):
        from urdp.session import Session
        error = Session().start(Profile(host="pc"), "secret")
        self.assertIsNotNone(error)
        self.assertIn("user name", error)

    def test_multimon_with_dynamic_resolution(self):
        problems = validate(Profile(host="pc", username="u", multimon=True,
                                    dynamic_resolution=True))
        self.assertTrue(any("Dynamic resolution" in p.message
                            for p in problems))
        self.assertFalse(any(p.blocking for p in problems))


class RdpFileTest(unittest.TestCase):
    def test_round_trip(self):
        original = Profile(host="pc.local", port=3390, username="halo",
                           domain="WORK", multimon=True, monitors=[0, 1],
                           color_depth=24, network="lan", wallpaper=False,
                           menu_anims=False, audio_mode="remote",
                           microphone=True, keyboard_mode="remote",
                           cert_policy="connect", admin_session=True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.rdp"
            rdpfile.save(original, path)
            loaded = rdpfile.load(path)

        for field in ("host", "port", "username", "domain", "multimon",
                      "monitors", "color_depth", "network", "wallpaper",
                      "menu_anims", "audio_mode", "microphone",
                      "keyboard_mode", "cert_policy", "admin_session"):
            self.assertEqual(getattr(original, field), getattr(loaded, field),
                             f"{field} korunmadı")

    def test_mstsc_style_file(self):
        text = ("full address:s:10.0.0.5\r\n"
                "username:s:admin\r\n"
                "screen mode id:i:2\r\n"
                "use multimon:i:1\r\n"
                "session bpp:i:32\r\n"
                "audiomode:i:0\r\n"
                "disable wallpaper:i:1\r\n"
                "authentication level:i:0\r\n")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "from-windows.rdp"
            path.write_text(text, encoding="utf-8")
            profile = rdpfile.load(path)
        self.assertEqual(profile.host, "10.0.0.5")
        self.assertEqual(profile.username, "admin")
        self.assertTrue(profile.multimon)
        self.assertFalse(profile.wallpaper)
        self.assertEqual(profile.cert_policy, "connect")


class ProfileTest(unittest.TestCase):
    def test_from_dict_ignores_junk_and_coerces(self):
        profile = Profile.from_dict({"host": "pc", "port": "3390",
                                     "multimon": 1, "nonexistent": True})
        self.assertEqual(profile.port, 3390)
        self.assertIs(profile.multimon, True)
        self.assertFalse(hasattr(profile, "nonexistent"))

    def test_preset_changes_effects(self):
        profile = Profile()
        profile.apply_experience_preset("modem")
        self.assertFalse(profile.wallpaper)
        profile.apply_experience_preset("lan")
        self.assertTrue(profile.wallpaper)


class MonitorParseTest(unittest.TestCase):
    def test_parses_freerdp_output(self):
        sample = ("      * [0] 2880x1620\t+0+0\n"
                  "        [1] 2560x1600\t+2880+0\n")
        parsed = [monitors._LINE.match(line) for line in sample.splitlines()]
        self.assertTrue(all(parsed))
        self.assertEqual(parsed[0]["width"], "2880")
        self.assertTrue(parsed[0]["primary"])
        self.assertIsNone(parsed[1]["primary"])
        self.assertEqual(parsed[1]["x"], "2880")


class CapabilityTest(unittest.TestCase):
    """The codec list must never offer something FreeRDP would ignore."""

    def test_offered_codecs_are_all_real(self):
        from urdp.tab_experience import GFX_CODECS
        offered = {value for value, _ in GFX_CODECS}
        # Exactly what "xfreerdp3 /?" documents for /gfx, plus our own
        # auto/off entries.  H.265 is deliberately absent: RDP has no such
        # codec, and /gfx silently ignores values it does not know.
        self.assertEqual(offered, {"auto", "AVC444", "AVC420", "RFX",
                                   "progressive", "off"})
        self.assertNotIn("H265", offered)
        self.assertNotIn("HEVC", offered)

    def test_buildconfig_parsing(self):
        version, flags = capabilities._buildconfig()
        if not flags:
            self.skipTest("FreeRDP kurulu değil")
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertIn("WITH_GFX_H264", flags)
        self.assertIn("H.265", capabilities.summary())

    def test_flag_defaults_when_unknown(self):
        self.assertFalse(capabilities.flag("WITH_MADE_UP_OPTION"))
        self.assertTrue(capabilities.flag("WITH_MADE_UP_OPTION", True))


class ProbeTest(unittest.TestCase):
    """The probe turns FreeRDP's one vague error into an actionable sentence."""

    def test_open_port(self):
        import socket
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            result = probe.check("127.0.0.1", port, timeout=2.0)
        finally:
            server.close()
        self.assertTrue(result.reachable)
        self.assertIn("open", result.message)

    def test_closed_port(self):
        import socket
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.close()                      # nothing listens here any more
        result = probe.check("127.0.0.1", port, timeout=2.0)
        self.assertFalse(result.reachable)
        self.assertIn("closed", result.message)

    def test_unresolvable_name(self):
        import socket
        name = "urdp-bulunmayan-ad.invalid"
        try:
            socket.getaddrinfo(name, 3389)
        except socket.gaierror:
            pass
        else:                                # a hijacking resolver answered
            self.skipTest("DNS bu adı çözüyor, test anlamsız")
        result = probe.check(name, 3389, timeout=1.0)
        self.assertFalse(result.reachable)
        self.assertIn("could not be resolved", result.message)

    def test_empty_host(self):
        self.assertFalse(probe.check("", 3389).reachable)


class TranslationTest(unittest.TestCase):
    """Locale files must stay in step with the strings in the source."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        root = Path(__file__).resolve().parent.parent
        subprocess.run([sys.executable, "tools/extract_strings.py"],
                       cwd=root, check=True, capture_output=True,
                       env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        cls.keys = set(json.loads((root / "keys.json").read_text("utf-8")))
        cls.root = root

    def _locales(self):
        for path in sorted((self.root / "urdp" / "locale").glob("*.json")):
            yield path.stem, json.loads(path.read_text("utf-8"))

    def test_at_least_one_locale_ships(self):
        self.assertTrue(list(self._locales()))

    def test_no_stale_keys(self):
        for code, table in self._locales():
            stale = sorted(set(table) - self.keys)
            self.assertEqual(stale, [], f"{code}.json has strings the source "
                                        f"no longer uses")

    def test_placeholders_survive_translation(self):
        # A translation that drops or renames a {placeholder} raises KeyError
        # or IndexError at run time, in a message the user only sees when
        # something already went wrong.
        field = re.compile(r"\{([a-zA-Z_][a-zA-Z_0-9]*)[^}]*\}")
        for code, table in self._locales():
            for source, translated in table.items():
                self.assertEqual(
                    sorted(field.findall(source)),
                    sorted(field.findall(translated)),
                    f"{code}.json: placeholders differ for {source!r}")

    def test_nothing_left_untranslated_by_accident(self):
        # Proper nouns (ALSA, TLS, RemoteFX ...) are the same everywhere and
        # are meant to fall through to English.
        allowed = {"ALSA", "SDL", "OSS", "TLS", "HTTP", "RemoteFX",
                   "Progressive", "PulseAudio / PipeWire", "RPC over HTTP",
                   "H.264 AVC420"}
        for code, table in self._locales():
            missing = self.keys - set(table) - allowed
            self.assertEqual(sorted(missing), [],
                             f"{code}.json is missing translations")

    def test_language_switching(self):
        i18n.set_language("tr")
        self.assertEqual(i18n.language(), "tr")
        self.assertEqual(i18n.gettext("Connect"), "Bağlan")
        i18n.set_language("nonexistent")
        self.assertEqual(i18n.language(), "en")
        self.assertEqual(i18n.gettext("Connect"), "Connect")


if __name__ == "__main__":
    unittest.main(verbosity=2)
