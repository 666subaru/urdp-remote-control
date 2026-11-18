"""Virtual input devices for the tablet screen, through ``/dev/uinput``.

The tablet's touches and pen strokes become a real touchscreen and a real
pen tablet on this computer, each tied to the virtual screen by KWin's
``outputName`` device setting.  KDE then treats them like built-in hardware:
touch scrolling works in applications, the pen keeps its pressure and tilt,
and the cursor follows the pen while it hovers.

Plain ``fcntl``/``struct``, so nothing has to be installed; the device node
must be writable, which logind's ``uaccess`` grants to the seat's user on
most distributions.
"""

from __future__ import annotations

import fcntl
import os
import struct
import subprocess
import time
from pathlib import Path

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_TOOL_PEN, BTN_TOOL_RUBBER = 0x140, 0x141
BTN_TOUCH, BTN_STYLUS, BTN_STYLUS2 = 0x14A, 0x14B, 0x14C
ABS_X, ABS_Y, ABS_PRESSURE, ABS_TILT_X, ABS_TILT_Y = 0x00, 0x01, 0x18, 0x1A, 0x1B
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x2F, 0x35, 0x36
ABS_MT_TRACKING_ID = 0x39
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0x0B, 0x0C
INPUT_PROP_DIRECT = 0x01
#: The S Pen has one side button, meant as right click.  Qt reads
#: BTN_STYLUS as the middle button and BTN_STYLUS2 as the right one.
BARREL = BTN_STYLUS2
BUS_VIRTUAL = 0x06

UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503
UI_ABS_SETUP = 0x401C5504
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567
UI_SET_PROPBIT = 0x4004556E


def _ui_get_sysname(length: int) -> int:
    return (2 << 30) | (length << 16) | (ord("U") << 8) | 44


SPAN = 32767            # coordinate range of every absolute axis
RESOLUTION = 100        # units per mm; libinput wants one for tablets
SLOTS = 10
#: The keys the keyboard has: what a PC keyboard sends, and nothing like
#: power, sleep or the radio switch.  Matches the page's KEYS table, plus the
#: few more keys a layout may need to type a character.
KEYS = frozenset([*range(1, 84), 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95,
                  96, 97, 98, 99, 100, 102, 103, 104, 105, 106, 107, 108, 109,
                  110, 111, 117, 119, 121, 122, 123, 124, 125, 126, 127])
PRESSURE_MAX = 4095
DEVICE = "/dev/uinput"


def available() -> bool:
    return os.access(DEVICE, os.W_OK)


class Device:
    """One uinput device; events are queued with ``emit`` and sent by
    ``sync``."""

    def __init__(self, name: str, keys=(), rels=(), absolutes=(),
                 props=()) -> None:
        self.fd = os.open(DEVICE, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if keys:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
                for code in keys:
                    fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            if rels:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
                for code in rels:
                    fcntl.ioctl(self.fd, UI_SET_RELBIT, code)
            if absolutes:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
                for code, low, high, resolution in absolutes:
                    fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
                    fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack(
                        "<H2x6i", code, 0, low, high, 0, 0, resolution))
            for prop in props:
                fcntl.ioctl(self.fd, UI_SET_PROPBIT, prop)
            setup = struct.pack("<4H80sI", BUS_VIRTUAL, 0x1209, 0x5544, 1,
                                name.encode()[:79], 0)
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise
        self.name = name
        self._queue: list[bytes] = []

    def sysname(self) -> str:
        """The event node (``event23``), the name KWin's D-Bus uses."""
        buffer = bytearray(64)
        fcntl.ioctl(self.fd, _ui_get_sysname(len(buffer)), buffer)
        base = Path("/sys/devices/virtual/input") / buffer.split(b"\0")[0].decode()
        events = sorted(p.name for p in base.glob("event*")) if base.exists() else []
        return events[0] if events else ""

    def emit(self, kind: int, code: int, value: int) -> None:
        self._queue.append(struct.pack("<qqHHi", 0, 0, kind, code, value))

    def sync(self) -> None:
        self.emit(EV_SYN, SYN_REPORT, 0)
        data, self._queue = b"".join(self._queue), []
        try:
            os.write(self.fd, data)
        except BlockingIOError:
            pass                       # the compositor is behind; drop it

    def close(self) -> None:
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


