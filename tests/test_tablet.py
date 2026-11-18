"""Tests for the tablet screen: sizes, layout, QR codes and the web bridge."""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import ssl
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
    """Stands in for TabletScreen, with its real challenge, wrong-code and
    ticket logic: the code is "goodcode00", VNC is local."""

    key = tablet.TabletScreen.key
    new_nonce = tablet.TabletScreen.new_nonce
    check_proof = tablet.TabletScreen.check_proof
    new_ticket = tablet.TabletScreen.new_ticket
    use_ticket = tablet.TabletScreen.use_ticket
    audio_opened = tablet.TabletScreen.audio_opened
    audio_closed = tablet.TabletScreen.audio_closed
    _audio_out = tablet.TabletScreen._audio_out
    video_opened = tablet.TabletScreen.video_opened
    video_closed = tablet.TabletScreen.video_closed
    _video_out = tablet.TabletScreen._video_out
    mic_opened = tablet.TabletScreen.mic_opened
    mic_closed = tablet.TabletScreen.mic_closed
    mic_in = tablet.TabletScreen.mic_in
    camera_opened = tablet.TabletScreen.camera_opened
    camera_closed = tablet.TabletScreen.camera_closed
    camera_in = tablet.TabletScreen.camera_in

    def __init__(self, vnc_port):
        self.vnc_port = vnc_port
        self.vnc_password = "pw123456"
        self.settings = tablet.Settings(code="goodcode00")
        self.salt = "0011223344556677"
        self._key = None
        self._lock = threading.Lock()
        self._bad_codes = 0
        self._bad_from = {}
        self._nonces = {}
        self._tickets = {}
        self._locked = unittest.mock.Mock()
        self.seen = []
        self.clients = []
        self.has_input = True
        self.has_audio = True
        self._audio = None
        self.has_video = True
        self._video = None
        self.heard = []
        self._mic = unittest.mock.Mock(write=self.heard.append)
        self._mic_bridge = None
        self.has_mic = True
        self.seen_pictures = []
        self._camera = unittest.mock.Mock(write=self.seen_pictures.append)
        self._camera_bridge = None
        self.camera_facing = "user"
        self._video_ready = True
        self._last_frame = b""
        self._epoch = 1
        self.messages = []
        self.inputs = []

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

    def get(self, path, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         headers=headers or {})
        with urllib.request.urlopen(request) as r:
            return r.status, r.headers, r.read()

    def hello(self, code="goodcode00", extra="", nonce=None):
        """The page's side of the challenge; returns (answer, key, nonce)."""
        challenge = json.loads(self.get("/nonce")[2])
        nonce = nonce or challenge["nonce"]
        key = tablet.derive_key(code, challenge["salt"])
        proof = tablet.proof(key, "hello", nonce).hex()
        _, _, body = self.get(f"/hello?n={nonce}&p={proof}{extra}")
        return json.loads(body), key, nonce

    def test_page_and_novnc_are_served_with_a_policy(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"tablet.js", body)
        policy = headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("connect-src 'self';", policy)
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
        with socket.create_connection(("127.0.0.1", self.port), 5) as sock:
            sock.sendall(b"GET /a\0.js HTTP/1.1\r\nHost: x\r\n\r\n")
            self.assertIn(b" 404 ", sock.recv(64))

    def test_hello_proves_the_code_without_sending_it(self):
        challenge = json.loads(self.get("/nonce")[2])
        self.assertEqual(challenge["rounds"], tablet.KEY_ROUNDS)
        self.assertIn("connecting", challenge["strings"])
        answer, _, _ = self.hello("badcode000")
        self.assertEqual((answer["ok"], answer["reason"]), (False, "code"))
        self.assertNotIn("password", answer)
        self.assertNotIn("ticket", answer)
        answer, key, nonce = self.hello(extra="&w=1480&h=924&dpr=2")
        self.assertTrue(answer["ok"])
        # The password is sealed: only the code's key opens it.
        self.assertNotIn("pw123456", json.dumps(answer))
        opened = bytes.fromhex(answer["password"])
        pad = tablet.proof(key, "vnc", nonce)
        self.assertEqual(bytes(a ^ b for a, b in zip(opened, pad)), b"pw123456")
        self.assertEqual(self.owner.seen, [(1480.0, 924.0, 2.0)])
        self.assertTrue(answer["ticket"])

    def test_a_challenge_answers_once(self):
        answer, _, nonce = self.hello()
        self.assertTrue(answer["ok"])
        answer, _, _ = self.hello(nonce=nonce)
        self.assertFalse(answer["ok"])
        answer, _, _ = self.hello(nonce="00" * 16)     # never handed out
        self.assertFalse(answer["ok"])
        self.assertEqual(self.owner._bad_codes, 0)     # not guesses

    def test_an_address_that_guesses_waits(self):
        _, _, body = self.get("/hello?n=x&p=")
        self.assertEqual(self.owner._bad_codes, 0)     # no answer, no guess
        for _ in range(tablet.IP_TRIES):
            self.assertEqual(self.hello("badcode000")[0]["reason"], "code")
        self.assertEqual(self.hello()[0]["reason"], "wait")
        self.owner._bad_from["127.0.0.1"] = (tablet.IP_TRIES, 0.0)
        self.assertTrue(self.hello()[0]["ok"])         # its wait is over
        self.assertNotIn("127.0.0.1", self.owner._bad_from)

    def test_lockout_after_many_wrong_answers(self):
        self.owner._bad_codes = tablet.MAX_BAD_CODES - 1
        self.assertEqual(self.hello("badcode000")[0]["reason"], "code")
        self.owner._locked.emit.assert_called_once()
        self.assertEqual(self.hello()[0]["reason"], "locked")

    def test_other_sites_are_turned_away(self):
        for headers in ({"Sec-Fetch-Site": "cross-site"},
                        {"Origin": "http://evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get("/hello?n=x&p=00", headers)
            self.assertEqual(cm.exception.code, 403)
        self.assertEqual(self.owner._bad_codes, 0)
        status, _, _ = self.get("/nonce", {"Sec-Fetch-Site": "same-origin",
                                            "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_tickets_are_for_one_address_and_one_use(self):
        ticket = self.owner.new_ticket("10.0.0.5")
        self.assertFalse(self.owner.use_ticket(ticket, "ws", "10.0.0.6"))
        self.assertTrue(self.owner.use_ticket(ticket, "ws", "10.0.0.5"))
        self.assertFalse(self.owner.use_ticket(ticket, "ws", "10.0.0.5"))
        self.assertTrue(self.owner.use_ticket(ticket, "input", "10.0.0.5"))
        self.assertFalse(self.owner.use_ticket("made-up", "ws", "10.0.0.5"))

    def test_idle_connections_time_out(self):
        self.assertEqual(tablet._Handler.timeout, 15)
        self.assertEqual(self.server._slots._value, tablet.MAX_CONNECTIONS)

    def _ticket(self):
        return self.hello()[0]["ticket"]

    def _handshake(self, ticket, path="/ws", origin=None):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        extra = f"Origin: {origin}\r\n" if origin else ""
        sock.sendall((f"GET {path}?t={ticket} HTTP/1.1\r\nHost: x\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\n{extra}"
                      "Sec-WebSocket-Protocol: binary\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(1)
        return sock, key, head.decode()

    def test_websocket_refuses_without_a_ticket(self):
        sock, _, head = self._handshake("wrong")
        sock.close()
        self.assertIn(" 403 ", head.splitlines()[0])

    def test_websocket_refuses_another_origin(self):
        sock, _, head = self._handshake(self._ticket(),
                                        origin="http://evil.example")
        sock.close()
        self.assertIn(" 403 ", head.splitlines()[0])

    def test_websocket_bridges_to_vnc(self):
        ticket = self._ticket()
        sock, key, head = self._handshake(ticket, origin="http://x")
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
        again, _, head = self._handshake(ticket)      # used up
        again.close()
        self.assertIn(" 403 ", head.splitlines()[0])

    def test_input_channel_delivers_messages(self):
        sock, _, head = self._handshake(self._ticket(), "/input")
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

    def test_audio_channel_sends_the_sound(self):
        sock, _, head = self._handshake(self._ticket(), "/audio")
        self.assertIn(" 101 ", head.splitlines()[0])
        for _ in range(50):
            if self.owner._audio is not None:
                break
            threading.Event().wait(0.02)
        piece = bytes(range(256)) * 15                  # 3840 bytes
        self.owner._audio_out(piece)
        first, length = sock.recv(2)
        self.assertEqual((first, length), (0x82, 126))
        size = struct.unpack("!H", sock.recv(2))[0]
        data = b""
        while len(data) < size:
            data += sock.recv(size - len(data))
        self.assertEqual(data, piece)
        sock.sendall(bytes([0x88, 0x80]) + b"\0\0\0\0")
        sock.close()
        for _ in range(100):
            if self.owner._audio is None:
                break
            threading.Event().wait(0.02)
        self.assertIsNone(self.owner._audio)
        self.owner._audio_out(piece)                    # nobody listening

    def _frame(self, sock):
        first, length = sock.recv(2)
        if length == 126:
            length = struct.unpack("!H", sock.recv(2))[0]
        data = b""
        while len(data) < length:
            data += sock.recv(length - len(data))
        return first, data

    def test_video_sends_the_newest_frame(self):
        self.owner._video_out(b"old frame")
        sock, _, head = self._handshake(self._ticket(), "/video")
        self.assertIn(" 101 ", head.splitlines()[0])
        self.assertEqual(self._frame(sock), (0x82, b"old frame"))  # at once
        self.owner._video_out(b"new frame")
        self.assertEqual(self._frame(sock), (0x82, b"new frame"))
        self.assertEqual(self.owner.clients[0], "127.0.0.1")       # status
        sock.sendall(bytes([0x88, 0x80]) + b"\0\0\0\0")
        sock.close()

    def test_channels_from_before_new_code_are_turned_away(self):
        bridge = unittest.mock.Mock()
        self.assertIsNotNone(self.owner.video_opened(bridge, 1))
        self.assertIsNotNone(self.owner.audio_opened(bridge, 1))
        self.owner._epoch = 2                       # New Code or Stop
        self.assertIsNone(self.owner.video_opened(bridge, 1))
        self.assertIsNone(self.owner.audio_opened(bridge, 1))
        ticket = self.owner.new_ticket("10.0.0.5")
        self.assertEqual(self.owner.use_ticket(ticket, "video", "10.0.0.5"), 2)
        self.owner._video_ready = False             # picture stopped
        self.assertIsNone(self.owner.video_opened(bridge, 2))

    def test_video_needs_the_fast_picture(self):
        self.owner.has_video = False
        sock, _, head = self._handshake(self._ticket(), "/video")
        sock.close()
        self.assertIn(" 503 ", head.splitlines()[0])

    def test_audio_needs_a_ticket_and_sound(self):
        sock, _, head = self._handshake("wrong", "/audio")
        sock.close()
        self.assertIn(" 403 ", head.splitlines()[0])
        self.owner.has_audio = False
        sock, _, head = self._handshake(self._ticket(), "/audio")
        sock.close()
        self.assertIn(" 503 ", head.splitlines()[0])

    def test_mic_channel_takes_the_sound(self):
        sock, _, head = self._handshake(self._ticket(), "/mic")
        self.assertIn(" 101 ", head.splitlines()[0])
        mask = b"\x05\x06\x07\x08"
        piece = bytes(range(200)) * 4 + bytes(160)      # 960 bytes: 10 ms
        sock.sendall(bytes([0x82, 0xFE]) + struct.pack("!H", len(piece))
                     + mask + tablet._unmask(piece, mask))
        for _ in range(100):
            if self.owner.heard:
                break
            threading.Event().wait(0.02)
        self.assertEqual(self.owner.heard, [piece])
        sock.sendall(bytes([0x88, 0x80]) + mask)
        sock.recv(16)
        sock.close()
        for _ in range(100):
            if self.owner._mic_bridge is None:
                break
            threading.Event().wait(0.02)
        self.assertIsNone(self.owner._mic_bridge)

    def test_mic_needs_a_ticket_and_the_microphone(self):
        sock, _, head = self._handshake("wrong", "/mic")
        sock.close()
        self.assertIn(" 403 ", head.splitlines()[0])
        self.owner.has_mic = False
        sock, _, head = self._handshake(self._ticket(), "/mic")
        sock.close()
        self.assertIn(" 503 ", head.splitlines()[0])

    def test_camera_channel_takes_pictures(self):
        sock, _, head = self._handshake(self._ticket(), "/camera")
        self.assertIn(" 101 ", head.splitlines()[0])
        mask = b"\x0a\x0b\x0c\x0d"
        picture = b"\xff\xd8" + os.urandom(70_000) + b"\xff\xd9"
        sock.sendall(bytes([0x82, 0xFF]) + struct.pack("!Q", len(picture))
                     + mask + tablet._unmask(picture, mask))
        for _ in range(100):
            if self.owner.seen_pictures:
                break
            threading.Event().wait(0.02)
        self.assertEqual(self.owner.seen_pictures, [picture])
        sock.close()

    def test_camera_needs_a_ticket_and_the_camera(self):
        sock, _, head = self._handshake("wrong", "/camera")
        sock.close()
        self.assertIn(" 403 ", head.splitlines()[0])
        self.owner.camera_facing = ""
        sock, _, head = self._handshake(self._ticket(), "/camera")
        sock.close()
        self.assertIn(" 503 ", head.splitlines()[0])

    def test_hello_says_which_camera(self):
        answer = self.hello()[0]
        self.assertEqual((answer["mic"], answer["camera"]), (True, "user"))

    def test_only_the_newest_mic_is_heard(self):
        old, new = unittest.mock.Mock(), unittest.mock.Mock()
        self.assertTrue(self.owner.mic_opened(old, 1))
        self.assertTrue(self.owner.mic_opened(new, 1))
        old.close.assert_called_once()
        self.owner.mic_in(old, b"ab")
        self.owner.mic_in(new, b"cd")
        self.assertEqual(self.owner.heard, [b"cd"])
        self.owner._epoch = 2
        self.assertFalse(self.owner.mic_opened(old, 1))

    def test_input_channel_needs_a_ticket(self):
        sock, _, head = self._handshake("wrong", "/input")
        sock.close()
        self.assertIn(" 403 ", head.splitlines()[0])


class VideoTest(unittest.TestCase):
    def test_frames_are_split_however_they_arrive(self):
        from urdp import tabletvideo

        def part(data):
            return (b"--urdpframe\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: %d\r\n\r\n" % len(data) + data + b"\r\n")

        stream = part(b"\xff\xd8one") + part(b"\xff\xd8two\r\n\r\nxx")
        for step in (1, 7, len(stream)):
            reader = tabletvideo.FrameReader()
            got = []
            for i in range(0, len(stream), step):
                got += reader.feed(stream[i:i + step])
            self.assertEqual(got, [b"\xff\xd8one", b"\xff\xd8two\r\n\r\nxx"])

    def test_a_bad_length_does_not_hang(self):
        from urdp import tabletvideo
        reader = tabletvideo.FrameReader()
        for length in (b"-24", b"0", b"999999999999"):
            self.assertEqual(reader.feed(
                b"--x\r\nContent-Length: " + length + b"\r\n\r\nabc"), [])
            self.assertEqual(reader.buffer, b"")

    def test_newest_frame_only(self):
        latest = tablet._Latest(b"a")
        latest.put(b"b")
        latest.put(b"c")
        self.assertEqual(latest.take(0), b"c")
        self.assertEqual(latest.take(0.01), b"")

    def test_gpu_pipeline_comes_first(self):
        from urdp import tabletvideo
        with unittest.mock.patch.object(tabletvideo, "_has_element",
                                        return_value=True):
            tries = tabletvideo.pipelines(7, 42)
        self.assertEqual(len(tries), 2)
        self.assertIn("vajpegenc", tries[0])
        self.assertIn("jpegenc", tries[1])
        for t in tries:
            self.assertEqual(t[:3], ["pipewiresrc", "fd=7", "path=42"])
            self.assertEqual(t[-3:], ["fdsink", "fd=1", "sync=false"])
        with unittest.mock.patch.object(tabletvideo, "_has_element",
                                        side_effect=lambda e: e != "vajpegenc"):
            self.assertEqual(len(tabletvideo.pipelines(7, 42)), 1)


class VideoStateTest(unittest.TestCase):
    def _screen(self, tmp):
        from PyQt6.QtCore import QCoreApplication
        # Kept: once the application goes, so do the objects made in it.
        self.app = QCoreApplication.instance() or QCoreApplication([])
        return tablet.TabletScreen()

    def test_wrong_screen_forgets_the_choice(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            screen = self._screen(tmp)
            screen.settings.video_token = "tok"
            notices = []
            screen.notice.connect(notices.append)
            screen._server = object()
            screen._video_failed("wrong screen")
            self.assertEqual(tablet.load_settings().video_token, "")
            self.assertIn("URDP-Tablet", notices[-1])
            screen._server = None

    def test_new_code_closes_every_channel(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            screen = self._screen(tmp)
            channels = [unittest.mock.Mock() for _ in range(6)]
            screen._bridges = [channels[0]]
            screen._input_bridge = channels[1]
            screen._audio = (channels[2], None)
            screen._video = (channels[3], None)
            screen._mic_bridge = channels[4]
            screen._camera_bridge = channels[5]
            epoch = screen._epoch
            screen.new_code()
            for channel in channels:
                channel.close.assert_called()
            self.assertEqual((screen._audio, screen._video, screen._mic_bridge,
                              screen._camera_bridge), (None,) * 4)
            self.assertEqual(screen._epoch, epoch + 1)


@unittest.skipUnless(shutil.which("openssl"), "needs openssl")
class TlsTest(unittest.TestCase):
    """The page over HTTPS: the same server, behind tablettls."""

    def setUp(self):
        from urdp import tablettls
        self.tmp = tempfile.TemporaryDirectory()
        self.env = unittest.mock.patch.dict(os.environ,
                                            {"XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()
        self.made = tablettls.context()
        self.assertIsNotNone(self.made)
        self.owner = _Owner(0)
        self.server = tablet._WebServer(0, self.owner, self.made)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = ssl.create_default_context()
        self.client.check_hostname = False
        self.client.verify_mode = ssl.CERT_NONE

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.env.stop()
        self.tmp.cleanup()

    def test_certificate_is_kept_and_its_key_private(self):
        from urdp import tablettls
        folder = Path(self.tmp.name) / "urdp" / "tablet-tls"
        self.assertEqual((folder / "key.pem").stat().st_mode & 0o777, 0o600)
        first = tablettls.fingerprint()
        self.assertEqual(len(first.split(":")), 32)
        self.assertIsNotNone(tablettls.context())
        self.assertEqual(tablettls.fingerprint(), first)    # not made again

    def test_page_comes_over_https(self):
        request = urllib.request.Request(f"https://127.0.0.1:{self.port}/")
        with urllib.request.urlopen(request, context=self.client) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"tablet.js", r.read())

    def test_plain_http_is_sent_on(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock.sendall(b"GET /?x=1 HTTP/1.1\r\nHost: 10.0.0.9:1701\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(1)
        sock.close()
        self.assertIn(b" 301 ", head)
        self.assertIn(b"Location: https://10.0.0.9:1701/?x=1\r\n", head)
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock.sendall(b"GET / HTTP/1.1\r\nHost: a\"b\r\n\r\n")
        self.assertIn(b" 400 ", sock.recv(100))
        sock.close()

    def test_a_tablet_that_stops_reading_gives_its_slot_back(self):
        """It cannot hold a thread for ever: the pump gives up after STALL
        seconds without progress, and the slot comes back with it."""
        from urdp import tablettls
        pumps = lambda: sum(t._target is tablettls._pump              # noqa: E731
                            for t in threading.enumerate()
                            if hasattr(t, "_target"))
        raw = socket.socket()
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        raw.connect(("127.0.0.1", self.port))
        sock = self.client.wrap_socket(raw)
        # Many requests for a big file, never read: replies pile up.
        sock.sendall(b"GET /novnc/core/rfb.js HTTP/1.1\r\nHost: x\r\n\r\n" * 40)
        busy = tablet.MAX_CONNECTIONS - 1
        with unittest.mock.patch.object(tablettls, "STALL", 1), \
                unittest.mock.patch.object(tablettls, "LINGER", 1):
            for _ in range(100):
                if self.server._slots._value == busy:
                    break
                threading.Event().wait(0.05)
            self.assertEqual(self.server._slots._value, busy)   # still held
            for _ in range(200):
                if self.server._slots._value == tablet.MAX_CONNECTIONS:
                    break
                threading.Event().wait(0.05)
        self.assertEqual(self.server._slots._value, tablet.MAX_CONNECTIONS)
        self.assertEqual(pumps(), 0)            # the pump itself is gone
        sock.close()

    def test_a_request_head_must_come_in_time(self):
        with unittest.mock.patch.object(tablet, "HEAD_DEADLINE", 0.5):
            raw = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            sock = self.client.wrap_socket(raw)
            sock.sendall(b"GET / HTTP/1.1\r\n")       # and then a trickle
            started = threading.Event()
            started.wait(1.5)
            try:
                sock.sendall(b"X-Slow: 1\r\n")
                data = sock.recv(100)
            except OSError:
                data = b""
            self.assertEqual(data, b"")                  # dropped
            sock.close()

    def test_websocket_through_tls_both_ways(self):
        """Large frames out while the page talks: the pump must not stall
        or mix them up."""
        with urllib.request.urlopen(
                f"https://127.0.0.1:{self.port}/nonce",
                context=self.client) as r:
            challenge = json.loads(r.read())
        key = tablet.derive_key("goodcode00", challenge["salt"])
        proof = tablet.proof(key, "hello", challenge["nonce"]).hex()
        with urllib.request.urlopen(
                f"https://127.0.0.1:{self.port}/hello?n={challenge['nonce']}"
                f"&p={proof}", context=self.client) as r:
            ticket = json.loads(r.read())["ticket"]
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        sock = self.client.wrap_socket(raw)
        sock.sendall((f"GET /video?t={ticket} HTTP/1.1\r\nHost: x\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(1)
        self.assertIn(b" 101 ", head)
        frames = [os.urandom(700_000) for _ in range(3)]
        got = []

        def read_exact(n):
            data = b""
            while len(data) < n:
                data += sock.recv(n - len(data))
            return data

        for frame in frames:
            self.owner._video_out(frame)
            mask = b"\x09\x08\x07\x06"
            sock.sendall(bytes([0x89, 0x84]) + mask                  # ping
                         + tablet._unmask(b"ping", mask))
            while True:
                first, length = read_exact(2)
                if length == 127:
                    length = struct.unpack("!Q", read_exact(8))[0]
                elif length == 126:
                    length = struct.unpack("!H", read_exact(2))[0]
                data = read_exact(length)
                if first == 0x82:
                    got.append(data)
                    break
                self.assertEqual((first, data), (0x8A, b"ping"))    # pong
        self.assertEqual(got, frames)
        sock.close()


class RecoveryTest(unittest.TestCase):
    """A URDP that crashed left its speaker or microphone as the default."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = unittest.mock.patch.dict(os.environ,
                                            {"XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _undo(self, pid, default="urdp_tablet_mic"):
        from urdp import tabletaudio
        tabletaudio.remember("source", "built-in")
        note = tabletaudio._note("source")
        note.write_text(f"built-in\n{pid}\n")
        calls = []

        def pactl(*args):
            calls.append(args)
            return default if args[0] == "get-default-source" else ""

        with unittest.mock.patch.object(tabletaudio, "_pactl", pactl):
            tabletaudio.undo("source", "urdp_tablet_mic", ["8", "7"])
        return calls, note.exists()

    def test_the_default_comes_back_after_a_crash(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        calls, kept = self._undo(dead.pid)
        self.assertEqual(calls, [("get-default-source",),
                                 ("set-default-source", "built-in"),
                                 ("unload-module", "8"),
                                 ("unload-module", "7")])
        self.assertFalse(kept)

    def test_a_running_urdp_is_left_alone(self):
        alive = subprocess.Popen(["sleep", "30"])
        try:
            calls, kept = self._undo(alive.pid)
        finally:
            alive.kill()
            alive.wait()
        self.assertEqual(calls, [])
        self.assertTrue(kept)

    def test_a_default_the_user_changed_is_kept(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        calls, _kept = self._undo(dead.pid, default="headset")
        self.assertNotIn(("set-default-source", "built-in"), calls)


class MicrophoneTest(unittest.TestCase):
    def test_finds_its_own_modules_source_first(self):
        from urdp import tabletmic
        listing = ("7\tmodule-null-sink\tsink_name=urdp_tablet_mic_in rate=48000\t\n"
                   "8\tmodule-remap-source\tsource_name=urdp_tablet_mic master=x\t\n"
                   "9\tmodule-null-sink\tsink_name=urdp_tablet\t\n"
                   "10\tmodule-remap-source\tsource_name=other\t\n")
        with unittest.mock.patch.object(tabletmic, "_pactl",
                                        return_value=listing):
            self.assertEqual(tabletmic._our_modules(), ["8", "7"])

    def test_gives_the_default_input_back(self):
        from urdp import tabletmic
        calls = []

        def pactl(*args):
            calls.append(args)
            return {"get-default-source": "urdp_tablet_mic"}.get(args[0], "")

        mic = tabletmic.Microphone()
        mic._modules, mic._previous = ["8", "7"], "built-in"
        with unittest.mock.patch.object(tabletmic, "_pactl", pactl):
            mic.stop()
        self.assertEqual(calls, [("get-default-source",),
                                 ("set-default-source", "built-in"),
                                 ("unload-module", "8"),
                                 ("unload-module", "7")])

    def test_a_stuck_player_never_blocks_the_tablet(self):
        from urdp import tabletmic
        mic = tabletmic.Microphone()
        # A pacat that never reads: the pipe fills, then pieces are dropped.
        process = subprocess.Popen(["sleep", "30"], stdin=subprocess.PIPE)
        os.set_blocking(process.stdin.fileno(), False)
        mic._process = process
        try:
            piece = b"\1\2" * 480
            done = threading.Event()

            def flood():
                for _ in range(2000):                   # 20 s of sound
                    mic.write(piece)
                done.set()

            threading.Thread(target=flood, daemon=True).start()
            self.assertTrue(done.wait(5))
            mic.write(b"\1")                             # odd: ignored
            mic.write(bytes(tabletmic.MAX_PIECE + 2))      # too big: ignored
        finally:
            process.kill()
            process.wait()
            mic._process = None


def _jpeg(width, height, padding):
    """Enough of a JPEG for the checks: SOI, an APP0, a SOF0, EOI."""
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = (b"\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big")
           + width.to_bytes(2, "big") + b"\x03" + bytes(9))
    return b"\xff\xd8" + app0 + sof + bytes(padding) + b"\xff\xd9"


class CameraTest(unittest.TestCase):
    def test_picture_size_is_read_from_the_frame_header(self):
        from urdp import tabletcamera
        self.assertEqual(tabletcamera.picture_size(_jpeg(960, 1280, 5)),
                         (960, 1280))
        self.assertEqual(tabletcamera.picture_size(b"\xff\xd8\xff\xda\x00\x02"),
                         (0, 0))
        self.assertEqual(tabletcamera.picture_size(b"\xff\xd8\x00\x00"), (0, 0))
        self.assertEqual(tabletcamera.picture_size(b"\xff\xd8\xff\xe0\x00\x00"),
                         (0, 0))

    def test_pipewire_without_a_loopback_device(self):
        from urdp import tabletcamera
        args = tabletcamera.pipeline("")
        self.assertIn("pipewiresink", args)
        # One argument, quoted for gst-launch, keeps the name with spaces.
        self.assertTrue(args[-1].startswith('stream-properties="'))
        self.assertIn('node.description=\\"URDP Tablet Camera\\"', args[-1])
        args = tabletcamera.pipeline("/dev/video7")
        self.assertEqual(args[-3:], ["v4l2sink", "device=/dev/video7",
                                     "sync=false"])

    def test_only_loopback_devices_are_used(self):
        from urdp import tabletcamera
        with tempfile.TemporaryDirectory() as tmp:
            for name, where, label in (
                    ("video0", "pci0000:00/usb3/video4linux", "URDP Tablet Camera"),
                    ("video8", "virtual/video4linux", "OBS Virtual Camera"),
                    ("video9", "virtual/video4linux", "URDP Tablet Camera")):
                target = Path(tmp, "devices", where, name)
                target.mkdir(parents=True)
                (target / "name").write_text(label + "\n")
                os.symlink(target, Path(tmp, name))
            with unittest.mock.patch.object(
                    tabletcamera.glob, "glob",
                    return_value=[str(Path(tmp, n))
                                  for n in ("video0", "video8", "video9")]), \
                    unittest.mock.patch.object(tabletcamera.os, "access",
                                               return_value=True) as access:
                self.assertEqual(tabletcamera.loopback(), "/dev/video9")
                access.assert_called_once_with("/dev/video9", os.W_OK)

    def test_pictures_go_whole_or_not_at_all(self):
        from urdp import tabletcamera
        camera = tabletcamera.Camera()
        process = subprocess.Popen(["sleep", "30"], stdin=subprocess.PIPE)
        fd = process.stdin.fileno()
        os.set_blocking(fd, False)
        camera._process = process
        try:
            picture = _jpeg(1280, 720, 100_000)
            for bad in (b"not a jpeg", b"\xff\xd8" + bytes(10),
                        b"\xff\xd8" + bytes(100) + b"\xff\xd9",       # no size
                        _jpeg(65000, 65000, 10),                       # bomb
                        _jpeg(1280, 720, tabletcamera.MAX_PICTURE)):
                camera.write(bad)
            self.assertEqual(self._waiting(fd), 0)
            done = threading.Event()

            def flood():
                for _ in range(50):                  # nobody reads: no block
                    camera.write(picture)
                done.set()

            threading.Thread(target=flood, daemon=True).start()
            self.assertTrue(done.wait(5))
            waiting = self._waiting(fd)
            self.assertEqual(waiting % len(picture), 0)           # whole ones
            self.assertLessEqual(waiting, tabletcamera.BACKLOG * len(picture))
        finally:
            process.kill()
            process.wait()
            camera._process = None

    @staticmethod
    def _waiting(fd):
        import fcntl
        import termios
        return int.from_bytes(fcntl.ioctl(fd, termios.FIONREAD, b"\0" * 4),
                              "little")

    def test_camera_follows_the_setting(self):
        from PyQt6.QtCore import QCoreApplication
        from urdp import tabletcamera
        self.app = QCoreApplication.instance() or QCoreApplication([])

        class FakeCamera:
            kind = "pipewire"

            def start(self):
                return True

            def stop(self):
                pass

        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}), \
                unittest.mock.patch.object(tabletcamera, "Camera", FakeCamera):
            screen = tablet.TabletScreen()
            screen._server = unittest.mock.Mock(tls=object())   # HTTPS on
            self.assertEqual(screen.camera_facing, "")
            screen.set_camera("back")
            self.assertEqual((screen.camera_facing, screen.camera_kind),
                             ("environment", "pipewire"))
            old = unittest.mock.Mock()
            screen._camera_bridge = old
            screen.set_camera("front")                # the page reconnects
            old.close.assert_called_once()
            self.assertEqual(screen.camera_facing, "user")
            self.assertEqual(tablet.load_settings().camera, "front")
            screen.set_camera("off")
            self.assertEqual((screen.camera_facing, screen.camera_kind), ("", ""))
            screen._server = unittest.mock.Mock(tls=None)       # plain HTTP
            screen.set_camera("back")
            self.assertEqual(screen.camera_facing, "")
            screen._server = None


class SpeakerTest(unittest.TestCase):
    def test_finds_its_own_sinks_only(self):
        from urdp import tabletaudio
        listing = ("12\tmodule-null-sink\tsink_name=urdp_tablet rate=48000\t\n"
                   "13\tmodule-null-sink\tsink_name=other\t\n"
                   "14\tmodule-loopback\tsink_name=urdp_tablet\t\n")
        with unittest.mock.patch.object(tabletaudio, "_pactl",
                                        return_value=listing):
            self.assertEqual(tabletaudio._our_modules(), ["12"])

    def test_gives_the_default_output_back(self):
        from urdp import tabletaudio
        calls = []

        def pactl(*args):
            calls.append(args)
            return {"get-default-sink": "urdp_tablet"}.get(args[0], "")

        speaker = tabletaudio.Speaker(lambda piece: None)
        speaker._module, speaker._previous = "42", "speakers"
        with unittest.mock.patch.object(tabletaudio, "_pactl", pactl):
            speaker.stop()
        self.assertEqual(calls, [("get-default-sink",),
                                 ("set-default-sink", "speakers"),
                                 ("unload-module", "42")])

    def test_silence_is_not_sent(self):
        from urdp import tabletaudio
        got = []
        speaker = tabletaudio.Speaker(got.append)
        sound = b"\1\2" * (tabletaudio.CHUNK // 2)
        data = bytes(tabletaudio.CHUNK) + sound + sound[:100]
        process = subprocess.Popen(["cat"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE)
        process.stdin.write(data)
        process.stdin.close()
        speaker._read(process)
        process.wait()
        self.assertEqual(got, [sound])


class SoundSwitchTest(unittest.TestCase):
    def test_sound_moves_while_sharing(self):
        from PyQt6.QtCore import QCoreApplication
        from urdp import tabletaudio
        _app = QCoreApplication.instance() or QCoreApplication([])
        started, stopped = [], []

        class FakeSpeaker:
            def __init__(self, listener):
                pass

            def start(self):
                started.append(1)
                return True

            def stop(self):
                stopped.append(1)

        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}), \
                unittest.mock.patch.object(tabletaudio, "Speaker", FakeSpeaker):
            screen = tablet.TabletScreen()
            screen.settings.sound = "computer"
            screen._server = object()                    # "running"
            bridge = unittest.mock.Mock()
            screen._bridges = [bridge]
            screen.set_sound("tablet")
            self.assertTrue(screen.has_audio)
            bridge.close.assert_called_once()            # page reconnects
            self.assertEqual(tablet.load_settings().sound, "tablet")
            screen.set_sound("computer")
            self.assertFalse(screen.has_audio)
            self.assertEqual((started, stopped), ([1], [1]))
            screen._server = None


class _FakeInput:
    def __init__(self):
        self.calls = []

    def touch_event(self, *a):
        self.calls.append(("touch", *a))

    def pen_event(self, *a):
        self.calls.append(("pen", *a))

    def touch_as_mouse(self, *a):
        self.calls.append(("mouse-touch", *a))

    def pen_as_mouse(self, *a):
        self.calls.append(("mouse-pen", *a))

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
        screen.settings.input = "mouse"
        screen.input_message(b'["t","d",3,0.25,0.75]')
        screen.input_message(b'["p","h",0.5,0.5,0,0,0,1,0]')
        self.assertEqual(fake.calls, [("mouse-touch", "down", 3, 0.25, 0.75),
                                      ("mouse-pen", "hover", 0.5, 0.5, True)])
        fake.calls.clear()
        screen.settings.input = "tablet"
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

    def test_hostile_messages_are_dropped_quietly(self):
        from PyQt6.QtCore import QCoreApplication
        _app = QCoreApplication.instance() or QCoreApplication([])
        screen = tablet.TabletScreen()
        fake = _FakeInput()
        screen._input = fake
        for payload in (b'["t","d",1,Infinity,0]', b'["w",1e999,0]',
                        b'["p","h",NaN,0,0,0,0,0,0]',
                        b"[" * 100000 + b"]" * 100000):
            screen.input_message(payload)
        self.assertEqual(fake.calls, [])


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


class MouseModeTest(unittest.TestCase):
    def setUp(self):
        from urdp import uinput
        self.u = uinput
        self.events = []
        events = self.events

        class Recorder:
            def __init__(self):
                self.frame = []

            def emit(self, kind, code, value):
                self.frame.append((kind, code, value))

            def sync(self):
                events.append(self.frame)
                self.frame = []

        dev = uinput.TabletInput.__new__(uinput.TabletInput)
        dev.wheel = Recorder()
        dev._area = (0.5, 0.0, 0.5, 1.0)        # right half of the desktop
        dev._buttons = 0
        dev._fingers = {}
        dev._finger_pressed = dev._scrolling = dev._scrolled = False
        dev._scroll_rest = [0.0, 0.0]
        self.dev = dev

    def buttons(self):
        u = self.u
        return [(c, v) for f in self.events for k, c, v in f
                if k == u.EV_KEY]

    def test_mouse_stays_on_the_tablet_screen(self):
        self.dev.pen_as_mouse("hover", -3.0, 0.5, False)
        x = [v for k, c, v in self.events[0] if c == self.u.ABS_X][0]
        self.assertEqual(x, round(0.5 * self.u.SPAN))     # its left edge

    def test_fingers_are_limited(self):
        for ident in range(1000):
            self.dev.touch_as_mouse("down", ident, 0.5, 0.5)
        self.assertEqual(len(self.dev._fingers), self.u.SLOTS)

    def test_area_maps_to_the_desktop(self):
        self.dev.pen_as_mouse("hover", 0.5, 0.5, False)
        x = [v for k, c, v in self.events[0] if c == self.u.ABS_X][0]
        self.assertEqual(x, round(0.75 * self.u.SPAN))

    def test_pen_tip_left_and_barrel_right(self):
        u = self.u
        self.dev.pen_as_mouse("down", 0.1, 0.1, False)
        self.dev.pen_as_mouse("up", 0.1, 0.1, False)
        self.dev.pen_as_mouse("down", 0.1, 0.1, True)
        self.dev.pen_as_mouse("up", 0.1, 0.1, False)
        self.assertEqual(self.buttons(), [(u.BTN_LEFT, 1), (u.BTN_LEFT, 0),
                                          (u.BTN_RIGHT, 1), (u.BTN_RIGHT, 0)])

    def test_tap_clicks_and_drag_drags(self):
        u = self.u
        t = self.dev.touch_as_mouse
        t("down", 1, 0.2, 0.2)
        t("up", 1, 0.2, 0.2)
        self.assertEqual(self.buttons(), [(u.BTN_LEFT, 1), (u.BTN_LEFT, 0)])
        self.events.clear()
        t("down", 1, 0.2, 0.2)
        t("move", 1, 0.3, 0.3)
        self.assertEqual(self.buttons(), [(u.BTN_LEFT, 1)])
        t("up", 1, 0.3, 0.3)
        self.assertEqual(self.buttons()[-1], (u.BTN_LEFT, 0))

    def test_two_fingers_scroll_or_right_click(self):
        u = self.u
        t = self.dev.touch_as_mouse
        t("down", 1, 0.5, 0.5)
        t("down", 2, 0.6, 0.5)
        t("move", 1, 0.5, 0.6)
        t("move", 2, 0.6, 0.6)
        t("up", 1, 0.5, 0.6)
        t("up", 2, 0.6, 0.6)
        wheel = [v for f in self.events for k, c, v in f
                 if c == u.REL_WHEEL_HI_RES]
        self.assertTrue(wheel and all(v > 0 for v in wheel))   # hand down
        self.assertEqual(self.buttons(), [])                   # no clicks
        self.events.clear()
        t("down", 1, 0.5, 0.5)
        t("down", 2, 0.6, 0.5)
        t("up", 1, 0.5, 0.5)
        t("up", 2, 0.6, 0.5)
        self.assertEqual(self.buttons(), [(u.BTN_RIGHT, 1), (u.BTN_RIGHT, 0)])


class KeyboardTest(unittest.TestCase):
    def _recorder(self):
        from urdp import uinput
        sent = []

        class Recorder:
            def emit(self, kind, code, value):
                if kind == uinput.EV_KEY:
                    sent.append((code, value))

            def sync(self):
                pass

        board = uinput.TabletInput.__new__(uinput.TabletInput)
        board.keyboard = Recorder()
        board._pressed = set()
        return board, sent

    def test_text_uses_the_layout_with_modifiers(self):
        import time
        board, sent = self._recorder()
        board._keymap = {"a": (30, ()), "A": (30, (42,)), "@": (16, (100,))}
        board._keymap_at = time.monotonic()
        board.type_text("aA@\n€")
        self.assertEqual(sent, [
            (30, 1), (30, 0),
            (42, 1), (30, 1), (30, 0), (42, 0),
            (100, 1), (16, 1), (16, 0), (100, 0),
            (28, 1), (28, 0)])              # Enter; € is not on this layout

    def test_keys_are_not_pressed_twice_and_get_released(self):
        board, sent = self._recorder()
        board.key(29, True)
        board.key(29, True)
        board.key(999, True)
        for code in (116, 142, 205, 247):         # power, sleep, suspend, radio
            board.key(code, True)
        board._slots = {}
        board.pen = type("P", (), {"emit": lambda *a: None,
                                   "sync": lambda *a: None})()
        board._pen_in = board._pen_down = board._barrel = False
        board._rubber = False
        board.release_all()
        self.assertEqual(sent, [(29, 1), (29, 0)])

    def test_turkish_layout_from_xkbcommon(self):
        from urdp import keymap
        table = keymap.build("tr")
        if not table:
            self.skipTest("libxkbcommon not available")
        self.assertEqual(table["ı"], (23, ()))
        self.assertEqual(table["İ"], (40, (42,)))
        self.assertEqual(table["ş"], (39, ()))
        self.assertEqual(table["/"], (8, (42,)))       # Shift+7, not keypad
        self.assertEqual(table["@"], (16, (100,)))     # AltGr+Q


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
