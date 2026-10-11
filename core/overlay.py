import tkinter as tk
import ctypes
import time
import math
import os
import logging
from core.gestures.screenshot import ScreenshotState
from config import SCREENSHOT_HOLD_DURATION_S, SCREENSHOT_OVERLAY_POSITION

logger = logging.getLogger(__name__)

def get_capsule_path(w, h, p):
    r = h / 2.0
    top_len = w - 2*r
    arc_len = math.pi * r
    total_len = 2*top_len + 2*arc_len
    target = p * total_len
    
    segments = []
    current = 0.0
    
    l = top_len / 2.0
    if target > current:
        draw_len = min(l, target - current)
        segments.append(('line', w/2, 0, w/2 + draw_len, 0))
        current += l
        
    if target > current:
        draw_len = min(arc_len, target - current)
        extent = -(draw_len / arc_len) * 180
        segments.append(('arc', w-2*r, 0, w, h, 90, extent))
        current += arc_len
        
    if target > current:
        draw_len = min(top_len, target - current)
        segments.append(('line', w-r, h, w-r - draw_len, h))
        current += top_len
        
    if target > current:
        draw_len = min(arc_len, target - current)
        extent = -(draw_len / arc_len) * 180
        segments.append(('arc', 0, 0, 2*r, h, 270, extent))
        current += arc_len
        
    if target > current:
        draw_len = min(l, target - current)
        segments.append(('line', r, 0, r + draw_len, 0))
        
    return segments

