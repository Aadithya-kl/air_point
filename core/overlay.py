# core/overlay.py
"""
Air Point Phase 7: Dynamic Island-Style Gesture Feedback HUD & Runtime Status.
Renders smooth, non-blocking on-screen feedback capsules for:
  - Volume Up / Down (with live percentage and progress border)
  - Audio Mute / Unmute (with distinct amber/emerald indicators)
  - Screenshot countdown, saved confirmation, and failure handling
  - WhatsApp desktop launch feedback
  - Hand lost and hand reacquired tracking state transitions
  - System paused and resumed status
  - Optional expanded runtime telemetry (Owner, Tracking, FPS, Status)
"""

import tkinter as tk
import ctypes
import time
import math
import logging
from core.gestures.screenshot import ScreenshotState
from config import (
    SCREENSHOT_HOLD_DURATION_S,
    HUD_ENABLED,
    HUD_ANIMATION_SPEED,
    HUD_TOAST_DURATION_S,
    HUD_STATUS_DURATION_S,
)

logger = logging.getLogger(__name__)

# Event priority hierarchy (higher preempts lower; prevents overlapping flicker)
PRIORITY_CRITICAL = 40    # Screenshot, app launch, audio mute/unmute, pause
PRIORITY_VOLUME = 30      # Ear volume continuous adjustment
PRIORITY_TRACKING = 20    # Hand lost / reacquired
PRIORITY_STATUS = 10      # Runtime status inspect


def get_capsule_path(w: float, h: float, p: float) -> list[tuple]:
    """
    Computes SVG-like path segments along the perimeter of a rounded capsule of dimensions (w, h)
    for progress scalar p in [0.0, 1.0].
    """
    r = h / 2.0
    top_len = w - 2 * r
    arc_len = math.pi * r
    total_len = 2 * top_len + 2 * arc_len
    target = p * total_len

    segments = []
    current = 0.0

    l = top_len / 2.0
    if target > current:
        draw_len = min(l, target - current)
        segments.append(('line', w / 2, 0, w / 2 + draw_len, 0))
        current += l

    if target > current:
        draw_len = min(arc_len, target - current)
        extent = -(draw_len / arc_len) * 180
        segments.append(('arc', w - 2 * r, 0, w, h, 90, extent))
        current += arc_len

    if target > current:
        draw_len = min(top_len, target - current)
        segments.append(('line', w - r, h, w - r - draw_len, h))
        current += top_len

    if target > current:
        draw_len = min(arc_len, target - current)
        extent = -(draw_len / arc_len) * 180
        segments.append(('arc', 0, 0, 2 * r, h, 270, extent))
        current += arc_len

    if target > current:
        draw_len = min(l, target - current)
        segments.append(('line', r, 0, r + draw_len, 0))

    return segments


