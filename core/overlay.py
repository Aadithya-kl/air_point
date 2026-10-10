# core/overlay.py
import tkinter as tk
import ctypes
import time
import os
from core.gestures.screenshot import ScreenshotState
import logging

logger = logging.getLogger(__name__)

class ScreenshotOverlay:
    def __init__(self, position="TOP_RIGHT"):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.85)
        self.root.configure(bg="#151515")
        
        self.lbl_title = tk.Label(self.root, text="AIR POINT · SCREENSHOT", bg="#151515", fg="#00e5ff", font=("Consolas", 10, "bold"))
        self.lbl_title.pack(anchor="w", padx=12, pady=(12, 6))
        
        self.lbl_status = tk.Label(self.root, text="STATUS: READY", bg="#151515", fg="#ffffff", font=("Consolas", 10))
        self.lbl_status.pack(anchor="w", padx=12)
        
        self.lbl_hands = tk.Label(self.root, text="LEFT HAND: 0/5 · RIGHT HAND: 0/5", bg="#151515", fg="#aaaaaa", font=("Consolas", 9))
        self.lbl_hands.pack(anchor="w", padx=12, pady=(2, 6))
        
        self.lbl_last = tk.Label(self.root, text="LAST CAPTURE: —", bg="#151515", fg="#ffffff", font=("Consolas", 10))
        self.lbl_last.pack(anchor="w", padx=12, pady=(0, 12))
        
        self.root.update_idletasks()
        
        # Make click-through on Windows (MUST use GetParent for TopLevel window on Windows, not the client frame)
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x00080000 | 0x00000020)
        except Exception as e:
            logger.warning("Could not make overlay click-through: %s", e)
        
        # Dimensions
        w = 300
        h = 105
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pad = 20
        
        
        if position == "TOP_RIGHT":
            x, y = sw - w - pad, pad
        elif position == "TOP_LEFT":
            x, y = pad, pad
        elif position == "BOTTOM_RIGHT":
            x, y = sw - w - pad, sh - h - pad
        else:
            x, y = pad, sh - h - pad
            
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self._last_capture = "—"
        self._toast_until = 0
        self._is_hidden = False
        
    def hide(self):
        if not self._is_hidden:
            self.root.withdraw()
            self.root.update()
            self._is_hidden = True
            time.sleep(0.02) # Give Windows DWM time to actually hide it from screen before capture
        
    def show(self):
        if self._is_hidden:
            self.root.deiconify()
            self.root.update()
            self._is_hidden = False
            
    def update_state(self, ss_controller, is_paused):
        now = time.time()
        if not hasattr(self, '_last_ui_update'):
            self._last_ui_update = 0
            
        if now - self._last_ui_update < 0.033: # Max 30fps update rate
            return
            
        self._last_ui_update = now
        
        if is_paused:
            self.lbl_status.config(text="STATUS: SYSTEM PAUSED", fg="#aaaaaa")
        elif now < self._toast_until:
            pass # Keep toast active
        elif ss_controller and ss_controller._state == ScreenshotState.READY:
            self.lbl_status.config(text="STATUS: READY", fg="#00e5ff")
        elif ss_controller and ss_controller._state == ScreenshotState.HOLDING:
            from config import SCREENSHOT_HOLD_DURATION_S
            elapsed = now - getattr(ss_controller, '_hold_start_time', now)
            rem = max(0.0, SCREENSHOT_HOLD_DURATION_S - elapsed)
            self.lbl_status.config(text=f"STATUS: HOLD BOTH PALMS ({rem:.1f}s)", fg="#ffff00")
        elif ss_controller and ss_controller._state == ScreenshotState.CAPTURED:
            if "CAPTURED" in ss_controller.hud_text and now > self._toast_until:
                import re
                m = re.search(r'\((.*?)\)', ss_controller.hud_text)
                if m:
                    self.show_success_toast(m.group(1))
        elif ss_controller and ss_controller._state == ScreenshotState.ERROR:
            if now > self._toast_until:
                self.show_error_toast()
        elif ss_controller and getattr(ss_controller, '_display_until', 0) > now:
            if "CANCELLED" in ss_controller.hud_text:
                self.lbl_status.config(text="STATUS: CANCELLED", fg="#ff4444")
            elif "REARM" in ss_controller.hud_text:
                self.lbl_status.config(text="STATUS: RELEASE TO REARM", fg="#ffaa00")

        left = getattr(ss_controller, 'left_fingers_ext', 0) if ss_controller else 0
        right = getattr(ss_controller, 'right_fingers_ext', 0) if ss_controller else 0
        
        self.lbl_hands.config(text=f"LEFT HAND: {left}/5 · RIGHT HAND: {right}/5")
        self.lbl_last.config(text=f"LAST CAPTURE: {self._last_capture}")
        
        if not self._is_hidden:
            self.root.update()
        
    def show_success_toast(self, filename):
        now = time.time()
        self._toast_until = now + 4.0
        base = os.path.basename(filename)
        self.lbl_status.config(text=f"SAVED: {base}", fg="#00ff00")
        self._last_capture = time.strftime("%H:%M:%S")
        self.update_state(None, False) # force redraw
        
    def show_error_toast(self):
        now = time.time()
        self._toast_until = now + 4.0
        self.lbl_status.config(text="ERROR: CAPTURE FAILED", fg="#ff0000")
        self.update_state(None, False)

    def close(self):
        self.root.destroy()
