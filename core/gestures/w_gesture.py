# core/gestures/w_gesture.py
"""
W Gesture Recognizer - Two-hand custom 'W' pose to open WhatsApp Desktop.
Recognizes both hands forming a capital W shape:
- Both index fingers point inward toward each other with tips touching/close.
- Outer pinkies extended vertically.
- Middle and ring fingers curled down into palm.
- Palms generally facing camera.
"""

import time
import logging
from enum import Enum, auto

from core.tracker import HandsResult, Landmark
from core.gestures.utils import (
    dist3d,
    is_palm_facing_camera_left_hand,
    is_palm_facing_camera_right_hand,
)
from core.app_launcher import AppLauncher
from config import (
    W_GESTURE_ENABLED,
    W_HOLD_DURATION_S,
    W_GRACE_PERIOD_S,
    W_REARM_DURATION_S,
    W_INDEX_TOUCH_THRESHOLD,
    W_HAND_CONFIDENCE_MIN,
)

logger = logging.getLogger(__name__)


class WState(Enum):
    IDLE = auto()
    CANDIDATE = auto()
    CONFIRMED = auto()
    COOLDOWN = auto()


def is_w_finger_configuration(lm: list[Landmark]) -> bool:
    """
    Checks if a single hand matches the W-pose prong criteria from the reference photo:
    - Index (8) and Pinky (20) are extended upwards.
    - Middle (12) and Ring (16) are curled down into palm.
    - Extended tips are higher than wrist and higher than curled tips.
    """
    wrist = lm[0]

    # Index: extended (tip further from wrist than PIP, and tip above wrist)
    index_ext = dist3d(lm[8], wrist) > dist3d(lm[6], wrist) and lm[8].y < wrist.y

    # Pinky: extended (tip further from wrist than PIP, and tip above wrist)
    pinky_ext = dist3d(lm[20], wrist) > dist3d(lm[18], wrist) and lm[20].y < wrist.y

    # Middle: curled into palm (tip closer to wrist than PIP or below PIP, and lower than index tip)
    middle_curled = (
        dist3d(lm[12], wrist) <= dist3d(lm[10], wrist) * 1.05
        or lm[12].y > lm[10].y
    ) and lm[12].y > lm[8].y

    # Ring: curled into palm (tip closer to wrist than PIP or below PIP, and lower than pinky tip)
    ring_curled = (
        dist3d(lm[16], wrist) <= dist3d(lm[14], wrist) * 1.05
        or lm[16].y > lm[14].y
    ) and lm[16].y > lm[20].y

    # Thumb: medial/curled towards palm (not extended like open hand)
    thumb_curled = dist3d(lm[4], lm[17]) <= dist3d(lm[3], lm[17]) * 1.25

    return index_ext and pinky_ext and middle_curled and ring_curled and thumb_curled


def check_w_pose(hands: HandsResult) -> tuple[bool, str]:
    """
    Evaluates both hands to check if the combined pose forms the reference 'W' gesture.
    Returns (is_valid, diagnostic_reason).
    """
    if hands.left is None or hands.right is None:
        return False, "BOTH_HANDS_REQUIRED"

    if (
        hands.left_score < W_HAND_CONFIDENCE_MIN
        or hands.right_score < W_HAND_CONFIDENCE_MIN
    ):
        return False, "LOW_CONFIDENCE"

    # 1. Both palms must face generally toward the camera
    left_facing = is_palm_facing_camera_left_hand(hands.left)
    right_facing = is_palm_facing_camera_right_hand(hands.right)
    if not (left_facing and right_facing):
        return False, "PALMS_NOT_FACING"

    # 2. Both hands must exhibit the index+pinky extended, middle+ring curled configuration
    if not is_w_finger_configuration(hands.left):
        return False, "LEFT_HAND_FINGERS_MISMATCH"

    if not is_w_finger_configuration(hands.right):
        return False, "RIGHT_HAND_FINGERS_MISMATCH"

    # 3. Palm width for scale normalization
    pw_left = dist3d(hands.left[5], hands.left[17])
    pw_right = dist3d(hands.right[5], hands.right[17])
    avg_pw = max((pw_left + pw_right) / 2.0, 1e-4)

    # 4. Proximity of index fingertips (2D camera plane distance avoids depth sensor noise)
    left_index_tip = hands.left[8]
    right_index_tip = hands.right[8]

    dx = left_index_tip.x - right_index_tip.x
    dy = left_index_tip.y - right_index_tip.y
    index_gap = (dx**2 + dy**2)**0.5 / avg_pw

    if index_gap > W_INDEX_TOUCH_THRESHOLD:
        return False, f"INDEX_TIPS_NOT_TOUCHING ({index_gap:.2f})"

    # Vertical alignment of index fingertips
    vert_diff = abs(dy) / avg_pw
    if vert_diff > 0.80:
        return False, "INDEX_TIPS_VERTICAL_MISMATCH"

    # 5. Spatial arrangement: Left pinky on outer left, Right pinky on outer right
    # Screen-left (hands.left): pinky (20) x < index (8) x
    # Screen-right (hands.right): pinky (20) x > index (8) x
    left_pinky_tip = hands.left[20]
    right_pinky_tip = hands.right[20]

    if left_pinky_tip.x >= left_index_tip.x:
        return False, "LEFT_PINKY_NOT_OUTER"

    if right_pinky_tip.x <= right_index_tip.x:
        return False, "RIGHT_PINKY_NOT_OUTER"

    if left_pinky_tip.x >= right_pinky_tip.x:
        return False, "HANDS_CROSSED"

    return True, "W_POSE_VALID"


