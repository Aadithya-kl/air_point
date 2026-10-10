import time
import logging
from enum import Enum, auto
from core.tracker import HandsResult
from core.actuator import MouseActuator
from core.gestures.utils import is_open_palm, is_palm_facing_camera_right_hand, is_palm_facing_camera_left_hand
from config import (
    SCREENSHOT_ENABLED,
    SCREENSHOT_DIR,
    SCREENSHOT_HOLD_DURATION_S,
    SCREENSHOT_GRACE_PERIOD_S,
    SCREENSHOT_HAND_CONFIDENCE_MIN,
    SCREENSHOT_REARM_DURATION_S
)

logger = logging.getLogger(__name__)

class ScreenshotState(Enum):
    READY = auto()
    HOLDING = auto()
    CAPTURED = auto()
    ERROR = auto()

class ScreenshotController:
    def __init__(self, actuator: MouseActuator):
        self._actuator = actuator
        self._state = ScreenshotState.READY
        self._hold_start_time = 0.0
        self._last_valid_time = 0.0
        self._last_invalid_time = 0.0
        self.hud_text = ""
        self.hud_left = ""
        self.hud_right = ""
        
        # Override cursor position while holding or captured
        self.is_locking_cursor = False
        self._display_until = 0.0

    def process(self, hands: HandsResult, now: float) -> bool:
        """
        Returns True if the screenshot gesture is currently active (holding or captured),
        which should block other gestures.
        """
        if not SCREENSHOT_ENABLED:
            return False

        # Evaluate both hands simultaneously
        left_valid = False
        left_fingers_ext = 0
        left_palm_facing = False
        left_msg = "MISSING"

        if hands.left is not None:
            score = hands.left_score
            if score < SCREENSHOT_HAND_CONFIDENCE_MIN:
                left_msg = f"LOW CONF ({score:.2f})"
            else:
                ext_count = self._count_extended(hands.left)
                left_fingers_ext = ext_count
                left_palm_facing = is_palm_facing_camera_left_hand(hands.left)
                if ext_count < 5:
                    left_msg = "FINGERS NOT EXTENDED"
                elif not left_palm_facing:
                    left_msg = "PALM FACING AWAY"
                else:
                    left_valid = True
                    left_msg = f"VALID ({score:.2f})"

        right_valid = False
        right_fingers_ext = 0
        right_palm_facing = False
        right_msg = "MISSING"

        if hands.right is not None:
            score = hands.right_score
            if score < SCREENSHOT_HAND_CONFIDENCE_MIN:
                right_msg = f"LOW CONF ({score:.2f})"
            else:
                ext_count = self._count_extended(hands.right)
                right_fingers_ext = ext_count
                right_palm_facing = is_palm_facing_camera_right_hand(hands.right)
                if ext_count < 5:
                    right_msg = "FINGERS NOT EXTENDED"
                elif not right_palm_facing:
                    right_msg = "PALM FACING AWAY"
                else:
                    right_valid = True
                    right_msg = f"VALID ({score:.2f})"

        pose_valid = left_valid and right_valid
        self.left_fingers_ext = left_fingers_ext
        self.right_fingers_ext = right_fingers_ext
        
        self.hud_left = f"LEFT HAND: {left_msg} | FINGERS: {left_fingers_ext}/5 | PALM: {'FACING' if left_palm_facing else 'AWAY'}"
        self.hud_right = f"RIGHT HAND: {right_msg} | FINGERS: {right_fingers_ext}/5 | PALM: {'FACING' if right_palm_facing else 'AWAY'}"

        # State machine
        if self._state == ScreenshotState.READY:
            if pose_valid:
                self._state = ScreenshotState.HOLDING
                self._hold_start_time = now
                self._last_valid_time = now
                self.is_locking_cursor = True
                self.hud_text = f"SCREENSHOT: HOLD - {SCREENSHOT_HOLD_DURATION_S:.1f}s"
            else:
                self.is_locking_cursor = False
                self.hud_text = "SCREENSHOT: READY"

        elif self._state == ScreenshotState.HOLDING:
            if pose_valid:
                self._last_valid_time = now
                elapsed = now - self._hold_start_time
                remaining = max(0.0, SCREENSHOT_HOLD_DURATION_S - elapsed)
                self.hud_text = f"SCREENSHOT: HOLD - {remaining:.1f}s"
                
                if elapsed >= SCREENSHOT_HOLD_DURATION_S:
                    # Capture!
                    pre_cb = getattr(self, 'pre_capture_cb', None)
                    post_cb = getattr(self, 'post_capture_cb', None)
                    result = self._actuator.take_screenshot(SCREENSHOT_DIR, pre_cb, post_cb)
                    if result:
                        self._state = ScreenshotState.CAPTURED
                        self.hud_text = f"SCREENSHOT: CAPTURED ({result})"
                    else:
                        self._state = ScreenshotState.ERROR
                        self.hud_text = "SCREENSHOT: ERROR"
                    self._display_until = now + 1.5
            else:
                if (now - self._last_valid_time) > SCREENSHOT_GRACE_PERIOD_S:
                    # Cancelled
                    self._state = ScreenshotState.READY
                    self.is_locking_cursor = False
                    self.hud_text = "SCREENSHOT: CANCELLED"
                    self._display_until = now + 1.0

        elif self._state in (ScreenshotState.CAPTURED, ScreenshotState.ERROR):
            any_hand_in_pose = left_valid or right_valid
            if any_hand_in_pose:
                self._last_invalid_time = now
                if now > self._display_until:
                    self.hud_text = "SCREENSHOT: RELEASE BOTH HANDS TO REARM"
            else:
                if (now - self._last_invalid_time) > SCREENSHOT_REARM_DURATION_S:
                    self._state = ScreenshotState.READY
                    self.is_locking_cursor = False
                    self.hud_text = "SCREENSHOT: READY"

        # If we have a display timer, override hud_text
        if self._state == ScreenshotState.READY and now <= getattr(self, '_display_until', 0):
            # keep previous cancellation text
            pass

        return self.is_locking_cursor

    def _count_extended(self, lm) -> int:
        from core.gestures.utils import dist3d
        count = 0
        if dist3d(lm[8], lm[0]) > dist3d(lm[6], lm[0]): count += 1
        if dist3d(lm[12], lm[0]) > dist3d(lm[10], lm[0]): count += 1
        if dist3d(lm[16], lm[0]) > dist3d(lm[14], lm[0]): count += 1
        if dist3d(lm[20], lm[0]) > dist3d(lm[18], lm[0]): count += 1
        
        # Thumb: tip should be further from pinky base (17) than the IP joint (3)
        if dist3d(lm[4], lm[17]) > dist3d(lm[3], lm[17]): count += 1
        return count
