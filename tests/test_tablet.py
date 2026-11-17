"""Tests for the tablet screen: sizes, layout, QR codes and the web bridge."""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urdp import qr, tablet                              # noqa: E402


class GeometryTest(unittest.TestCase):
    def test_sharp_uses_device_pixels_and_scale(self):
        # Galaxy Tab S9 Ultra in landscape: 1480 x 924 CSS px at ratio 2.
        self.assertEqual(tablet.geometry("sharp", 1480, 924, 2.0),
                         (2960, 1848, 2.0))

    def test_light_uses_css_pixels(self):
        self.assertEqual(tablet.geometry("light", 1480, 924, 2.0),
                         (1480, 924, 1.0))

    def test_fixed_size_ignores_tablet(self):
        self.assertEqual(tablet.geometry("1920x1200", 1480, 924, 2.0),
                         (1920, 1200, 1.0))

    def test_unknown_tablet_gets_default(self):
        self.assertEqual(tablet.geometry("sharp", 0, 0, 0),
                         (*tablet.DEFAULT_SIZE, 1.0))

    def test_scale_is_rounded_and_clamped(self):
        self.assertEqual(tablet.geometry("sharp", 1000, 600, 2.63)[2], 2.75)
        self.assertEqual(tablet.geometry("sharp", 1000, 600, 0.5)[2], 1.0)
        self.assertEqual(tablet.geometry("sharp", 1000, 600, 4.0)[2], 3.0)


def _output(name, x, y, w, h, scale=1, rotation=1):
    return {"name": name, "pos": {"x": x, "y": y}, "scale": scale,
            "rotation": rotation, "enabled": True, "connected": True,
            "currentModeId": "1",
            "modes": [{"id": "1", "size": {"width": w, "height": h}}]}


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.outputs = [_output("eDP-1", 0, 0, 1920, 1080),
                        _output("V", 1920, 0, 1480, 924)]

    def test_right(self):
        self.assertEqual(tablet.layout(self.outputs, "V", 1480, 924, "right"),
                         {"V": (1920, 0), "eDP-1": (0, 0)})

    def test_left_moves_the_others(self):
        self.assertEqual(tablet.layout(self.outputs, "V", 1480, 924, "left"),
                         {"V": (0, 0), "eDP-1": (1480, 0)})

    def test_above_and_below(self):
        self.assertEqual(tablet.layout(self.outputs, "V", 1480, 924, "above"),
                         {"V": (0, 0), "eDP-1": (0, 924)})
        self.assertEqual(tablet.layout(self.outputs, "V", 1480, 924, "below"),
                         {"V": (0, 1080), "eDP-1": (0, 0)})

    def test_scaled_and_rotated_screens_use_logical_size(self):
        outputs = [_output("A", 0, 0, 3840, 2160, scale=2),
                   _output("B", 1920, 0, 1920, 1080, rotation=2),
                   _output("V", 0, 0, 100, 100)]
        # A is 1920 wide logically, B is 1080 wide after rotation.
        self.assertEqual(tablet.layout(outputs, "V", 100, 100, "right"),
                         {"V": (3000, 0), "A": (0, 0), "B": (1920, 0)})

    def test_a_gap_left_by_kwin_is_closed(self):
        outputs = [_output("eDP-1", 2560, 0, 1920, 1080),
                   _output("V", 4480, 0, 1692, 1056)]
        self.assertEqual(tablet.layout(outputs, "V", 1692, 1056, "right"),
                         {"V": (1920, 0), "eDP-1": (0, 0)})


