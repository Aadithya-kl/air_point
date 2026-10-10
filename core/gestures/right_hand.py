# core/gestures/right_hand.py
"""
Right-hand gesture processor.

State machine:
  IDLE
    ├─ fist                      → LOCKED
    ├─ peace sign                → SCROLLING
    └─ thumb+index pinch         → LEFT_PINCH_PENDING_DRAG

  LOCKED
    └─ any finger extends        → IDLE

  SCROLLING
    ├─ wrist flick up            → scroll(+ticks)  [stay SCROLLING]
    ├─ wrist flick down          → scroll(-ticks)  [stay SCROLLING]
    └─ peace sign released       → IDLE

  LEFT_PINCH_PENDING_DRAG
    ├─ pinch released < DRAG_HOLD_SECONDS  → left_click() or double_click() → IDLE
    └─ pinch held ≥ DRAG_HOLD_SECONDS      → drag_start() → DRAGGING

  DRAGGING
    └─ pinch released            → drag_end() → IDLE
"""

import time
import logging
from enum import Enum, auto

from config import (
    CAMERA_FPS,
    THUMB_INDEX_CLICK_DIST,
    THUMB_MIDDLE_CLICK_DIST,
    DRAG_HOLD_SECONDS,
    GESTURE_COOLDOWN_SECONDS,
    DOUBLE_CLICK_WINDOW_S,
    WRIST_VELOCITY_THRESHOLD,
    SCROLL_TICK_SCALE,
    SCROLL_COOLDOWN_S,
    ONE_EURO_MINCUTOFF,
    ONE_EURO_BETA,
    ONE_EURO_DCUTOFF,
    TAP_BENT_ANGLE,
    TAP_STRAIGHT_ANGLE,
    TAP_MAX_DURATION_S,
    TAP_DOUBLE_WINDOW_S,
    USE_PINCH_CLICK,
)
from core.tracker import Landmark
from core.filter import OneEuroFilter
from core.display import VirtualDesktop, TrackpadZone, map_to_desktop
from core.actuator import MouseActuator
from core.gestures.utils import dist3d, is_fist, is_peace_sign, angle3d, is_three_fingers

logger = logging.getLogger(__name__)


class _State(Enum):
    IDLE = auto()
    PINCH_CANDIDATE = auto()
    PINCH_LOCKED = auto()
    LEFT_PINCH_PENDING_DRAG = auto()
    DRAGGING = auto()
    SCROLLING = auto()
    LOCKED = auto()
    CLICK_PENDING = auto()
    ZOOM_ACTIVATING = auto()
    ZOOM_ACTIVE = auto()
    TAP_BENT = auto()
    TAP_WAITING_FOR_SECOND = auto()
    TAP_BENT_SECOND = auto()
    SCREENSHOT_PENDING = auto()