class ScreenshotOverlay:
    def __init__(self, position="TOP_RIGHT"):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.wm_attributes('-transparentcolor', '#000001')
        self.root.configure(bg='#000001')
        
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x00080000 | 0x00000020)
        except Exception as e:
            logger.warning("Could not make overlay click-through: %s", e)
            
        self.w = 220
        self.h = 44
        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()
        
        self.pad_top = 16
        self.x = (self.sw - self.w) // 2
        self.y_hidden = -self.h - 10
        self.y_visible = self.pad_top
        
        self.current_y = self.y_hidden
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{int(self.current_y)}")
        
        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h, bg='#000001', highlightthickness=0)
        self.canvas.pack()
        
        self._is_hidden = True
        self.root.withdraw()
        
        self._last_ui_update = 0
        self._toast_until = 0
        self._island_state = "IDLE" # IDLE, DROPPING, HOLDING, SUCCESS, ERROR, RETRACTING
        self._island_text = "Hold still"
        self._island_color = "#00e5ff"
        self._progress = 0.0
        
        self._force_hide = False

    def hide(self):
        if not self._force_hide:
            self.root.withdraw()
            self.root.update()
            self._force_hide = True
            time.sleep(0.02)
            
    def show(self):
        if self._force_hide:
            if self._island_state != "IDLE":
                self.root.deiconify()
                self.root.lift()
            self.root.update()
            self._force_hide = False
            
    def _draw_island(self):
        self.canvas.delete("all")
        # Base pill
        r = self.h / 2
        points = [
            r, 0, self.w-r, 0,
            self.w-r, 0, self.w, 0, self.w, r,
            self.w, r, self.w, self.h-r,
            self.w, self.h-r, self.w, self.h, self.w-r, self.h,
            self.w-r, self.h, r, self.h,
            r, self.h, 0, self.h, 0, self.h-r,
            0, self.h-r, 0, r,
            0, r, 0, 0, r, 0
        ]
        self.canvas.create_polygon(points, smooth=True, fill="#121212", outline="#222222", width=1)
        
        # Text
        if self._island_state == "SUCCESS":
            # Draw a small checkmark on the left
            cx, cy = 30, self.h/2
            self.canvas.create_line(cx-6, cy, cx-2, cy+5, cx+6, cy-5, fill="#00ff66", width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)
            self.canvas.create_text(self.w/2 + 10, self.h/2, text=self._island_text, fill="white", font=("Segoe UI", 11, "bold"))
        elif self._island_state == "ERROR":
            cx, cy = 30, self.h/2
            self.canvas.create_line(cx-5, cy-5, cx+5, cy+5, fill="#ff4444", width=2, capstyle=tk.ROUND)
            self.canvas.create_line(cx-5, cy+5, cx+5, cy-5, fill="#ff4444", width=2, capstyle=tk.ROUND)
            self.canvas.create_text(self.w/2 + 10, self.h/2, text=self._island_text, fill="white", font=("Segoe UI", 11, "bold"))
        else:
            self.canvas.create_text(self.w/2, self.h/2, text=self._island_text, fill="white", font=("Segoe UI", 11, "bold"))
            
        # Progress border
        if self._progress > 0:
            segments = get_capsule_path(self.w, self.h, self._progress)
            for seg in segments:
                if seg[0] == 'line':
                    self.canvas.create_line(seg[1], seg[2], seg[3], seg[4], fill=self._island_color, width=2, capstyle=tk.ROUND)
                elif seg[0] == 'arc':
                    self.canvas.create_arc(seg[1], seg[2], seg[3], seg[4], start=seg[5], extent=seg[6], style=tk.ARC, outline=self._island_color, width=2)
                    
    def _animate_y(self, target_y, speed=0.3):
        dy = target_y - self.current_y
        self.current_y += dy * speed
        if abs(self.current_y - target_y) < 1.0:
            self.current_y = target_y
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{int(self.current_y)}")
        
    def update_state(self, ss_controller, is_paused):
        now = time.perf_counter()
        if now - self._last_ui_update < 0.033:
            return
        dt = now - self._last_ui_update
        self._last_ui_update = now
        
        # State transitions
        if self._island_state in ("SUCCESS", "ERROR"):
            if now > self._toast_until:
                self._island_state = "RETRACTING"
        elif ss_controller and ss_controller._state == ScreenshotState.HOLDING:
            rem = getattr(ss_controller, 'remaining', SCREENSHOT_HOLD_DURATION_S)
            prog = getattr(ss_controller, 'progress', 0.0)

            if self._island_state in ("IDLE", "RETRACTING"):
                self._island_state = "DROPPING"
                self._island_color = "#00ff66"
                self.root.deiconify()
                self.root.lift()
                self._is_hidden = False
            elif self._island_state == "DROPPING" and self.current_y == self.y_visible:
                self._island_state = "HOLDING"
                
            self._island_text = f"Hold still  {rem:.1f}s"
            self._progress = min(1.0, max(0.0, float(prog)))
        elif ss_controller and getattr(ss_controller, '_display_until', 0) > now:
            if "CANCELLED" in getattr(ss_controller, 'hud_text', '') and self._island_state in ("DROPPING", "HOLDING"):
                self._island_state = "RETRACTING"
                self._progress = 0.0
        else:
            if self._island_state in ("DROPPING", "HOLDING"):
                self._island_state = "RETRACTING"
                self._progress = 0.0
                
        if ss_controller:
            if ss_controller._state == ScreenshotState.CAPTURED:
                if "CAPTURED" in getattr(ss_controller, 'hud_text', '') and self._island_state != "SUCCESS":
                    import re
                    m = re.search(r'\((.*?)\)', ss_controller.hud_text)
                    if m:
                        self.show_success_toast(m.group(1))
            elif ss_controller._state == ScreenshotState.ERROR:
                if self._island_state != "ERROR":
                    self.show_error_toast()
                    
        if is_paused:
            self._island_state = "RETRACTING"
            self._progress = 0.0
            
        # Animation
        if self._island_state == "DROPPING":
            self._animate_y(self.y_visible)
        elif self._island_state == "RETRACTING":
            self._animate_y(self.y_hidden)
            if self.current_y == self.y_hidden and not self._is_hidden:
                self.root.withdraw()
                self.root.update()
                self._is_hidden = True
                self._island_state = "IDLE"
                self._progress = 0.0
        elif self._island_state == "SUCCESS":
            self._progress = 1.0
            self._animate_y(self.y_visible)
        elif self._island_state == "ERROR":
            self._progress = 1.0
            self._animate_y(self.y_visible)
        elif self._island_state == "IDLE":
            self._progress = 0.0
            
        if not self._is_hidden and not self._force_hide:
            self._draw_island()
            self.root.update()

    def show_success_toast(self, filename):
        now = time.perf_counter()
        self._toast_until = now + 1.5
        self._island_state = "SUCCESS"
        self._island_text = "Screenshot saved"
        self._island_color = "#00ff66"
        self._progress = 1.0
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False
        
    def show_error_toast(self):
        now = time.perf_counter()
        self._toast_until = now + 1.5
        self._island_state = "ERROR"
        self._island_text = "Screenshot failed"
        self._island_color = "#ff4444"
        self._progress = 1.0
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def show_app_toast(self, app_name: str = "WhatsApp", success: bool = True):
        now = time.perf_counter()
        self._toast_until = now + 1.8
        self._island_state = "SUCCESS" if success else "ERROR"
        self._island_text = f"Opening {app_name}" if success else f"{app_name} not found"
        self._island_color = "#25D366" if success else "#ff4444"
        self._progress = 1.0
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def close(self):
        self.root.destroy()