class ScreenshotOverlay:
    """
    Dynamic Island desktop HUD overlay.
    Provides non-blocking, transparent, click-through graphical feedback at top-center of desktop.
    """

    def __init__(self, position: str = "TOP_CENTER") -> None:
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

        self.current_y = float(self.y_hidden)
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{int(self.current_y)}")

        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h, bg='#000001', highlightthickness=0)
        self.canvas.pack()

        self._is_hidden = True
        self.root.withdraw()

        self._last_ui_update = 0.0
        self._toast_until = 0.0
        self._island_state = "IDLE"  # IDLE, DROPPING, HOLDING, SUCCESS, ERROR, STATUS, RETRACTING
        self._island_text = "Hold still"
        self._island_subtext = ""
        self._island_icon = "CHECK"
        self._island_color = "#00e5ff"
        self._progress = 0.0

        # Phase 7.1 Dynamic Island Volume Visualizer state
        self._island_mode = ""  # "", "STANDARD", "STATUS", "VOLUME"
        self._volume_scalar = 0.50
        self._volume_muted = False
        self._is_volume_active = False
        self._volume_direction = "NONE"
        self._bar_heights = [2.0] * 7
        self._audio_controller = None

        self._current_priority = 0
        self._last_event_text = ""
        self._last_event_time = 0.0

        self._force_hide = False
        logger.info("Dynamic Island HUD initialized (w=%d, h=%d)", self.w, self.h)

    @property
    def volume_scalar(self) -> float:
        return self._volume_scalar

    @property
    def volume_pct(self) -> int:
        return int(round(self._volume_scalar * 100.0))

    @property
    def displayed_volume_pct(self) -> str:
        return f"{self.volume_pct}%"

    @property
    def is_volume_active(self) -> bool:
        return self._is_volume_active

    @property
    def is_volume_muted(self) -> bool:
        return self._volume_muted

    @property
    def volume_direction(self) -> str:
        return self._volume_direction

    @property
    def sound_bars(self) -> list[float]:
        return getattr(self, '_bar_heights', self.get_sound_bar_heights(self._volume_scalar, self._volume_muted, self._is_volume_active))

    def set_audio_controller(self, audio_controller) -> None:
        self._audio_controller = audio_controller

    def _set_dimensions(self, w: int, h: int) -> None:
        if self.w != w or self.h != h:
            self.w = w
            self.h = h
            self.x = (self.sw - self.w) // 2
            self.canvas.configure(width=self.w, height=self.h)
            self.root.geometry(f"{self.w}x{self.h}+{self.x}+{int(self.current_y)}")

    def _reset_dimensions(self) -> None:
        self._island_mode = ""
        self._set_dimensions(220, 44)

    def hide(self) -> None:
        if not self._force_hide:
            self.root.withdraw()
            self.root.update()
            self._force_hide = True
            time.sleep(0.02)

    def show(self) -> None:
        if self._force_hide:
            if self._island_state != "IDLE":
                self.root.deiconify()
                self.root.lift()
            self.root.update()
            self._force_hide = False

    def _can_post_event(self, priority: int, text: str, now: float) -> bool:
        """Determines whether a new event can be displayed, enforcing duplicate suppression and priority."""
        # Duplicate suppression: identical message within 0.8s ignored
        if text == self._last_event_text and (now - self._last_event_time) < 0.8:
            return False

        # Priority arbitration: active higher priority toast is protected
        if self._island_state in ("SUCCESS", "ERROR", "HOLDING", "STATUS"):
            if now < self._toast_until and priority < self._current_priority:
                return False

        return True

    def _draw_vector_icon(self, cx: float, cy: float) -> None:
        """Renders anti-aliased native Canvas vector icons."""
        icon = self._island_icon
        color = self._island_color

        if icon == "CHECK":
            self.canvas.create_line(cx - 6, cy, cx - 2, cy + 5, cx + 6, cy - 5, fill="#00ff66", width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)
        elif icon == "CROSS":
            self.canvas.create_line(cx - 5, cy - 5, cx + 5, cy + 5, fill="#ff4444", width=2, capstyle=tk.ROUND)
            self.canvas.create_line(cx - 5, cy + 5, cx + 5, cy - 5, fill="#ff4444", width=2, capstyle=tk.ROUND)
        elif icon == "VOL_UP":
            self.canvas.create_polygon(cx - 8, cy - 3, cx - 5, cy - 3, cx - 2, cy - 6, cx - 2, cy + 6, cx - 5, cy + 3, cx - 8, cy + 3, fill="#00e5ff", outline="")
            self.canvas.create_line(cx + 3, cy + 3, cx + 3, cy - 4, fill="#00e5ff", width=2, capstyle=tk.ROUND)
            self.canvas.create_line(cx + 1, cy - 2, cx + 3, cy - 4, cx + 5, cy - 2, fill="#00e5ff", width=2, capstyle=tk.ROUND)
        elif icon == "VOL_DOWN":
            self.canvas.create_polygon(cx - 8, cy - 3, cx - 5, cy - 3, cx - 2, cy - 6, cx - 2, cy + 6, cx - 5, cy + 3, cx - 8, cy + 3, fill="#00e5ff", outline="")
            self.canvas.create_line(cx + 3, cy - 4, cx + 3, cy + 3, fill="#00e5ff", width=2, capstyle=tk.ROUND)
            self.canvas.create_line(cx + 1, cy + 1, cx + 3, cy + 3, cx + 5, cy + 1, fill="#00e5ff", width=2, capstyle=tk.ROUND)
        elif icon == "MUTE":
            self.canvas.create_polygon(cx - 8, cy - 3, cx - 5, cy - 3, cx - 2, cy - 6, cx - 2, cy + 6, cx - 5, cy + 3, cx - 8, cy + 3, fill="#ffaa00", outline="")
            self.canvas.create_line(cx - 7, cy + 6, cx + 6, cy - 6, fill="#ff4444", width=2, capstyle=tk.ROUND)
        elif icon == "UNMUTE":
            self.canvas.create_polygon(cx - 8, cy - 3, cx - 5, cy - 3, cx - 2, cy - 6, cx - 2, cy + 6, cx - 5, cy + 3, cx - 8, cy + 3, fill="#00ff66", outline="")
            self.canvas.create_arc(cx - 1, cy - 5, cx + 5, cy + 5, start=-45, extent=90, style=tk.ARC, outline="#00ff66", width=2)
            self.canvas.create_arc(cx + 2, cy - 8, cx + 10, cy + 8, start=-45, extent=90, style=tk.ARC, outline="#00ff66", width=2)
        elif icon == "APP":
            self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, fill="#25D366", outline="")
            self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="white", outline="")
        elif icon == "HAND_LOST":
            self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, outline="#ffaa00", width=2)
            self.canvas.create_line(cx - 3, cy, cx + 3, cy, fill="#ffaa00", width=2, capstyle=tk.ROUND)
        elif icon == "HAND_FOUND":
            self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, outline="#00e5ff", width=1)
            self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="#00e5ff", outline="")
        elif icon == "PAUSE":
            self.canvas.create_rectangle(cx - 5, cy - 6, cx - 2, cy + 6, fill="#ffaa00", outline="")
            self.canvas.create_rectangle(cx + 2, cy - 6, cx + 5, cy + 6, fill="#ffaa00", outline="")
        elif icon == "PLAY":
            self.canvas.create_polygon(cx - 4, cy - 6, cx + 5, cy, cx - 4, cy + 6, fill="#00ff66", outline="")
        elif icon == "STATUS":
            self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, outline="#00e5ff", width=1)
            self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#00e5ff", outline="")
        else:
            self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=color, outline="")

    def get_sound_bar_heights(
        self,
        volume_scalar: float,
        is_muted: bool = False,
        is_active: bool = False,
        now: float | None = None,
        num_bars: int = 7,
    ) -> list[float]:
        """
        Calculates vertical sound bar heights strictly tied to the system volume scalar [0.0, 1.0].
        - Muted: bars become inactive/flat (2.0px).
        - Low volume: short bars.
        - High volume: tall bars.
        - Active dwell: subtle harmonic modulation (<= 10%) while maintaining true volume proportions.
        """
        if is_muted:
            return [2.0] * num_bars

        if now is None:
            now = time.perf_counter()

        vol = max(0.0, min(1.0, float(volume_scalar)))
        profile = [0.45, 0.65, 0.85, 1.00, 0.85, 0.65, 0.45]
        min_h = 3.0
        max_h = 18.0

        heights = []
        for i in range(num_bars):
            p = profile[i % len(profile)]
            base_h = min_h + (max_h - min_h) * vol * p
            if is_active and vol > 0.001:
                osc = 0.10 * math.sin(now * 8.0 + i * 0.9)
                h_bar = max(min_h, min(max_h, base_h * (1.0 + osc)))
            else:
                h_bar = base_h
            heights.append(round(h_bar, 2))
        return heights

    def _draw_volume_visualizer(self, w: float, h: float, cy: float) -> None:
        """
        Renders the Dynamic Island live audio visualizer:
        [ 🔊   ▁ ▃ ▆ █ ▆ ▃ ▁   68%   + ]
        - Speaker icon reflecting actual mute/unmute state
        - 7 animated vertical sound bars scaled to system volume
        - Accurate volume percentage (e.g. '68%')
        - Subtle direction indicator ('+' for Volume Up, '−' for Volume Down)
        """
        now = time.perf_counter()
        vol = self._volume_scalar
        muted = self._volume_muted
        is_active = self._is_volume_active
        direction = self._volume_direction

        # 1. Speaker icon (reflecting mute state)
        cx_spk = 26.0
        if muted or vol <= 0.001:
            self.canvas.create_polygon(
                cx_spk - 8, cy - 3, cx_spk - 5, cy - 3,
                cx_spk - 2, cy - 6, cx_spk - 2, cy + 6,
                cx_spk - 5, cy + 3, cx_spk - 8, cy + 3,
                fill="#ffaa00", outline=""
            )
            self.canvas.create_line(
                cx_spk - 7, cy + 6, cx_spk + 6, cy - 6,
                fill="#ff4444", width=2, capstyle=tk.ROUND
            )
        else:
            self.canvas.create_polygon(
                cx_spk - 8, cy - 3, cx_spk - 5, cy - 3,
                cx_spk - 2, cy - 6, cx_spk - 2, cy + 6,
                cx_spk - 5, cy + 3, cx_spk - 8, cy + 3,
                fill="#00e5ff", outline=""
            )
            if vol > 0.05:
                self.canvas.create_arc(
                    cx_spk - 1, cy - 5, cx_spk + 5, cy + 5,
                    start=-45, extent=90, style=tk.ARC,
                    outline="#00e5ff", width=2
                )
            if vol > 0.50:
                self.canvas.create_arc(
                    cx_spk + 2, cy - 8, cx_spk + 10, cy + 8,
                    start=-45, extent=90, style=tk.ARC,
                    outline="#00e5ff", width=2
                )

        # 2. Sound bars (7 vertical bars with volume-proportional heights)
        bar_heights = self.get_sound_bar_heights(vol, is_muted=muted, is_active=is_active, now=now)
        self._bar_heights = bar_heights
        bars_cx = 90.0
        spacing = 6.5
        start_x = bars_cx - (len(bar_heights) - 1) * spacing / 2.0
        bar_color = "#4a4d57" if muted else "#00e5ff"

        for i, bh in enumerate(bar_heights):
            bx = start_x + i * spacing
            self.canvas.create_line(
                bx, cy - bh / 2.0, bx, cy + bh / 2.0,
                fill=bar_color, width=3, capstyle=tk.ROUND
            )

        # 3. Volume percentage text
        vol_pct = int(round(vol * 100.0))
        tx = 150.0
        text_color = "#888899" if muted else "#ffffff"
        self.canvas.create_text(
            tx, cy, anchor="center",
            text=f"{vol_pct}%",
            fill=text_color,
            font=("Segoe UI", 11, "bold")
        )

        # 4. Subtle direction indicator ('+' or '−')
        dx = 192.0
        if direction == "VOLUME_UP":
            self.canvas.create_text(
                dx, cy - 1, anchor="center",
                text="+",
                fill="#00ff66",
                font=("Segoe UI", 13, "bold")
            )
        elif direction == "VOLUME_DOWN":
            self.canvas.create_text(
                dx, cy - 1, anchor="center",
                text="−",
                fill="#ffaa00",
                font=("Segoe UI", 13, "bold")
            )

        # 5. Perimeter accent progress border
        if self._progress > 0 and not muted:
            segments = get_capsule_path(w, h, self._progress)
            for seg in segments:
                if seg[0] == 'line':
                    self.canvas.create_line(seg[1], seg[2], seg[3], seg[4], fill=self._island_color, width=1.5, capstyle=tk.ROUND)
                elif seg[0] == 'arc':
                    self.canvas.create_arc(seg[1], seg[2], seg[3], seg[4], start=seg[5], extent=seg[6], style=tk.ARC, outline=self._island_color, width=1.5)

    def _draw_island(self) -> None:
        self.canvas.delete("all")
        w = self.w
        h = self.h
        r = h / 2.0
        cy = h / 2.0

        # Base dark glass pill
        points = [
            r, 0, w - r, 0,
            w - r, 0, w, 0, w, r,
            w, r, w, h - r,
            w, h - r, w, h, w - r, h,
            w - r, h, r, h,
            r, h, 0, h, 0, h - r,
            0, h - r, 0, r,
            0, r, 0, 0, r, 0
        ]
        self.canvas.create_polygon(points, smooth=True, fill="#121216", outline="#282834", width=1)

        # Dynamic Island Volume Visualizer layout
        if self._island_mode == "VOLUME":
            self._draw_volume_visualizer(w, h, cy)
            return

        # Vector icon
        cx = 28.0
        self._draw_vector_icon(cx, cy)

        # Text rendering
        tx = 46.0
        if self._island_subtext:
            self.canvas.create_text(tx, cy - 8, anchor="w", text=self._island_text, fill="white", font=("Segoe UI", 10, "bold"))
            self.canvas.create_text(tx, cy + 9, anchor="w", text=self._island_subtext, fill="#9999aa", font=("Segoe UI", 8))
        else:
            self.canvas.create_text(tx, cy, anchor="w", text=self._island_text, fill="white", font=("Segoe UI", 11, "bold"))

        # Progress perimeter border
        if self._progress > 0:
            segments = get_capsule_path(w, h, self._progress)
            for seg in segments:
                if seg[0] == 'line':
                    self.canvas.create_line(seg[1], seg[2], seg[3], seg[4], fill=self._island_color, width=2, capstyle=tk.ROUND)
                elif seg[0] == 'arc':
                    self.canvas.create_arc(seg[1], seg[2], seg[3], seg[4], start=seg[5], extent=seg[6], style=tk.ARC, outline=self._island_color, width=2)

    def _animate_y(self, target_y: float, speed: float = HUD_ANIMATION_SPEED) -> None:
        dy = target_y - self.current_y
        self.current_y += dy * speed
        if abs(self.current_y - target_y) < 1.0:
            self.current_y = target_y
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{int(self.current_y)}")

    def update_state(self, ss_controller=None, is_paused: bool = False, runtime_status: dict | None = None) -> None:
        now = time.perf_counter()
        if now - self._last_ui_update < 0.033:
            return
        self._last_ui_update = now

        # State transitions
        if self._island_state in ("SUCCESS", "ERROR", "STATUS"):
            if now > self._toast_until:
                self._island_state = "RETRACTING"
                self._current_priority = 0
                self._island_subtext = ""
                self._reset_dimensions()
        elif ss_controller and ss_controller._state == ScreenshotState.HOLDING:
            rem = getattr(ss_controller, 'remaining', SCREENSHOT_HOLD_DURATION_S)
            prog = getattr(ss_controller, 'progress', 0.0)

            self._current_priority = PRIORITY_CRITICAL
            if self._island_state in ("IDLE", "RETRACTING"):
                self._island_state = "DROPPING"
                self._island_color = "#00ff66"
                self.root.deiconify()
                self.root.lift()
                self._is_hidden = False
            elif self._island_state == "DROPPING" and self.current_y == self.y_visible:
                self._island_state = "HOLDING"

            self._island_text = f"Hold still  {rem:.1f}s"
            self._island_subtext = ""
            self._island_icon = "CHECK"
            self._progress = min(1.0, max(0.0, float(prog)))
        elif ss_controller and getattr(ss_controller, '_display_until', 0) > now:
            if "CANCELLED" in getattr(ss_controller, 'hud_text', '') and self._island_state in ("DROPPING", "HOLDING"):
                self._island_state = "RETRACTING"
                self._progress = 0.0
                self._current_priority = 0
        else:
            if self._island_state in ("DROPPING", "HOLDING"):
                if self._island_mode == "VOLUME":
                    if now > self._toast_until and not self._is_volume_active:
                        self._island_state = "RETRACTING"
                        self._progress = 0.0
                        self._current_priority = 0
                else:
                    self._island_state = "RETRACTING"
                    self._progress = 0.0
                    self._current_priority = 0

        if ss_controller:
            if ss_controller._state == ScreenshotState.CAPTURED:
                if "CAPTURED" in getattr(ss_controller, 'hud_text', '') and self._island_state != "SUCCESS":
                    import re
                    m = re.search(r'\((.*?)\)', ss_controller.hud_text)
                    if m:
                        self.show_success_toast(m.group(1))
                    else:
                        self.show_success_toast()
            elif ss_controller._state == ScreenshotState.ERROR:
                if self._island_state != "ERROR":
                    self.show_error_toast()

        # Animation dispatch
        if self._island_state in ("DROPPING", "HOLDING", "SUCCESS", "ERROR", "STATUS"):
            self._animate_y(self.y_visible)
        elif self._island_state == "RETRACTING":
            self._animate_y(self.y_hidden)
            if self.current_y == self.y_hidden and not self._is_hidden:
                self.root.withdraw()
                self.root.update()
                self._is_hidden = True
                self._island_state = "IDLE"
                self._island_mode = ""
                self._is_volume_active = False
                self._progress = 0.0
                self._current_priority = 0
                self._island_subtext = ""
                self._reset_dimensions()
        elif self._island_state == "IDLE":
            self._progress = 0.0

        if not self._is_hidden and not self._force_hide:
            self._draw_island()
            self.root.update()

    def show_success_toast(self, filename: str | None = None) -> None:
        now = time.perf_counter()
        if not self._can_post_event(PRIORITY_CRITICAL, "Screenshot saved", now):
            return
        self._current_priority = PRIORITY_CRITICAL
        self._last_event_text = "Screenshot saved"
        self._last_event_time = now
        self._toast_until = now + HUD_TOAST_DURATION_S
        self._island_state = "SUCCESS"
        self._island_text = "Screenshot saved"
        self._island_subtext = ""
        self._island_icon = "CHECK"
        self._island_color = "#00ff66"
        self._progress = 1.0
        self._reset_dimensions()
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def show_error_toast(self) -> None:
        now = time.perf_counter()
        if not self._can_post_event(PRIORITY_CRITICAL, "Screenshot failed", now):
            return
        self._current_priority = PRIORITY_CRITICAL
        self._last_event_text = "Screenshot failed"
        self._last_event_time = now
        self._toast_until = now + HUD_TOAST_DURATION_S
        self._island_state = "ERROR"
        self._island_text = "Screenshot failed"
        self._island_subtext = ""
        self._island_icon = "CROSS"
        self._island_color = "#ff4444"
        self._progress = 1.0
        self._reset_dimensions()
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def show_app_toast(self, app_name: str = "WhatsApp", success: bool = True) -> None:
        now = time.perf_counter()
        text = f"Opening {app_name}" if success else f"{app_name} not found"
        if not self._can_post_event(PRIORITY_CRITICAL, text, now):
            return
        self._current_priority = PRIORITY_CRITICAL
        self._last_event_text = text
        self._last_event_time = now
        self._toast_until = now + HUD_TOAST_DURATION_S
        self._island_state = "SUCCESS" if success else "ERROR"
        self._island_text = text
        self._island_subtext = ""
        self._island_icon = "APP" if success else "CROSS"
        self._island_color = "#25D366" if success else "#ff4444"
        self._progress = 1.0
        self._reset_dimensions()
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def show_audio_toast(self, action: str, success: bool = True) -> None:
        now = time.perf_counter()
        if not success:
            text = "Audio control failed"
            icon = "CROSS"
            color = "#ff4444"
            state = "ERROR"
        elif action == "MUTED":
            text = "Muted"
            icon = "MUTE"
            color = "#ff9900"
            state = "SUCCESS"
        elif action in ("RESTORED", "UNMUTED"):
            text = "Unmuted" if action == "UNMUTED" else "Volume restored"
            icon = "UNMUTE"
            color = "#00ff66"
            state = "SUCCESS"
        else:
            text = f"Audio {action.lower()}"
            icon = "UNMUTE"
            color = "#00ff66"
            state = "SUCCESS"

        if not self._can_post_event(PRIORITY_CRITICAL, text, now):
            return
        self._current_priority = PRIORITY_CRITICAL
        self._last_event_text = text
        self._last_event_time = now
        self._toast_until = now + HUD_TOAST_DURATION_S
        self._island_state = state
        self._island_text = text
        self._island_subtext = ""
        self._island_icon = icon
        self._island_color = color
        self._progress = 1.0
        self._reset_dimensions()
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def show_ear_volume(
        self,
        direction_name: str,
        volume_scalar: float,
        is_active: bool,
        is_muted: bool | None = None,
    ) -> None:
        now = time.perf_counter()
        vol = max(0.0, min(1.0, float(volume_scalar)))
        vol_pct = int(round(vol * 100.0))

        # Determine mute state
        if is_muted is not None:
            muted = bool(is_muted)
        elif self._audio_controller is not None:
            muted = bool(getattr(self._audio_controller, 'is_muted', False))
        else:
            muted = (vol <= 0.001)

        self._volume_scalar = vol
        self._volume_muted = muted
        self._is_volume_active = is_active
        self._volume_direction = direction_name

        arrow = "↑" if direction_name == "VOLUME_UP" else "↓"
        icon = "MUTE" if muted else ("VOL_UP" if direction_name == "VOLUME_UP" else "VOL_DOWN")
        text = f"Volume {arrow}  {vol_pct}%"

        self._current_priority = PRIORITY_VOLUME
        self._last_event_text = text
        self._last_event_time = now
        self._island_mode = "VOLUME"
        self._island_text = text
        self._island_subtext = ""
        self._island_icon = icon
        self._island_color = "#ffaa00" if muted else "#00e5ff"
        self._progress = vol

        if not is_active:
            self._is_volume_active = False
            # When ending gesture, keep island visible briefly (0.8s) then dismiss smoothly
            self._toast_until = now + 0.8
            self._island_state = "SUCCESS"
            return

        self._toast_until = now + 1.2
        self._island_state = "HOLDING"
        self._set_dimensions(230, 44)
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def show_tracking_toast(self, transition: str) -> None:
        now = time.perf_counter()
        if transition == "LOST":
            text = "Hand lost"
            icon = "HAND_LOST"
            color = "#ffaa00"
        else:
            text = "Hand reacquired"
            icon = "HAND_FOUND"
            color = "#00e5ff"

        if not self._can_post_event(PRIORITY_TRACKING, text, now):
            return
        self._current_priority = PRIORITY_TRACKING
        self._last_event_text = text
        self._last_event_time = now
        self._toast_until = now + 1.2
        self._island_state = "SUCCESS"
        self._island_text = text
        self._island_subtext = ""
        self._island_icon = icon
        self._island_color = color
        self._progress = 1.0
        self._reset_dimensions()
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def show_pause_toast(self, is_paused: bool) -> None:
        now = time.perf_counter()
        text = "System Paused" if is_paused else "System Resumed"
        icon = "PAUSE" if is_paused else "PLAY"
        color = "#ffaa00" if is_paused else "#00ff66"

        if not self._can_post_event(PRIORITY_CRITICAL, text, now):
            return
        self._current_priority = PRIORITY_CRITICAL
        self._last_event_text = text
        self._last_event_time = now
        self._toast_until = now + 1.5
        self._island_state = "SUCCESS"
        self._island_text = text
        self._island_subtext = ""
        self._island_icon = icon
        self._island_color = color
        self._progress = 1.0
        self._reset_dimensions()
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def show_runtime_status(self, status_info: dict, duration: float = HUD_STATUS_DURATION_S) -> None:
        now = time.perf_counter()
        if not self._can_post_event(PRIORITY_STATUS, "STATUS", now):
            return

        fps = float(status_info.get("fps", 0.0))
        owner = str(status_info.get("owner", "NONE"))
        tracking = str(status_info.get("tracking", "No Hands"))
        paused = "PAUSED" if status_info.get("paused", False) else "ACTIVE"

        self._current_priority = PRIORITY_STATUS
        self._last_event_text = "STATUS"
        self._last_event_time = now
        self._toast_until = now + duration
        self._island_state = "STATUS"
        self._island_icon = "STATUS"
        self._island_color = "#00e5ff"
        self._island_text = f"Air Point · {fps:.0f} FPS · {paused}"
        self._island_subtext = f"Cursor: {owner} | Track: {tracking}"
        self._progress = 0.0
        self._set_dimensions(320, 52)
        self.root.deiconify()
        self.root.lift()
        self._is_hidden = False

    def close(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass


# Backward & Forward Compatibility Aliases
DynamicIslandHUD = ScreenshotOverlay
FeedbackOverlay = ScreenshotOverlay
