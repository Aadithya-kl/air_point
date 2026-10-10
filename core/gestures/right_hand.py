# core/gestures/right_hand.py
"""
Right-hand gesture processor.

State machine:
  IDLE
    â”œâ”€ fist                      â†’ LOCKED
    â”œâ”€ peace sign                â†’ SCROLLING
    â””â”€ thumb+index pinch         â†’ LEFT_PINCH_PENDING_DRAG

  LOCKED
    â””â”€ any finger extends        â†’ IDLE

  SCROLLING
    â”œâ”€ wrist flick up            â†’ scroll(+ticks)  [stay SCROLLING]
    â”œâ”€ wrist flick down          â†’ scroll(-ticks)  [stay SCROLLING]
    â””â”€ peace sign released       â†’ IDLE

  LEFT_PINCH_PENDING_DRAG
    â”œâ”€ pinch released < DRAG_HOLD_SECONDS  â†’ left_click() or double_click() â†’ IDLE
    â””â”€ pinch held â‰¥ DRAG_HOLD_SECONDS      â†’ drag_start() â†’ DRAGGING

  DRAGGING
    â””â”€ pinch released            â†’ drag_end() â†’ IDLE
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
from core.gestures.utils import dist3d, is_fist, is_peace_sign, angle3d, is_three_fingers, is_open_palm, is_palm_facing_camera_right_hand

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
    SCREENSHOT_HOLD = auto()
    SCREENSHOT_CAPTURED = auto()


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
        self._screenshot_start_time: float | None = None
        self._screenshot_last_seen_time: float | None = None
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
            # Hand left frame â€” clean up drag if active
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

        fist = is_fist(landmarks)
        if self._state == _State.LOCKED:
            if not fist:
                self._state = _State.IDLE
                logger.debug("Unlocked")
            return  # no cursor, no gestures while locked

        if fist and self._state not in (_State.LEFT_PINCH_PENDING_DRAG, _State.DRAGGING):
            if self._state == _State.DRAGGING:
                self._actuator.drag_end()
            self._state = _State.LOCKED
            logger.debug("Locked (fist)")
            return



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
                    
            # â”€â”€ Phase 3: Zoom Gestures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
                            
                            # DIAGNOSTIC FEEDBACK
                            logger.info(
                                f"ZOOM DIAGNOSTIC | State: {self._state.name} | Peace: {peace} | "
                                f"DistNorm: {d_index_mid_norm:.3f} | Baseline: {self._zoom_baseline:.3f} | "
                                f"Delta: {delta:.3f} | Backend: {ZOOM_BACKEND}"
                            )
                            
                            if delta > ZOOM_SPREAD_THRESHOLD:
                                if ZOOM_BACKEND == "windows_magnifier":
                                    self._actuator.mag_zoom_in()
                                else:
                                    self._actuator.zoom_in()
                                self._zoom_baseline = d_index_mid_norm
                                self._last_zoom_step_time = now
                                self._click_state_text = "ZOOM: IN"
                                self._display_text_until = now + 0.3
                                logger.info(f"ZOOM DIAGNOSTIC | ACTION DISPATCHED: IN (delta={delta:.3f})")
                            elif delta < ZOOM_CLOSE_THRESHOLD:
                                if ZOOM_BACKEND == "windows_magnifier":
                                    self._actuator.mag_zoom_out()
                                else:
                                    self._actuator.zoom_out()
                                self._zoom_baseline = d_index_mid_norm
                                self._last_zoom_step_time = now
                                self._click_state_text = "ZOOM: OUT"
                                self._display_text_until = now + 0.3
                                logger.info(f"ZOOM DIAGNOSTIC | ACTION DISPATCHED: OUT (delta={delta:.3f})")
                                
                    if now <= getattr(self, "_display_text_until", 0) and self._click_state_text in ("ZOOM: IN", "ZOOM: OUT"):
                        pass # keep text
                    elif self._state == _State.ZOOM_ACTIVE:
                        self._click_state_text = "ZOOM: ACTIVE"


    @property
    def cursor_pos(self):
        return getattr(self, '_cursor_pos', None)
        
    @property
    def right_state(self) -> str:
        return self._state.name
        
    @property
    def click_state_text(self) -> str:
        return self._click_state_text
        
    @property
    def last_pinch_norm(self) -> float:
        return getattr(self, '_last_pinch_norm', 0.0)

    @property
    def is_cursor_locked(self) -> bool:
        return getattr(self, '_locked_cursor_pos', None) is not None
