"""
Left-hand gesture processor - Drag-and-Drop and Smooth Pointer Tracking.
"""

import time
import logging
from enum import Enum, auto

from config import (
    CAMERA_FPS,
    LEFT_DRAG_ENABLED,
    LEFT_DRAG_CONFIRM_S,
    LEFT_DRAG_ACTIVATION_DURATION_S,
    LEFT_DRAG_RELEASE_NORM_DIST,
    LEFT_DRAG_START_NORM_DIST,
    LEFT_DRAG_LOST_TIMEOUT_S,
    ONE_EURO_MINCUTOFF,
    ONE_EURO_BETA,
    ONE_EURO_DCUTOFF,
)
from core.tracker import Landmark
from core.actuator import MouseActuator
from core.gestures.utils import dist3d, is_fist, is_open_palm
from core.filter import OneEuroFilter
from core.display import VirtualDesktop, TrackpadZone, map_to_desktop

logger = logging.getLogger(__name__)


class _LeftState(Enum):
    IDLE = auto()
    GRAB_CANDIDATE = auto()
    DRAGGING = auto()
    RELEASING = auto()


class LeftHandProcessor:
    """
    Processes left-hand landmarks each frame.
    Responsibilities:
    - Smooth left index finger cursor tracking across the full desktop.
    - Persistent OneEuroFilter state across frames without palm-orientation freezing.
    - Reliable thumb-index pinch-and-hold drag-and-drop.
    - Safe lost-tracking cleanup to prevent stuck mouse button.
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
        
        self._state = _LeftState.IDLE
        self._pinch_start_time: float | None = None
        self._display_text_until: float = 0.0
        self._drag_state_text = "READY"
        self.just_started_drag = False
        
        # Pointer filters (matched to right hand for identical responsiveness & smoothness)
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
        self.cursor_pos: tuple[int, int] | None = None
        self.is_pointing: bool = False
        self._last_pinch_norm: float = 0.0

        # Visual filters for 5 fingertips (Thumb:4, Index:8, Middle:12, Ring:16, Pinky:20)
        self.visual_tips: dict[int, tuple[float, float, float]] = {}
        self._visual_filters = {}
        for tip_idx in [4, 8, 12, 16, 20]:
            self._visual_filters[tip_idx] = (
                OneEuroFilter(freq=float(CAMERA_FPS), mincutoff=1.0, beta=0.05, dcutoff=1.0),
                OneEuroFilter(freq=float(CAMERA_FPS), mincutoff=1.0, beta=0.05, dcutoff=1.0),
            )

    def process(self, landmarks: list[Landmark] | None) -> None:
        self.just_started_drag = False
        now = time.perf_counter()

        if landmarks is None:
            for fx, fy in self._visual_filters.values():
                fx.reset()
                fy.reset()
            self.visual_tips.clear()
            self.cursor_pos = None
            self.is_pointing = False
            self._filter_x.reset()
            self._filter_y.reset()

            # Tracking loss safety policy: safely drop immediately if hand lost
            if self._state in (_LeftState.DRAGGING, _LeftState.GRAB_CANDIDATE):
                if self._state == _LeftState.DRAGGING:
                    self._actuator.drag_end()
                self._state = _LeftState.IDLE
                self._drag_state_text = "READY"
                self._pinch_start_time = None
            return

        # Update visual tips
        self.visual_tips.clear()
        for tip_idx in [4, 8, 12, 16, 20]:
            lm = landmarks[tip_idx]
            fx, fy = self._visual_filters[tip_idx]
            vx = fx(lm.x, now)
            vy = fy(lm.y, now)
            self.visual_tips[tip_idx] = (vx, vy, lm.z)

        # Calculate normalized pinch distance (thumb tip 4 to index tip 8 normalized by palm width)
        palm_width = dist3d(landmarks[5], landmarks[17])
        if palm_width < 1e-6:
            palm_width = 1e-6
        pinch_dist_norm = dist3d(landmarks[4], landmarks[8]) / palm_width
        self._last_pinch_norm = pinch_dist_norm

        # Continuous smooth pointer tracking for left index fingertip (Landmark 8)
        # Separated from palm orientation so facing palm toward camera never freezes the pointer.
        fist = is_fist(landmarks)
        if fist:
            self.is_pointing = False
            self.cursor_pos = None
        else:
            filtered_x = self._filter_x(landmarks[8].x, now)
            filtered_y = self._filter_y(landmarks[8].y, now)
            raw_screen_x, raw_screen_y = map_to_desktop(filtered_x, filtered_y, self._trackpad, self._desktop)
            self.cursor_pos = (raw_screen_x, raw_screen_y)
            self.is_pointing = True

        # Left-Hand Drag-and-Drop State Machine
        if LEFT_DRAG_ENABLED:
            if self._state == _LeftState.IDLE:
                if pinch_dist_norm < LEFT_DRAG_START_NORM_DIST:
                    self._state = _LeftState.GRAB_CANDIDATE
                    self._pinch_start_time = now
                    self._drag_state_text = "CANDIDATE"
                else:
                    self._drag_state_text = "READY"

            elif self._state == _LeftState.GRAB_CANDIDATE:
                if pinch_dist_norm > LEFT_DRAG_RELEASE_NORM_DIST:
                    # Pinch opened before confirmation -> cancel candidate
                    self._state = _LeftState.IDLE
                    self._pinch_start_time = None
                    self._drag_state_text = "READY"
                else:
                    hold_duration = (now - self._pinch_start_time) if self._pinch_start_time else 0.0
                    if hold_duration >= LEFT_DRAG_CONFIRM_S:
                        # Confirmed pinch hold -> grab item
                        self.just_started_drag = True
                        self._actuator.drag_start()
                        self._state = _LeftState.DRAGGING
                        self._drag_state_text = "ACTIVE"
                    else:
                        self._drag_state_text = "CANDIDATE"

            elif self._state == _LeftState.DRAGGING:
                self._drag_state_text = "ACTIVE"
                if pinch_dist_norm > LEFT_DRAG_RELEASE_NORM_DIST:
                    # Released pinch -> drop item
                    self._actuator.drag_end()
                    self._state = _LeftState.RELEASING
                    self._drag_state_text = "DROP"
                    self._display_text_until = now + 0.35

            elif self._state == _LeftState.RELEASING:
                if now > self._display_text_until:
                    self._state = _LeftState.IDLE
                    self._drag_state_text = "READY"
                elif pinch_dist_norm < LEFT_DRAG_START_NORM_DIST:
                    # Fast re-grab
                    self._state = _LeftState.GRAB_CANDIDATE
                    self._pinch_start_time = now
                    self._drag_state_text = "CANDIDATE"

    @property
    def left_drag_state(self) -> str:
        return self._state.name

    @property
    def drag_state_text(self) -> str:
        return self._drag_state_text

    @property
    def last_pinch_norm(self) -> float:
        return getattr(self, '_last_pinch_norm', 0.0)
