# core/gestures/lip_mute_gesture.py
"""
Phase 5: Lip-Touch System Audio Mute / Restore Gesture Recognizer.
Detects index finger of either hand touching lips for a configurable hold duration (~0.6s).
Alternates between mute and volume restore using AudioController with Dynamic Island feedback.
"""

import logging
import time
from enum import Enum, auto

from config import (
    LIP_MUTE_ENABLED,
    LIP_MUTE_HOLD_DURATION_S,
    LIP_MUTE_PROXIMITY_THRESHOLD,
    LIP_MUTE_RELEASE_THRESHOLD,
    LIP_MUTE_REARM_DURATION_S,
    LIP_MUTE_MAX_STABILITY_DELTA,
    LIP_MUTE_MAX_DEPTH_DELTA,
)
from core.tracker import Landmark, HandsResult
from core.face_tracker import FaceResult
from core.audio_controller import AudioController

logger = logging.getLogger(__name__)


class LipMuteState(Enum):
    IDLE = auto()             # Armed, waiting for lip touch
    CANDIDATE = auto()        # Index finger near lips, hold timer running
    CONFIRMED = auto()        # Successfully fired audio action once
    WAITING_RELEASE = auto()  # Waiting for finger to move away before rearming


def dist2d(a: Landmark | tuple[float, float, float], b: Landmark | tuple[float, float, float]) -> float:
    ax = a.x if isinstance(a, Landmark) else a[0]
    ay = a.y if isinstance(a, Landmark) else a[1]
    bx = b.x if isinstance(b, Landmark) else b[0]
    by = b.y if isinstance(b, Landmark) else b[1]
    return ((ax - bx)**2 + (ay - by)**2)**0.5


def is_index_pointing_upward(lm: list[Landmark]) -> bool:
    """Verifies that the candidate hand has its index finger extended."""
    wrist = lm[0]
    index_tip = lm[8]
    index_pip = lm[6]
    # Index tip is above PIP or further from wrist than PIP
    tip_dist = ((index_tip.x - wrist.x)**2 + (index_tip.y - wrist.y)**2)**0.5
    pip_dist = ((index_pip.x - wrist.x)**2 + (index_pip.y - wrist.y)**2)**0.5
    return tip_dist > pip_dist * 0.95 or index_tip.y < index_pip.y


def is_hand_pinching(lm: list[Landmark]) -> bool:
    """
    Returns True if thumb tip (4) and index tip (8) are pinched together (as in drag/click).
    In a true lip-touch gesture, the index finger is extended alone with thumb relaxed.
    """
    if len(lm) < 21:
        return False
    pw = dist2d(lm[5], lm[17])
    if pw > 1e-4:
        pinch_dist = dist2d(lm[4], lm[8])
        return (pinch_dist / pw) < 0.65
    return False