class SettingsTest(unittest.TestCase):
    def test_bad_values_are_replaced(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": tmp}):
            path = tablet.settings_path()
            path.write_text(json.dumps({"port": 80, "position": "inside",
                                        "size": "huge", "code": "x"}))
            s = tablet.load_settings()
            self.assertEqual((s.port, s.position, s.size),
                             (tablet.DEFAULT_PORT, "right", "sharp"))
            self.assertEqual(len(s.code), 10)
            tablet.save_settings(s)
            self.assertEqual(path.stat().st_mode & 0o077, 0)
            self.assertEqual(tablet.load_settings().code, s.code)


class QrTest(unittest.TestCase):
    def test_sizes_and_finder_patterns(self):
        for text, size in (("A", 21), ("x" * 40, 29), ("x" * 200, 57)):
            modules = qr.encode(text)
            self.assertEqual(len(modules), size)
            for x0, y0 in ((0, 0), (size - 7, 0), (0, size - 7)):
                ring = [modules[y0][x0 + i] for i in range(7)]
                self.assertEqual(ring, [True] * 7)
                self.assertFalse(modules[y0 + 1][x0 + 1])
                self.assertTrue(modules[y0 + 3][x0 + 3])

    def test_too_long(self):
        with self.assertRaises(ValueError):
            qr.encode("x" * 300)

    @unittest.skipUnless(shutil.which("zbarimg"), "zbarimg not installed")
    def test_a_reader_decodes_it(self):
        import zlib
        text = "http://192.168.1.20:1701/#k=abcdefghjk"
        modules = qr.encode(text)
        n = len(modules) + 8
        scale = 4
        rows = []
        for y in range(n * scale):
            yy = y // scale - 4
            rows.append(b"\0" + bytes(
                0 if 0 <= yy < len(modules) and 0 <= x // scale - 4 < len(modules)
                and modules[yy][x // scale - 4] else 255
                for x in range(n * scale)))

        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data)))

        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", n * scale, n * scale,
                                            8, 0, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"".join(rows)))
               + chunk(b"IEND", b""))
        with tempfile.NamedTemporaryFile(suffix=".png") as fh:
            fh.write(png)
            fh.flush()
            out = subprocess.run(["zbarimg", "-q", "--raw", fh.name],
                                 capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), text)