class WGestureRecognizer:
    """
    Recognizes the custom two-hand W gesture and manages the 0.7s hold timer,
    one-time activation, and re-arm sequence to launch WhatsApp.
    """

    def __init__(
        self,
        launcher: AppLauncher | None = None,
        on_activated_cb = None,
    ) -> None:
        self._launcher = launcher or AppLauncher()
        self._on_activated_cb = on_activated_cb

        self._state = WState.IDLE
        self._start_time: float | None = None
        self._last_valid_time: float | None = None
        self._release_time: float | None = None
        self._display_status: str = "READY"

        self.elapsed: float = 0.0
        self.progress: float = 0.0
        self.has_activated: bool = False

    def set_activation_callback(self, cb) -> None:
        self._on_activated_cb = cb

    def process(self, hands: HandsResult, now: float) -> tuple[bool, str]:
        """
        Processes hands each frame using a monotonic timestamp.
        Returns:
            (activated_this_frame, status_text)
        """
        if not W_GESTURE_ENABLED:
            return False, "DISABLED"

        pose_valid, reason = check_w_pose(hands)
        activated_this_frame = False

        if pose_valid:
            if self._state == WState.IDLE:
                self._state = WState.CANDIDATE
                self._start_time = now
                self._last_valid_time = now
                self.elapsed = 0.0
                self.progress = 0.0
                self._display_status = "DETECTED"

            elif self._state == WState.CANDIDATE:
                self._last_valid_time = now
                self.elapsed = max(0.0, now - (self._start_time or now))
                self.progress = min(1.0, max(0.0, self.elapsed / W_HOLD_DURATION_S))
                self._display_status = f"HOLDING ({self.elapsed:.1f}s / {W_HOLD_DURATION_S:.1f}s)"

                if self.elapsed >= W_HOLD_DURATION_S:
                    # Confirmed! Fire exactly once
                    self._state = WState.CONFIRMED
                    self.has_activated = True
                    activated_this_frame = True
                    self.progress = 1.0
                    self._display_status = "ACTIVATED"

                    success = self._launcher.open_whatsapp()
                    logger.info("W gesture confirmed! WhatsApp launch success: %s", success)

                    if self._on_activated_cb:
                        try:
                            self._on_activated_cb(success)
                        except Exception as e:
                            logger.warning("Error in W activation callback: %s", e)

            elif self._state == WState.CONFIRMED:
                self._last_valid_time = now
                self.progress = 1.0
                self._display_status = "HOLD (CONFIRMED)"
                # Do NOT fire again while held

            elif self._state == WState.COOLDOWN:
                # Still within cooldown, wait until released
                self._display_status = "RELEASE TO REARM"

        else:
            # Pose invalid or broken
            if self._state == WState.CANDIDATE:
                # Check grace period for brief frame loss
                if (now - (self._last_valid_time or 0.0)) > W_GRACE_PERIOD_S:
                    self._state = WState.IDLE
                    self._start_time = None
                    self.elapsed = 0.0
                    self.progress = 0.0
                    self._display_status = "READY"

            elif self._state == WState.CONFIRMED:
                # Hand just left the confirmed pose -> enter cooldown/wait for release
                self._state = WState.COOLDOWN
                self._release_time = now
                self._display_status = "RELEASED"

            elif self._state == WState.COOLDOWN:
                # Must stay released for W_REARM_DURATION_S to rearm to IDLE
                if (now - (self._release_time or now)) >= W_REARM_DURATION_S:
                    self._state = WState.IDLE
                    self._release_time = None
                    self._start_time = None
                    self.elapsed = 0.0
                    self.progress = 0.0
                    self._display_status = "READY"
                else:
                    self._display_status = "REARMING"

            elif self._state == WState.IDLE:
                self.elapsed = 0.0
                self.progress = 0.0
                self._display_status = "READY"

        return activated_this_frame, self._display_status

    @property
    def state(self) -> WState:
        return self._state

    @property
    def status_text(self) -> str:
        return self._display_status