class LipMuteGestureRecognizer:
    """
    State machine for lip-touch mute/restore gesture.
    Supports either hand, enforces temporal stability, single-fire execution, and release re-arming.
    """

    def __init__(self, audio_controller: AudioController | None = None) -> None:
        self._audio = audio_controller or AudioController()
        self._state = LipMuteState.IDLE
        self._candidate_hand: str | None = None  # 'LEFT' or 'RIGHT'
        self._hold_start_time: float | None = None
        self._initial_touch_pos: tuple[float, float] | None = None
        self._release_start_time: float | None = None
        self._last_norm_dist: float = 999.0
        self._activation_callback = None
        self._last_action: str | None = None
        logger.info("LipMuteGestureRecognizer initialized (enabled=%s)", LIP_MUTE_ENABLED)

    @property
    def state(self) -> LipMuteState:
        return self._state

    @property
    def audio_controller(self) -> AudioController:
        return self._audio

    @property
    def is_holding(self) -> bool:
        """True when pose is candidate or confirmed, used to lock cursor movement."""
        return self._state in (LipMuteState.CANDIDATE, LipMuteState.CONFIRMED)

    @property
    def status_text(self) -> str:
        if self._state == LipMuteState.CANDIDATE:
            elapsed = time.perf_counter() - (self._hold_start_time or time.perf_counter())
            rem = max(0.0, LIP_MUTE_HOLD_DURATION_S - elapsed)
            return f"HOLDING ({rem:.1f}s)"
        elif self._state in (LipMuteState.CONFIRMED, LipMuteState.WAITING_RELEASE):
            return "RELEASE TO REARM"
        return "READY"

    @property
    def candidate_hand(self) -> str | None:
        return self._candidate_hand

    @property
    def last_norm_dist(self) -> float:
        return self._last_norm_dist

    def set_activation_callback(self, cb) -> None:
        """Sets callback called on audio toggle: cb(action: str, success: bool)."""
        self._activation_callback = cb

    def reset(self) -> None:
        """Resets recognizer to clean IDLE state."""
        self._state = LipMuteState.IDLE
        self._candidate_hand = None
        self._hold_start_time = None
        self._initial_touch_pos = None
        self._release_start_time = None

    def _find_best_hand_candidate(
        self,
        hands: HandsResult,
        lip_center: tuple[float, float, float],
        mouth_width: float,
        face_width: float = 0.0,
    ) -> tuple[str | None, list[Landmark] | None, float]:
        """
        Determines which hand's index tip is closest to the lip center and meets
        physical contact depth and pose criteria.
        Returns (hand_name, landmarks, normalized_distance).
        """
        best_name = None
        best_lms = None
        best_norm_dist = 999.0
        fw = max(face_width, mouth_width * 2.5, 1e-4)

        for name, lms in (("RIGHT", hands.right), ("LEFT", hands.left)):
            if lms is None:
                continue

            # Index finger must be extended
            if not is_index_pointing_upward(lms):
                continue

            # Reject pinching hands (e.g. performing drag or click)
            if is_hand_pinching(lms):
                continue

            index_tip = lms[8]

            # Relative depth check: index tip must be physically near lips in depth (Z space)
            if hasattr(index_tip, 'z') and len(lip_center) >= 3 and fw > 0:
                dz = abs(index_tip.z - lip_center[2]) / fw
                if dz > LIP_MUTE_MAX_DEPTH_DELTA:
                    continue

            d = dist2d(index_tip, lip_center)
            norm_d = d / max(mouth_width, 1e-4)
            if norm_d < best_norm_dist:
                best_norm_dist = norm_d
                best_name = name
                best_lms = lms

        return best_name, best_lms, best_norm_dist

    def process(self, hands: HandsResult | None, face: FaceResult | None, now: float | None = None) -> bool:
        """
        Processes current hand and face landmarks.
        Returns True if lip mute gesture is actively holding or triggered.
        """
        if not LIP_MUTE_ENABLED:
            return False

        if now is None:
            now = time.perf_counter()

        # If face or hands not present
        if face is None or not face.detected or face.lip_center is None or hands is None:
            if self._state == LipMuteState.CANDIDATE:
                logger.debug("Lip mute candidate aborted: face or hands lost")
                self.reset()
            elif self._state == LipMuteState.CONFIRMED:
                self._state = LipMuteState.WAITING_RELEASE
                self._release_start_time = now
            return False

        fw = getattr(face, 'face_width', 0.0) or (face.mouth_width * 2.5)
        hand_name, hand_lms, norm_dist = self._find_best_hand_candidate(
            hands, face.lip_center, face.mouth_width, fw
        )
        self._last_norm_dist = norm_dist

        # State machine transitions
        if self._state == LipMuteState.IDLE:
            if hand_lms is not None and norm_dist <= LIP_MUTE_PROXIMITY_THRESHOLD:
                self._state = LipMuteState.CANDIDATE
                self._candidate_hand = hand_name
                self._hold_start_time = now
                self._initial_touch_pos = (hand_lms[8].x, hand_lms[8].y)
                logger.debug("Lip mute CANDIDATE started with %s hand (dist=%.2f)", hand_name, norm_dist)

        elif self._state == LipMuteState.CANDIDATE:
            # Validate candidate maintains proximity, depth, and stability
            cand_lms = hands.right if self._candidate_hand == "RIGHT" else hands.left
            if (
                cand_lms is None
                or not is_index_pointing_upward(cand_lms)
                or is_hand_pinching(cand_lms)
            ):
                logger.debug("Lip mute CANDIDATE cancelled: hand lost or pose changed")
                self.reset()
                return False

            index_tip = cand_lms[8]
            norm_d = dist2d(index_tip, face.lip_center) / max(face.mouth_width, 1e-4)
            if hasattr(index_tip, 'z') and len(face.lip_center) >= 3 and fw > 0:
                dz = abs(index_tip.z - face.lip_center[2]) / fw
            else:
                dz = 0.0

            if norm_d > LIP_MUTE_PROXIMITY_THRESHOLD or dz > LIP_MUTE_MAX_DEPTH_DELTA:
                logger.debug(
                    "Lip mute CANDIDATE cancelled: finger moved away (dist=%.2f, dz=%.2f)",
                    norm_d, dz
                )
                self.reset()
                return False

            # Check stability to prevent sweeping hand triggers
            if self._initial_touch_pos is not None:
                drift = dist2d(cand_lms[8], self._initial_touch_pos) / max(face.mouth_width, 1e-4)
                if drift > LIP_MUTE_MAX_STABILITY_DELTA:
                    logger.debug("Lip mute CANDIDATE cancelled: excessive motion drift (%.2f)", drift)
                    self.reset()
                    return False

            # Check if hold duration satisfied
            elapsed = now - (self._hold_start_time or now)
            if elapsed >= LIP_MUTE_HOLD_DURATION_S:
                # Trigger audio toggle action exactly once
                success, action = self._audio.toggle_mute()
                self._last_action = action
                self._state = LipMuteState.CONFIRMED
                logger.info("Lip mute ACTIVATED: action=%s, success=%s", action, success)

                if self._activation_callback:
                    try:
                        self._activation_callback(action, success)
                    except Exception as e:
                        logger.error("Error in lip mute activation callback: %s", e)

                return True

        elif self._state in (LipMuteState.CONFIRMED, LipMuteState.WAITING_RELEASE):
            # Check for physical release
            cand_lms = hands.right if self._candidate_hand == "RIGHT" else hands.left
            if cand_lms is not None and hasattr(cand_lms[8], 'z') and len(face.lip_center) >= 3 and fw > 0:
                dz = abs(cand_lms[8].z - face.lip_center[2]) / fw
            else:
                dz = 0.0

            is_released = (
                cand_lms is None
                or norm_dist >= LIP_MUTE_RELEASE_THRESHOLD
                or dz > LIP_MUTE_MAX_DEPTH_DELTA
                or not is_index_pointing_upward(cand_lms)
            )

            if is_released:
                if self._release_start_time is None:
                    self._release_start_time = now
                    self._state = LipMuteState.WAITING_RELEASE
                elif (now - self._release_start_time) >= LIP_MUTE_REARM_DURATION_S:
                    # Successfully rearmed
                    logger.debug("Lip mute REARMED after confirmed release")
                    self.reset()
            else:
                # Still held near lips
                self._release_start_time = None
                self._state = LipMuteState.WAITING_RELEASE

        return self.is_holding