def _clamp(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


def _map_to_output(sysname: str, output: str) -> bool:
    """Tie a device to one screen through KWin's InputDevice D-Bus API."""
    if not sysname:
        return False
    try:
        result = subprocess.run(
            ["busctl", "--user", "set-property", "org.kde.KWin",
             f"/org/kde/KWin/InputDevice/{sysname}", "org.kde.KWin.InputDevice",
             "outputName", "s", output],
            capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class TabletInput:
    """A touchscreen, a pen and a wheel, fed from the tablet page.

    Coordinates arrive normalised to 0..1 across the virtual screen.
    """

    def __init__(self, label: str = "URDP Tablet") -> None:
        axis = (0, SPAN, RESOLUTION)
        self.touch = Device(
            f"{label} Touch", keys=(BTN_TOUCH,),
            absolutes=[(ABS_X, *axis), (ABS_Y, *axis),
                       (ABS_MT_SLOT, 0, SLOTS - 1, 0),
                       (ABS_MT_TRACKING_ID, 0, 65535, 0),
                       (ABS_MT_POSITION_X, *axis), (ABS_MT_POSITION_Y, *axis)],
            props=(INPUT_PROP_DIRECT,))
        self.pen = Device(
            f"{label} Pen",
            keys=(BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS,
                  BTN_STYLUS2),
            absolutes=[(ABS_X, *axis), (ABS_Y, *axis),
                       (ABS_PRESSURE, 0, PRESSURE_MAX, 0),
                       (ABS_TILT_X, -90, 90, 0), (ABS_TILT_Y, -90, 90, 0)],
            props=(INPUT_PROP_DIRECT,))
        # An absolute mouse: KWin spreads its range over the whole desktop,
        # so set_area says where the tablet screen lies in it.
        self.wheel = Device(
            f"{label} Mouse", keys=(BTN_LEFT, BTN_RIGHT, BTN_MIDDLE),
            rels=(REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES),
            absolutes=[(ABS_X, 0, SPAN, 0), (ABS_Y, 0, SPAN, 0)])
        #: "mouse": pen and touch act as a mouse (works in every program);
        #: "tablet": a real touchscreen and pen (pressure, gestures).
        self.mode = "mouse"
        self._area = (0.0, 0.0, 1.0, 1.0)      # x, y, w, h as desktop fractions
        self._buttons = 0
        self._fingers: dict[int, list[float]] = {}   # id -> [x, y, start x, start y]
        self._finger_pressed = False
        self._scrolling = False
        self._scrolled = False
        self._scroll_rest = [0.0, 0.0]
        self.keyboard = Device(f"{label} Keyboard", keys=sorted(KEYS))
        self._pressed: set[int] = set()
        self._keymap: dict[str, tuple[int, tuple[int, ...]]] = {}
        self._keymap_at = 0.0
        self._slots: dict[int, int] = {}       # pointer id -> slot
        self._tracking = 0
        self._pen_in = False
        self._pen_down = False
        self._rubber = False
        self._barrel = False

    def set_area(self, x: float, y: float, w: float, h: float,
                 desk_w: float, desk_h: float) -> None:
        """The tablet screen's place on the desktop, in logical pixels."""
        if desk_w > 0 and desk_h > 0:
            self._area = (x / desk_w, y / desk_h, w / desk_w, h / desk_h)

    def map_to(self, output: str) -> bool:
        ok = True
        for device in (self.touch, self.pen):
            ok = _map_to_output(device.sysname(), output) and ok
        return ok

    def close(self) -> None:
        self.release_all()
        for device in (self.touch, self.pen, self.wheel, self.keyboard):
            device.close()

    # ------------------------------------------------------------- touch
    def touch_event(self, phase: str, ident: int, x: float, y: float) -> None:
        dev = self.touch
        if phase == "down":
            if ident in self._slots or len(self._slots) >= SLOTS:
                return
            slot = next(s for s in range(SLOTS) if s not in self._slots.values())
            self._slots[ident] = slot
            self._tracking = (self._tracking + 1) % 65536
            dev.emit(EV_ABS, ABS_MT_SLOT, slot)
            dev.emit(EV_ABS, ABS_MT_TRACKING_ID, self._tracking)
        else:
            slot = self._slots.get(ident)
            if slot is None:
                return
            dev.emit(EV_ABS, ABS_MT_SLOT, slot)
        if phase in ("down", "move"):
            px, py = _clamp(x * SPAN, 0, SPAN), _clamp(y * SPAN, 0, SPAN)
            dev.emit(EV_ABS, ABS_MT_POSITION_X, px)
            dev.emit(EV_ABS, ABS_MT_POSITION_Y, py)
            if slot == min(self._slots.values()):
                dev.emit(EV_ABS, ABS_X, px)
                dev.emit(EV_ABS, ABS_Y, py)
        else:                                   # up / cancel
            dev.emit(EV_ABS, ABS_MT_TRACKING_ID, -1)
            del self._slots[ident]
        dev.emit(EV_KEY, BTN_TOUCH, 1 if self._slots else 0)
        dev.sync()

    # --------------------------------------------------------------- pen
    def pen_event(self, phase: str, x: float, y: float, pressure: float,
                  tilt_x: float, tilt_y: float, barrel: bool,
                  rubber: bool) -> None:
        """phase: "hover" / "down" / "move" / "up" / "out"."""
        dev = self.pen
        if phase == "out":
            if self._pen_in:
                if self._pen_down:
                    dev.emit(EV_KEY, BTN_TOUCH, 0)
                    dev.emit(EV_ABS, ABS_PRESSURE, 0)
                dev.emit(EV_KEY, BARREL, 0)
                dev.emit(EV_KEY, BTN_TOOL_RUBBER if self._rubber
                         else BTN_TOOL_PEN, 0)
                dev.sync()
            self._pen_in = self._pen_down = self._barrel = False
            return
        if self._pen_in and rubber != self._rubber:
            self.pen_event("out", x, y, 0, 0, 0, False, self._rubber)
        dev.emit(EV_ABS, ABS_X, _clamp(x * SPAN, 0, SPAN))
        dev.emit(EV_ABS, ABS_Y, _clamp(y * SPAN, 0, SPAN))
        dev.emit(EV_ABS, ABS_TILT_X, _clamp(tilt_x, -90, 90))
        dev.emit(EV_ABS, ABS_TILT_Y, _clamp(tilt_y, -90, 90))
        if not self._pen_in:
            self._rubber = rubber
            dev.emit(EV_KEY, BTN_TOOL_RUBBER if rubber else BTN_TOOL_PEN, 1)
            self._pen_in = True
        down = phase in ("down", "move")   # "move" comes only while down
        # The barrel button goes in a frame of its own: pressed before the tip
        # touches and released after it lifts.  In one frame libinput reports
        # the tip first, and Qt then makes a left click of a barrel tap.
        if barrel and not self._barrel:
            dev.emit(EV_KEY, BARREL, 1)
            dev.sync()
            self._barrel = True
        dev.emit(EV_ABS, ABS_PRESSURE,
                 _clamp(pressure * PRESSURE_MAX, 1, PRESSURE_MAX) if down else 0)
        if down != self._pen_down:
            dev.emit(EV_KEY, BTN_TOUCH, 1 if down else 0)
            self._pen_down = down
        dev.sync()
        if self._barrel and not barrel:
            dev.emit(EV_KEY, BARREL, 0)
            dev.sync()
            self._barrel = False

    # ------------------------------------------------------------- wheel
    def scroll(self, dx: float, dy: float) -> None:
        """Scroll by *dx*/*dy* wheel clicks (fractions allowed)."""
        dev = self.wheel
        if dy:
            dev.emit(EV_REL, REL_WHEEL_HI_RES, int(round(dy * 120)))
        if dx:
            dev.emit(EV_REL, REL_HWHEEL_HI_RES, int(round(dx * 120)))
        if dx or dy:
            dev.sync()

    # ------------------------------------------------------- mouse mode
    def _point(self, x: float, y: float) -> None:
        ax, ay, aw, ah = self._area
        dev = self.wheel
        # Kept on the tablet screen: the device itself spans the desktop.
        x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        dev.emit(EV_ABS, ABS_X, _clamp((ax + x * aw) * SPAN, 0, SPAN))
        dev.emit(EV_ABS, ABS_Y, _clamp((ay + y * ah) * SPAN, 0, SPAN))

    def _mouse(self, x: float, y: float, buttons: int) -> None:
        """Move to x, y (0..1 on the tablet screen) with *buttons* held
        (1 left, 2 right); presses come after the move, in frames of their
        own, so programs see them at the new place."""
        self._point(x, y)
        self.wheel.sync()
        for bit, code in ((1, BTN_LEFT), (2, BTN_RIGHT)):
            if (buttons ^ self._buttons) & bit:
                self.wheel.emit(EV_KEY, code, 1 if buttons & bit else 0)
                self.wheel.sync()
        self._buttons = buttons

    def pen_as_mouse(self, phase: str, x: float, y: float,
                     barrel: bool) -> None:
        if phase == "out":
            if self._buttons:
                self._mouse(x, y, 0)
            return
        # The page sends "move" only while the pen is down.
        held = phase in ("down", "move")
        buttons = self._buttons or (2 if barrel else 1)   # keep the one pressed
        self._mouse(x, y, buttons if held else 0)

    def touch_as_mouse(self, phase: str, ident: int, x: float,
                       y: float) -> None:
        """One finger: tap clicks, drag drags (the button goes down on the
        first movement, so a second finger can still turn it into a scroll).
        Two fingers: drag scrolls, tap right-clicks."""
        fingers = self._fingers
        if phase == "down":
            if ident in fingers or len(fingers) >= SLOTS:
                return
            fingers[ident] = [x, y, x, y]
            if len(fingers) == 1:
                self._scrolling = self._scrolled = False
                self._finger_pressed = False
                self._mouse(x, y, 0)
            elif not self._finger_pressed:
                self._scrolling = True
                self._scroll_rest = [0.0, 0.0]
            return
        finger = fingers.get(ident)
        if finger is None:
            return
        if phase == "move":
            dx, dy = x - finger[0], y - finger[1]
            finger[0], finger[1] = x, y
            if self._scrolling:
                # Each finger adds half its travel; 1/20 of the screen
                # height is one wheel click, in the direction of the hand.
                self._scroll_rest[0] -= dx / len(fingers) * 20
                self._scroll_rest[1] += dy / len(fingers) * 20
                sx, sy = self._scroll_rest
                if abs(sx) >= 0.1 or abs(sy) >= 0.1:
                    self.scroll(sx, sy)
                    self._scroll_rest = [0.0, 0.0]
                    self._scrolled = True
                return
            if not self._finger_pressed:
                if abs(x - finger[2]) + abs(y - finger[3]) < 0.004:
                    return                     # still a tap
                self._mouse(finger[2], finger[3], 1)
                self._finger_pressed = True
            self._mouse(x, y, 1)
            return
        # up / cancel
        del fingers[ident]
        if self._scrolling:
            if not fingers:
                if not self._scrolled:
                    self._mouse(x, y, 2)
                    self._mouse(x, y, 0)
                self._scrolling = False
            return
        if self._finger_pressed:
            self._mouse(x, y, 0)
        else:
            self._mouse(finger[2], finger[3], 1)
            self._mouse(finger[2], finger[3], 0)
        self._finger_pressed = False

    # ---------------------------------------------------------- keyboard
    def key(self, code: int, down: bool) -> None:
        """A physical key of the tablet's keyboard, as an evdev code; the
        desktop's own layout decides what it types."""
        if code not in KEYS or (code in self._pressed) == down:
            return
        (self._pressed.add if down else self._pressed.discard)(code)
        self.keyboard.emit(EV_KEY, code, 1 if down else 0)
        self.keyboard.sync()

    def _tap(self, code: int, mods: tuple[int, ...] = ()) -> None:
        dev = self.keyboard
        for mod in mods:
            dev.emit(EV_KEY, mod, 1)
            dev.sync()
        dev.emit(EV_KEY, code, 1)
        dev.sync()
        dev.emit(EV_KEY, code, 0)
        dev.sync()
        for mod in reversed(mods):
            dev.emit(EV_KEY, mod, 0)
            dev.sync()

    def type_text(self, text: str) -> None:
        """Text from the on-screen keyboard, typed key by key with the
        desktop's current layout.  Characters it cannot type are skipped."""
        from . import keymap
        if time.monotonic() - self._keymap_at > 5:       # layout may change
            self._keymap = keymap.build(*keymap.kde_layout())
            self._keymap_at = time.monotonic()
        for char in text[:500]:
            if char == "\n":
                self._tap(keymap.KEY_ENTER)
            elif char == "\t":
                self._tap(keymap.KEY_TAB)
            elif char in self._keymap and self._keymap[char][0] in KEYS:
                self._tap(*self._keymap[char])

    def release_all(self) -> None:
        for code in list(self._pressed):
            self.key(code, False)
        if getattr(self, "_buttons", 0):
            for code in (BTN_LEFT, BTN_RIGHT):
                self.wheel.emit(EV_KEY, code, 0)
            self.wheel.sync()
            self._buttons = 0
        if hasattr(self, "_fingers"):
            self._fingers.clear()
            self._scrolling = self._finger_pressed = False
        for ident in list(self._slots):
            self.touch_event("up", ident, 0, 0)
        self.pen_event("out", 0, 0, 0, 0, 0, False, False)