class RightHandProcessor:
    """
    Processes right-hand landmarks each frame.
    Call process(landmarks) where landmarks is list[Landmark] | None.
    None means no right hand detected this frame.
    """

    def __init__(
        self,
        actuator: MouseActuator,
        desktop: VirtualDesktop,
        trackpad: TrackpadZone,
    ) -> None:
        self._actuator = actuator
        self._desktop = desktop
        self._trackpad = trackpad
        self._state = _State.IDLE
        self._pinch_start_time: float | None = None
        self._tap_start_time: float | None = None
        self._first_tap_time: float | None = None
        self._zoom_baseline: float | None = None
        self._screenshot_start_y: float | None = None
        self._last_click_time: float = 0.0
        self._last_gesture_time: float = 0.0
        self._last_scroll_time: float = 0.0
        self._last_screenshot_time: float = 0.0
        self._prev_wrist_y: float | None = None
        self._last_frame_time: float | None = None
        self._click_state_text: str = "DISABLED"
        self._last_pinch_norm: float = 0.0
        self._locked_cursor_pos: tuple[int, int] | None = None
        self._zoom_start_time: float | None = None
        self._last_zoom_step_time: float = 0.0
        self._click_pending_start_time: float | None = None
        self._last_click_pos: tuple[int, int] | None = None
        self._filter_x = OneEuroFilter(
            freq=float(CAMERA_FPS),
            mincutoff=ONE_EURO_MINCUTOFF,
            beta=ONE_EURO_BETA,
            dcutoff=ONE_EURO_DCUTOFF,
        )
        self._filter_y = OneEuroFilter(
            freq=float(CAMERA_FPS),
            mincutoff=ONE_EURO_MINCUTOFF,
            beta=ONE_EURO_BETA,
            dcutoff=ONE_EURO_DCUTOFF,
        )
        # Visual filters for the 5 fingertips (Thumb:4, Index:8, Middle:12, Ring:16, Pinky:20)
        self.visual_tips: dict[int, tuple[float, float, float]] = {}
        self._visual_filters = {}
        for tip_idx in [4, 8, 12, 16, 20]:
            self._visual_filters[tip_idx] = (
                OneEuroFilter(freq=float(CAMERA_FPS), mincutoff=1.0, beta=0.0, dcutoff=1.0),
                OneEuroFilter(freq=float(CAMERA_FPS), mincutoff=1.0, beta=0.0, dcutoff=1.0)
            )

    def process(self, landmarks: list[Landmark] | None) -> None:
        from config import POINTER_ONLY_MODE
        if landmarks is None:
            # Hand left frame — clean up drag if active
            if self._state == _State.DRAGGING:
                self._actuator.drag_end()
            if self._state == _State.CLICK_PENDING:
                # Cancel pending click safely if hand lost
                self._click_pending_start_time = None
            self._state = _State.IDLE
            self._pinch_start_time = None
            self._prev_wrist_y = None
            self._last_frame_time = None
            self._click_state_text = "DISABLED"
            self._locked_cursor_pos = None
            self._zoom_start_time = None
            self._filter_x.reset()
            self._filter_y.reset()
            for fx, fy in self._visual_filters.values():
                fx.reset()
                fy.reset()
            self.visual_tips.clear()
            return

        now = time.perf_counter()

        # Update visual tips
        self.visual_tips.clear()
        for tip_idx in [4, 8, 12, 16, 20]:
            lm = landmarks[tip_idx]
            fx, fy = self._visual_filters[tip_idx]
            vx = fx(lm.x, now)
            vy = fy(lm.y, now)
            # Storing original z for completeness if needed
            self.visual_tips[tip_idx] = (vx, vy, lm.z)

        filtered_x = self._filter_x(landmarks[8].x, now)  # index tip
        filtered_y = self._filter_y(landmarks[8].y, now)
        raw_screen_x, raw_screen_y = map_to_desktop(filtered_x, filtered_y, self._trackpad, self._desktop)
        
        screen_x, screen_y = raw_screen_x, raw_screen_y
        # If cursor is locked during a pinch or waiting, override the output coordinates
        if self._locked_cursor_pos is not None:
            screen_x, screen_y = self._locked_cursor_pos
            
        self._cursor_pos = (screen_x, screen_y)
        self._actuator.move(screen_x, screen_y)

        from config import PINCH_CLICK_ENABLED, POINTER_ONLY_MODE
        if POINTER_ONLY_MODE:
            self._state = _State.IDLE
            self._click_state_text = "DISABLED"
            return
            
        if PINCH_CLICK_ENABLED:
            from config import (
                PINCH_START_NORM_DIST, PINCH_RELEASE_NORM_DIST, PINCH_CONFIRM_S, 
                PINCH_COOLDOWN_S,
                SINGLE_CLICK_MODE, DOUBLE_CLICK_GESTURE_WINDOW_MS, DOUBLE_CLICK_POSITION_TOLERANCE_PX
            )
            
            # Using same tolerance variable for both modes for simplicity
            DOUBLE_PINCH_WINDOW_S = DOUBLE_CLICK_GESTURE_WINDOW_MS / 1000.0
            DOUBLE_PINCH_TOL_PX = DOUBLE_CLICK_POSITION_TOLERANCE_PX
            
            # Normalize pinch distance by palm width (index MCP to pinky MCP)
            palm_width = dist3d(landmarks[5], landmarks[17])
            if palm_width < 1e-6:
                palm_width = 1e-6
                
            pinch_dist_norm = dist3d(landmarks[4], landmarks[8]) / palm_width
            self._last_pinch_norm = pinch_dist_norm
            
            is_comfortable = (SINGLE_CLICK_MODE == "comfortable")
            
            if self._state == _State.CLICK_PENDING:
                self._click_state_text = "CLICK PENDING"
                window_s = DOUBLE_CLICK_GESTURE_WINDOW_MS / 1000.0
                if self._click_pending_start_time is not None and (now - self._click_pending_start_time) > window_s:
                    # Expired -> Single click
                    self._actuator.left_click()
                    logger.debug("Buffered SINGLE CLICK triggered")
                    
                    if pinch_dist_norm < PINCH_RELEASE_NORM_DIST:
                        self._state = _State.PINCH_LOCKED
                        self._click_state_text = "SINGLE CLICK"
                    else:
                        self._state = _State.IDLE
                        self._locked_cursor_pos = None
                        self._click_state_text = "SINGLE CLICK"
                        
                    self._display_text_until = now + 0.5
                else:
                    if pinch_dist_norm < PINCH_START_NORM_DIST and (now - self._last_click_time) >= PINCH_COOLDOWN_S:
                        # Check position tolerance using physical hand position (raw_screen_x)
                        valid_pos = False
                        if self._locked_cursor_pos:
                            px, py = self._locked_cursor_pos
                            if abs(raw_screen_x - px) <= DOUBLE_CLICK_POSITION_TOLERANCE_PX and abs(raw_screen_y - py) <= DOUBLE_CLICK_POSITION_TOLERANCE_PX:
                                valid_pos = True
                        if valid_pos:
                            self._state = _State.PINCH_CANDIDATE
                            self._pinch_start_time = now
                            self._click_state_text = "DOUBLE-CLICK CANDIDATE"
                        else:
                            # Too far -> resolve the pending click, start new one
                            self._actuator.left_click()
                            logger.debug("Buffered SINGLE CLICK triggered (moved too far)")
                            self._click_pending_start_time = None
                            self._state = _State.PINCH_CANDIDATE
                            self._pinch_start_time = now
                            self._locked_cursor_pos = (raw_screen_x, raw_screen_y)
                            self._click_state_text = "CANDIDATE"

            elif self._state == _State.IDLE:
                if (now - self._last_click_time) < PINCH_COOLDOWN_S:
                    self._click_state_text = "COOLDOWN"
                else:
                    self._click_state_text = "READY"
                    
                if pinch_dist_norm < PINCH_START_NORM_DIST and (now - self._last_click_time) >= PINCH_COOLDOWN_S:
                    self._state = _State.PINCH_CANDIDATE
                    self._pinch_start_time = now
                    
                    locked_x, locked_y = raw_screen_x, raw_screen_y
                    if not is_comfortable:
                        if self._last_click_pos is not None and (now - self._last_click_time) <= DOUBLE_PINCH_WINDOW_S:
                            prev_x, prev_y = self._last_click_pos
                            if abs(locked_x - prev_x) <= DOUBLE_PINCH_TOL_PX and abs(locked_y - prev_y) <= DOUBLE_PINCH_TOL_PX:
                                locked_x, locked_y = prev_x, prev_y
                                logger.debug("Snapped pinch for instant double-click")
                    
                    self._locked_cursor_pos = (locked_x, locked_y)
                    self._click_state_text = "CANDIDATE"
                    
            elif self._state == _State.PINCH_CANDIDATE:
                if self._click_state_text != "DOUBLE-CLICK CANDIDATE":
                    self._click_state_text = "CANDIDATE"
                
                if pinch_dist_norm > PINCH_RELEASE_NORM_DIST:
                    # Released before confirmation
                    if self._click_pending_start_time is not None:
                        # Revert back to pending click
                        self._state = _State.CLICK_PENDING
                        self._click_state_text = "CLICK PENDING"
                    else:
                        self._state = _State.IDLE
                        self._locked_cursor_pos = None
                    self._pinch_start_time = None
                    
                elif self._pinch_start_time is not None and (now - self._pinch_start_time) >= PINCH_CONFIRM_S:
                    # Confirmed
                    if is_comfortable:
                        if self._click_pending_start_time is not None:
                            # It's a double click!
                            self._actuator.double_click()
                            self._click_pending_start_time = None
                            self._state = _State.PINCH_LOCKED
                            self._click_state_text = "DOUBLE CLICK"
                            self._display_text_until = now + 0.5
                            logger.debug("Buffered DOUBLE CLICK triggered")
                        else:
                            # First pinch confirmed, enter pending
                            self._state = _State.CLICK_PENDING
                            self._click_pending_start_time = now
                            self._click_state_text = "CLICK PENDING"
                            self._last_click_time = now
                            logger.debug("First pinch confirmed -> CLICK PENDING")
                    else:
                        # Instant mode
                        self._actuator.left_click()
                        is_double = False
                        if self._last_click_pos is not None and (now - self._last_click_time) <= DOUBLE_PINCH_WINDOW_S:
                            prev_x, prev_y = self._last_click_pos
                            curr_x, curr_y = self._locked_cursor_pos
                            if curr_x == prev_x and curr_y == prev_y:
                                is_double = True
                        
                        self._last_click_time = now
                        self._last_click_pos = self._locked_cursor_pos
                        self._state = _State.PINCH_LOCKED
                        self._click_state_text = "DOUBLE CLICK" if is_double else "SINGLE CLICK"
                        self._display_text_until = now + 0.5
                        logger.debug("Instant %s triggered", self._click_state_text)
                    
            elif self._state == _State.PINCH_LOCKED:
                if now > getattr(self, "_display_text_until", 0):
                    self._click_state_text = "WAITING FOR RELEASE"
                if pinch_dist_norm > PINCH_RELEASE_NORM_DIST:
                    self._state = _State.IDLE
                    self._locked_cursor_pos = None
                    self._pinch_start_time = None
                    self._click_state_text = "READY"
                    
            # ── Phase 3: Zoom Gestures ───────────────────────────────────────────────
            from config import (
                ZOOM_ENABLED, ZOOM_BACKEND, ZOOM_ACTIVATION_DURATION_S,
                ZOOM_EXIT_DURATION_S, ZOOM_COOLDOWN_S, ZOOM_SPREAD_THRESHOLD, ZOOM_CLOSE_THRESHOLD
            )
            
            if ZOOM_ENABLED:
                peace = is_peace_sign(landmarks)
                d_index_mid_norm = dist3d(landmarks[8], landmarks[12]) / palm_width
                
                if self._state not in (_State.ZOOM_ACTIVATING, _State.ZOOM_ACTIVE):
                    if peace and self._state == _State.IDLE:
                        self._state = _State.ZOOM_ACTIVATING
                        self._zoom_start_time = now
                        self._click_state_text = "ZOOM: ACTIVATING"
                
                elif self._state == _State.ZOOM_ACTIVATING:
                    if not peace:
                        self._state = _State.IDLE
                        self._click_state_text = "READY"
                        self._zoom_start_time = None
                    elif self._zoom_start_time is not None and (now - self._zoom_start_time) >= ZOOM_ACTIVATION_DURATION_S:
                        self._state = _State.ZOOM_ACTIVE
                        self._zoom_baseline = d_index_mid_norm
                        self._locked_cursor_pos = (raw_screen_x, raw_screen_y)
                        self._click_state_text = "ZOOM: ACTIVE"
                        self._last_zoom_step_time = now
                        logger.debug("Zoom mode activated, baseline=%.3f", self._zoom_baseline)
                        
                elif self._state == _State.ZOOM_ACTIVE:
                    self._click_state_text = "ZOOM: ACTIVE"
                    if not peace:
                        if self._zoom_start_time is None:
                            self._zoom_start_time = now # Use as exit timer
                        elif (now - self._zoom_start_time) >= ZOOM_EXIT_DURATION_S:
                            self._state = _State.IDLE
                            self._locked_cursor_pos = None
                            self._click_state_text = "READY"
                            self._zoom_start_time = None
                            logger.debug("Zoom mode exited")
                    else:
                        self._zoom_start_time = None # Reset exit timer
                        
                        if (now - self._last_zoom_step_time) >= ZOOM_COOLDOWN_S:
                            delta = d_index_mid_norm - self._zoom_baseline
                            if delta > ZOOM_SPREAD_THRESHOLD:
                                if ZOOM_BACKEND == "windows_magnifier":
                                    self._actuator.mag_zoom_in()
                                else:
                                    self._actuator.zoom_in()
                                self._zoom_baseline = d_index_mid_norm
                                self._last_zoom_step_time = now
                                self._click_state_text = "ZOOM: IN"
                                self._display_text_until = now + 0.3
                                logger.debug("Zoom In (delta=%.3f)", delta)
                            elif delta < ZOOM_CLOSE_THRESHOLD:
                                if ZOOM_BACKEND == "windows_magnifier":
                                    self._actuator.mag_zoom_out()
                                else:
                                    self._actuator.zoom_out()
                                self._zoom_baseline = d_index_mid_norm
                                self._last_zoom_step_time = now
                                self._click_state_text = "ZOOM: OUT"
                                self._display_text_until = now + 0.3
                                logger.debug("Zoom Out (delta=%.3f)", delta)
                                
                    if now <= getattr(self, "_display_text_until", 0) and self._click_state_text in ("ZOOM: IN", "ZOOM: OUT"):
                        pass # keep text
                    elif self._state == _State.ZOOM_ACTIVE:
                        self._click_state_text = "ZOOM: ACTIVE"

            return # Skip the rest of the legacy gesture engine

        # Compute wrist velocity (landmark 0 = wrist joint)
        wrist_y = landmarks[0].y
        dt = (now - self._last_frame_time) if self._last_frame_time is not None else (1.0 / CAMERA_FPS)
        dt = max(dt, 1e-6)  # guard divide-by-zero
        wrist_vel_y = (
            (wrist_y - self._prev_wrist_y) / dt
            if self._prev_wrist_y is not None
            else 0.0
        )
        self._prev_wrist_y = wrist_y
        self._last_frame_time = now

        fist = is_fist(landmarks)
        peace = is_peace_sign(landmarks)
        three_fingers = is_three_fingers(landmarks)
        d_thumb_idx = dist3d(landmarks[4], landmarks[8])
        d_thumb_mid = dist3d(landmarks[4], landmarks[12])
        thumb_idx_pinch = d_thumb_idx < THUMB_INDEX_CLICK_DIST

        # ── LOCKED state ──────────────────────────────────────────────────────
        if self._state == _State.LOCKED:
            if not fist:
                self._state = _State.IDLE
                logger.debug("Unlocked")
            return  # no cursor, no gestures while locked

        # ── Fist → LOCKED ─────────────────────────────────────────────────────
        if fist and self._state not in (_State.LEFT_PINCH_PENDING_DRAG, _State.DRAGGING):
            if self._state == _State.DRAGGING:
                self._actuator.drag_end()
            self._state = _State.LOCKED
            logger.debug("Locked (fist)")
            return

        # ── SCREENSHOT ────────────────────────────────────────────────────────
        from config import SCREENSHOT_DIR, SCREENSHOT_SWIPE_DIST, SCREENSHOT_SWIPE_VEL, SCREENSHOT_COOLDOWN_S

        if self._state == _State.SCREENSHOT_PENDING:
            if not three_fingers:
                self._state = _State.IDLE
                self._screenshot_start_y = None
                return
            if wrist_vel_y > SCREENSHOT_SWIPE_VEL and self._screenshot_start_y is not None:
                if (wrist_y - self._screenshot_start_y) > SCREENSHOT_SWIPE_DIST:
                    if (now - self._last_screenshot_time) > SCREENSHOT_COOLDOWN_S:
                        self._actuator.take_screenshot(SCREENSHOT_DIR)
                        self._last_screenshot_time = now
                        logger.debug("Screenshot taken")
                    # Must release gesture before taking another
                    self._state = _State.IDLE
                    self._screenshot_start_y = None
            return  # freeze cursor while swiping
        
        if three_fingers and self._state == _State.IDLE:
            if (now - self._last_screenshot_time) > SCREENSHOT_COOLDOWN_S:
                self._state = _State.SCREENSHOT_PENDING
                self._screenshot_start_y = wrist_y
                logger.debug("Entered screenshot pending")
            return

        # ── SCROLL_ZOOM state ─────────────────────────────────────────────────
        if self._state == _State.SCROLLING:
            if not peace:
                self._state = _State.IDLE
                self._zoom_baseline = None
                return
            
            # 1. Scroll logic
            if abs(wrist_vel_y) > WRIST_VELOCITY_THRESHOLD:
                if (now - self._last_scroll_time) >= SCROLL_COOLDOWN_S:
                    ticks = int(min(5, max(1, abs(wrist_vel_y) / SCROLL_TICK_SCALE)))
                    self._actuator.scroll(ticks if wrist_vel_y < 0 else -ticks)
                    self._last_scroll_time = now
                    logger.debug("Scroll %s ticks=%d vel=%.4f", "up" if wrist_vel_y < 0 else "down", ticks, wrist_vel_y)

            # 2. Zoom logic
            from config import ZOOM_TICK_DELTA, ZOOM_MODE
            d_index_mid = dist3d(landmarks[8], landmarks[12])
            if self._zoom_baseline is None:
                self._zoom_baseline = d_index_mid
            else:
                if d_index_mid > self._zoom_baseline + ZOOM_TICK_DELTA:
                    if ZOOM_MODE == "APP":
                        self._actuator.zoom_in()
                    else:
                        self._actuator.mag_zoom_in()
                    self._zoom_baseline += ZOOM_TICK_DELTA
                    logger.debug("Zoom in")
                elif d_index_mid < self._zoom_baseline - ZOOM_TICK_DELTA:
                    if ZOOM_MODE == "APP":
                        self._actuator.zoom_out()
                    else:
                        self._actuator.mag_zoom_out()
                    self._zoom_baseline -= ZOOM_TICK_DELTA
                    logger.debug("Zoom out")
            return  # cursor suspended while scrolling/zooming

        # ── Enter SCROLL_ZOOM ─────────────────────────────────────────────────
        if peace and self._state == _State.IDLE:
            self._state = _State.SCROLLING
            self._zoom_baseline = dist3d(landmarks[8], landmarks[12])
            logger.debug("Entered scroll/zoom mode (peace sign)")
            return

        # ── Right click ───────────────────────────────────────────────────────
        if (
            d_thumb_mid < THUMB_MIDDLE_CLICK_DIST
            and self._state == _State.IDLE
            and (now - self._last_gesture_time) >= GESTURE_COOLDOWN_SECONDS
        ):
            self._actuator.right_click()
            self._last_gesture_time = now
            logger.debug("Right click (d=%.4f)", d_thumb_mid)
            return

        index_pip_angle = angle3d(landmarks[5], landmarks[6], landmarks[8])
        index_bent = index_pip_angle < TAP_BENT_ANGLE
        index_straight = index_pip_angle > TAP_STRAIGHT_ANGLE

        # ── Left click / drag state machine ───────────────────────────────────
        if USE_PINCH_CLICK:
            if self._state == _State.IDLE:
                if thumb_idx_pinch:
                    self._state = _State.LEFT_PINCH_PENDING_DRAG
                    self._pinch_start_time = now

            elif self._state == _State.LEFT_PINCH_PENDING_DRAG:
                if not thumb_idx_pinch:
                    if (now - self._last_gesture_time) >= GESTURE_COOLDOWN_SECONDS:
                        if (now - self._last_click_time) <= DOUBLE_CLICK_WINDOW_S and self._last_click_time > 0:
                            self._actuator.double_click()
                            logger.debug("Double click")
                            self._last_click_time = 0.0
                        else:
                            self._actuator.left_click()
                            logger.debug("Left click (d=%.4f)", d_thumb_idx)
                            self._last_click_time = now
                        self._last_gesture_time = now
                    self._state = _State.IDLE
                    self._pinch_start_time = None

                elif self._pinch_start_time is not None and (now - self._pinch_start_time) >= DRAG_HOLD_SECONDS:
                    self._actuator.drag_start()
                    self._state = _State.DRAGGING
                    logger.debug("Drag start")

            elif self._state == _State.DRAGGING:
                if not thumb_idx_pinch:
                    self._actuator.drag_end()
                    self._state = _State.IDLE
                    self._pinch_start_time = None
                    logger.debug("Drag end")
        else:
            if self._state == _State.IDLE:
                if index_bent:
                    self._state = _State.TAP_BENT
                    self._tap_start_time = now
            elif self._state == _State.TAP_BENT:
                if index_straight:
                    if self._tap_start_time is not None and (now - self._tap_start_time) <= TAP_MAX_DURATION_S:
                        self._state = _State.TAP_WAITING_FOR_SECOND
                        self._first_tap_time = now
                        logger.debug("First tap registered")
                    else:
                        self._state = _State.IDLE
                elif self._tap_start_time is not None and (now - self._tap_start_time) >= DRAG_HOLD_SECONDS:
                    self._actuator.drag_start()
                    self._state = _State.DRAGGING
                    logger.debug("Drag start (tap hold)")
            elif self._state == _State.TAP_WAITING_FOR_SECOND:
                if self._first_tap_time is not None and (now - self._first_tap_time) > TAP_DOUBLE_WINDOW_S:
                    self._state = _State.IDLE
                    logger.debug("Double tap window expired")
                elif index_bent:
                    self._state = _State.TAP_BENT_SECOND
                    self._tap_start_time = now
            elif self._state == _State.TAP_BENT_SECOND:
                if index_straight:
                    if self._tap_start_time is not None and (now - self._tap_start_time) <= TAP_MAX_DURATION_S:
                        self._actuator.left_click()
                        self._last_gesture_time = now
                        logger.debug("Double tap left click")
                    self._state = _State.IDLE
                elif self._tap_start_time is not None and (now - self._tap_start_time) >= DRAG_HOLD_SECONDS:
                    self._state = _State.IDLE

            elif self._state == _State.DRAGGING:
                if index_straight:
                    self._actuator.drag_end()
                    self._state = _State.IDLE
                    logger.debug("Drag end (tap release)")

        # ── Cursor movement (IDLE and DRAGGING only) ──────────────────────────
        filtered_x = self._filter_x(landmarks[8].x, now)  # index tip
        filtered_y = self._filter_y(landmarks[8].y, now)
        screen_x, screen_y = map_to_desktop(filtered_x, filtered_y, self._trackpad, self._desktop)
        self._cursor_pos = (screen_x, screen_y)
        self._actuator.move(screen_x, screen_y)

    @property
    def cursor_pos(self) -> tuple[int, int] | None:
        return getattr(self, "_cursor_pos", None)
        
    @property
    def right_state(self) -> str:
        return self._state.name
        
    @property
    def click_state_text(self) -> str:
        return self._click_state_text
        
    @property
    def last_pinch_norm(self) -> float:
        return getattr(self, "_last_pinch_norm", 0.0)

    @property
    def is_cursor_locked(self) -> bool:
        return getattr(self, "_locked_cursor_pos", None) is not None