class WebSocketTest(unittest.TestCase):
    def test_accept_key_matches_rfc_6455(self):
        self.assertEqual(tablet._accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_unmask_round_trip(self):
        key = b"\x01\x02\x03\x04"
        data = bytes(range(37))
        self.assertEqual(tablet._unmask(tablet._unmask(data, key), key), data)
        self.assertEqual(tablet._unmask(b"\x01\x02", key), b"\x00\x00")

    def test_frame_lengths(self):
        self.assertEqual(tablet._frame(2, b"ab")[:2], b"\x82\x02")
        self.assertEqual(tablet._frame(2, b"x" * 300)[:4],
                         b"\x82\x7e\x01\x2c")
        self.assertEqual(tablet._frame(2, b"x" * 70000)[1], 127)


class _Owner:
    """Stands in for TabletScreen: the code is "goodcode00", VNC is local."""

    def __init__(self, vnc_port):
        self.vnc_port = vnc_port
        self.vnc_password = "pw123456"
        self.seen = []
        self.clients = []
        self.bad = 0
        self.has_input = True
        self.messages = []
        self.inputs = []

    def check_code(self, given):
        if self.bad >= 3:
            return None
        if given == "goodcode00":
            return True
        self.bad += 1
        return False

    def tablet_seen(self, w, h, ratio):
        self.seen.append((w, h, ratio))
        return False

    def bridge_opened(self, bridge, address):
        self.clients.append(address)

    def bridge_closed(self, bridge):
        self.clients.append(None)

    def input_opened(self, bridge):
        self.inputs.append("open")

    def input_closed(self, bridge):
        self.inputs.append("closed")

    def input_message(self, payload):
        self.messages.append(payload)


class ServerTest(unittest.TestCase):
    def setUp(self):
        # A fake VNC server that echoes whatever it gets, upper-cased.
        self.vnc = socket.socket()
        self.vnc.bind(("127.0.0.1", 0))
        self.vnc.listen(1)

        def echo():
            conn, _ = self.vnc.accept()
            with conn:
                while data := conn.recv(4096):
                    conn.sendall(data.upper())

        threading.Thread(target=echo, daemon=True).start()
        self.owner = _Owner(self.vnc.getsockname()[1])
        self.server = tablet._WebServer(0, self.owner)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.vnc.close()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.headers, r.read()

    def test_page_and_novnc_are_served_with_a_policy(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"tablet.js", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        status, _, body = self.get("/novnc/core/rfb.js")
        self.assertIn(b"export default class RFB", body)
        status, headers, body = self.get("/keepawake.mp4")
        self.assertEqual(headers["Content-Type"], "video/mp4")
        self.assertEqual(body[4:8], b"ftyp")

    def test_no_files_outside_the_web_folder(self):
        for path in ("/../tablet.py", "/novnc/../../tablet.py",
                     "/%2e%2e/tablet.py", "/novnc/LICENSE.txt"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404)

    def test_hello_needs_the_code(self):
        _, _, body = self.get("/hello?k=wrong")
        answer = json.loads(body)
        self.assertFalse(answer["ok"])
        self.assertNotIn("password", answer)
        self.assertIn("connecting", answer["strings"])
        _, _, body = self.get("/hello?k=goodcode00&w=1480&h=924&dpr=2")
        answer = json.loads(body)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["password"], "pw123456")
        self.assertEqual(self.owner.seen, [(1480.0, 924.0, 2.0)])

    def test_lockout(self):
        for _ in range(3):
            self.get("/hello?k=nope")
        _, _, body = self.get("/hello?k=goodcode00")
        self.assertEqual(json.loads(body)["reason"], "locked")

    def _handshake(self, code, path="/ws"):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {path}?k={code} HTTP/1.1\r\nHost: x\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\n"
                      "Sec-WebSocket-Protocol: binary\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(1)
        return sock, key, head.decode()

    def test_websocket_refuses_a_wrong_code(self):
        sock, _, head = self._handshake("wrong")
        sock.close()
        self.assertIn(" 403 ", head.splitlines()[0])

    def test_websocket_bridges_to_vnc(self):
        sock, key, head = self._handshake("goodcode00")
        self.assertIn(" 101 ", head.splitlines()[0])
        self.assertIn(tablet._accept_key(key), head)
        self.assertIn("Sec-WebSocket-Protocol: binary", head)
        mask = b"\x11\x22\x33\x44"
        payload = b"hello vnc"
        sock.sendall(bytes([0x82, 0x80 | len(payload)]) + mask
                     + tablet._unmask(payload, mask))
        first, length = sock.recv(2)
        data = b""
        while len(data) < length:
            data += sock.recv(length - len(data))
        self.assertEqual((first, data), (0x82, b"HELLO VNC"))
        sock.sendall(bytes([0x88, 0x80]) + mask)      # close
        sock.close()
        self.assertEqual(self.owner.clients[0], "127.0.0.1")


    def test_input_channel_delivers_messages(self):
        sock, _, head = self._handshake("goodcode00", "/input")
        self.assertIn(" 101 ", head.splitlines()[0])
        mask = b"\x01\x02\x03\x04"
        for text in (b'["t","d",1,0.5,0.5]', b'["p","h",0.1,0.2,0,0,0,0,0]'):
            sock.sendall(bytes([0x81, 0x80 | len(text)]) + mask
                         + tablet._unmask(text, mask))
        sock.sendall(bytes([0x88, 0x80]) + mask)
        sock.recv(16)
        sock.close()
        for _ in range(50):
            if "closed" in self.owner.inputs:
                break
            threading.Event().wait(0.02)
        self.assertEqual(self.owner.messages,
                         [b'["t","d",1,0.5,0.5]', b'["p","h",0.1,0.2,0,0,0,0,0]'])
        self.assertEqual(self.owner.inputs, ["open", "closed"])

    def test_input_channel_needs_the_code(self):
        sock, _, head = self._handshake("wrong", "/input")
        sock.close()
        self.assertIn(" 403 ", head.splitlines()[0])


class _FakeInput:
    def __init__(self):
        self.calls = []

    def touch_event(self, *a):
        self.calls.append(("touch", *a))

    def pen_event(self, *a):
        self.calls.append(("pen", *a))

    def scroll(self, *a):
        self.calls.append(("scroll", *a))

    def release_all(self):
        self.calls.append(("release",))


class InputMessageTest(unittest.TestCase):
    def test_messages_reach_the_devices(self):
        from PyQt6.QtCore import QCoreApplication
        _app = QCoreApplication.instance() or QCoreApplication([])
        screen = tablet.TabletScreen()
        fake = _FakeInput()
        screen._input = fake
        screen.input_message(b'["t","d",3,0.25,0.75]')
        screen.input_message(b'["p","d",0.5,0.5,0.8,10,-5,1,0]')
        screen.input_message(b'["w",0,-200]')
        screen.input_message(b'not json')
        screen.input_message(b'["t","x",1,0,0]')
        self.assertEqual(fake.calls, [
            ("touch", "down", 3, 0.25, 0.75),
            ("pen", "down", 0.5, 0.5, 0.8, 10.0, -5.0, True, False),
            ("scroll", 0.0, -50.0)])
        screen.input_closed(None)
        self.assertEqual(fake.calls[-1], ("release",))


class UinputTest(unittest.TestCase):
    def _ioc(self, direction, number, size):
        return (direction << 30) | (size << 16) | (ord("U") << 8) | number

    def test_ioctl_numbers_match_the_kernel_structs(self):
        from urdp import uinput
        # struct uinput_setup: input_id (4 x u16) + name[80] + u32
        self.assertEqual(uinput.UI_DEV_SETUP, self._ioc(1, 3, 8 + 80 + 4))
        # struct uinput_abs_setup: u16 code, padding, input_absinfo (6 x s32)
        self.assertEqual(uinput.UI_ABS_SETUP, self._ioc(1, 4, 4 + 24))
        self.assertEqual(uinput.UI_SET_EVBIT, self._ioc(1, 100, 4))
        self.assertEqual(uinput.UI_SET_PROPBIT, self._ioc(1, 110, 4))
        self.assertEqual(uinput._ui_get_sysname(64), self._ioc(2, 44, 64))
        self.assertEqual(struct.calcsize("<qqHHi"), 24)    # input_event

    def test_pen_barrel_is_its_own_frame(self):
        from urdp import uinput
        frames = []

        class Recorder:
            def __init__(self):
                self.events = []

            def emit(self, kind, code, value):
                self.events.append((kind, code, value))

            def sync(self):
                frames.append(self.events)
                self.events = []

        pen = uinput.TabletInput.__new__(uinput.TabletInput)
        pen.pen = Recorder()
        pen._pen_in = pen._pen_down = pen._rubber = pen._barrel = False
        pen.pen_event("down", 0.5, 0.5, 0.7, 0, 0, True, False)
        pen.pen_event("up", 0.5, 0.5, 0, 0, 0, False, False)
        keys = [[(c, v) for k, c, v in f if k == uinput.EV_KEY] for f in frames]
        barrel, tip = uinput.BARREL, uinput.BTN_TOUCH
        # Frame 0 brings the pen in with the button held, before the tip.
        self.assertIn((barrel, 1), keys[0])
        self.assertNotIn((tip, 1), keys[0])
        self.assertIn((tip, 1), keys[1])
        self.assertIn((tip, 0), keys[2])
        self.assertEqual(keys[3], [(barrel, 0)])


class RouteTest(unittest.TestCase):
    def test_reports_a_vpn_that_took_the_lan(self):
        address = tablet.Address("wlan0", "192.168.1.20", 24)
        reply = json.dumps([{"dst": "192.168.1.1", "dev": "tailscale0"}])
        with unittest.mock.patch.object(
                tablet.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, reply, "")
        ) as run:
            self.assertEqual(tablet.misrouted(address, 1701), "tailscale0")
        argv = run.call_args.args[0]
        self.assertEqual(argv[5:8], ["192.168.1.1", "from", "192.168.1.20"])
        self.assertIn("1701", argv)

    def test_quiet_when_the_lan_is_direct(self):
        address = tablet.Address("wlan0", "192.168.1.20", 24)
        reply = json.dumps([{"dst": "192.168.1.1", "dev": "wlan0"}])
        with unittest.mock.patch.object(
                tablet.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, reply, "")):
            self.assertEqual(tablet.misrouted(address, 1701), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
