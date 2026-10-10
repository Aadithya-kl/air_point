# core/actuator.py
"""
Windows mouse actuation via SendInput.

Why SendInput over mouse_event:
- mouse_event is deprecated since Windows Vista; routes through a compatibility shim
- SendInput is the documented, low-overhead replacement
- MOUSEEVENTF_VIRTUALDESK + MOUSEEVENTF_ABSOLUTE correctly spans all monitors

Why SendInput over pynput for movement:
- pynput wraps SendInput anyway; calling ctypes directly removes one layer
- pynput is used here only for click/drag state because its press/release
  abstraction is cleaner than manually managing button flags in INPUT structs

Coordinate system for MOUSEEVENTF_ABSOLUTE:
- The virtual desktop is mapped to a normalized space of [0, 65535] x [0, 65535]
- Physical pixel (x, y) -> norm = (x * 65535 / total_w, y * 65535 / total_h)
- MOUSEEVENTF_VIRTUALDESK flag makes (65535, 65535) = bottom-right of ALL monitors
  Without this flag, (65535, 65535) = bottom-right of the PRIMARY monitor only
"""

import ctypes
import logging
import os
import time
from PIL import ImageGrab

from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Controller as KeyboardController, Key

logger = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)

# ── SendInput ctypes structures ───────────────────────────────────────────────

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("_u",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("_u",   _INPUT_UNION),
    ]


INPUT_MOUSE              = 0
MOUSEEVENTF_MOVE         = 0x0001
MOUSEEVENTF_ABSOLUTE     = 0x8000
MOUSEEVENTF_VIRTUALDESK  = 0x4000   # Interpret absolute coords as virtual desktop coords


MOUSEEVENTF_LEFTDOWN     = 0x0002
MOUSEEVENTF_LEFTUP       = 0x0004
MOUSEEVENTF_RIGHTDOWN    = 0x0008
MOUSEEVENTF_RIGHTUP      = 0x0010

def _send_input(inp: INPUT) -> None:
    result = _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if result == 0:
        err = ctypes.get_last_error()
        logger.warning("SendInput returned 0 (UIPI block or invalid input?). GetLastError=%d", err)


# ── Public Actuator ───────────────────────────────────────────────────────────

class MouseActuator:
    """
    Handles all mouse movement and click state.

    Args:
        total_width:  Physical pixel width of the full virtual desktop
        total_height: Physical pixel height of the full virtual desktop
    """

    def __init__(self, total_width: int, total_height: int) -> None:
        self._total_w = total_width
        self._total_h = total_height
        self._pynput = MouseController()
        self._dragging = False
        self._keyboard = KeyboardController()
        logger.info("MouseActuator ready (%dx%d desktop)", total_width, total_height)

    def move(self, x: int, y: int) -> None:
        """
        Move cursor to absolute desktop coordinates (physical pixels).
        Maps to SendInput's [0, 65535] normalized space.
        """
        norm_x = int(x * 65535 / max(self._total_w - 1, 1))
        norm_y = int(y * 65535 / max(self._total_h - 1, 1))

        # Clamp to valid range
        norm_x = max(0, min(65535, norm_x))
        norm_y = max(0, min(65535, norm_y))

        inp = INPUT(
            type=INPUT_MOUSE,
            mi=MOUSEINPUT(
                dx=norm_x,
                dy=norm_y,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time=0,
                dwExtraInfo=0,
            ),
        )
        _send_input(inp)

    def _send_mouse_flag(self, flag: int) -> None:
        inp = INPUT(
            type=INPUT_MOUSE,
            mi=MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=0,
                dwFlags=flag,
                time=0,
                dwExtraInfo=0,
            ),
        )
        _send_input(inp)

    def left_click(self) -> None:
        logger.debug("OS left_click executing via SendInput")
        self._send_mouse_flag(MOUSEEVENTF_LEFTDOWN)
        self._send_mouse_flag(MOUSEEVENTF_LEFTUP)

    def right_click(self) -> None:
        self._send_mouse_flag(MOUSEEVENTF_RIGHTDOWN)
        self._send_mouse_flag(MOUSEEVENTF_RIGHTUP)

    def drag_start(self) -> None:
        if not self._dragging:
            self._send_mouse_flag(MOUSEEVENTF_LEFTDOWN)
            self._dragging = True
            logger.debug("Drag started")

    def drag_end(self) -> None:
        if self._dragging:
            self._send_mouse_flag(MOUSEEVENTF_LEFTUP)
            self._dragging = False
            logger.debug("Drag ended")

    def scroll(self, ticks: int) -> None:
        """
        Scroll vertically. Positive ticks = scroll up, negative = scroll down.
        """
        self._pynput.scroll(0, ticks)

    def double_click(self) -> None:
        logger.debug("OS double_click executing via SendInput")
        self.left_click()
        time.sleep(0.05)
        self.left_click()

    def win_d(self) -> None:
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press('d')
            self._keyboard.release('d')

    def alt_tab(self) -> None:
        with self._keyboard.pressed(Key.alt):
            self._keyboard.press(Key.tab)
            self._keyboard.release(Key.tab)

    def win_tab(self) -> None:
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press(Key.tab)
            self._keyboard.release(Key.tab)

    def win_key(self) -> None:
        self._keyboard.press(Key.cmd)
        self._keyboard.release(Key.cmd)

    def alt_left(self) -> None:
        with self._keyboard.pressed(Key.alt):
            self._keyboard.press(Key.left)
            self._keyboard.release(Key.left)

    def win_l(self) -> None:
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press('l')
            self._keyboard.release('l')

    def win_snap_left(self) -> None:
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press(Key.left)
            self._keyboard.release(Key.left)

    def win_snap_right(self) -> None:
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press(Key.right)
            self._keyboard.release(Key.right)

    def ctrl_down(self) -> None:
        self._keyboard.press(Key.ctrl)

    def ctrl_up(self) -> None:
        self._keyboard.release(Key.ctrl)

    def zoom_in(self) -> None:
        with self._keyboard.pressed(Key.ctrl):
            self._keyboard.press('=')
            self._keyboard.release('=')

    def zoom_out(self) -> None:
        with self._keyboard.pressed(Key.ctrl):
            self._keyboard.press('-')
            self._keyboard.release('-')

    def mag_zoom_in(self) -> None:
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press('=')
            self._keyboard.release('=')

    def mag_zoom_out(self) -> None:
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press('-')
            self._keyboard.release('-')

    def take_screenshot(self, directory: str, pre_capture_cb=None, post_capture_cb=None) -> str | None:
        try:
            if not os.path.exists(directory):
                os.makedirs(directory)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(directory, f"screenshot_{timestamp}.png")
            
            if pre_capture_cb:
                pre_capture_cb()
                
            img = ImageGrab.grab(all_screens=True)
            
            if post_capture_cb:
                post_capture_cb()
                
            img.save(filename)
            logger.info("Screenshot saved to %s", filename)
            return filename
        except Exception as e:
            if post_capture_cb:
                post_capture_cb()
            logger.error("Screenshot failed: %s", e)
            return None

    @property
    def is_dragging(self) -> bool:
        return self._dragging
