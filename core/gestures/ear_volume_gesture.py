# core/gestures/ear_volume_gesture.py
"""
Phase 6: Ear-Touch Windows Output Volume Controller.
Continuously increases or decreases master volume when an index finger touches the corresponding ear:
- Anatomical Right Ear: VOLUME_UP (rate-limited, default ~30%/sec)
- Anatomical Left Ear: VOLUME_DOWN (rate-limited, default ~30%/sec)
Features:
- Explicit state machine: IDLE, CANDIDATE, ACTIVE_VOLUME_UP, ACTIVE_VOLUME_DOWN, RELEASING.
- Strict candidate vs active separation: candidate proximity NEVER locks the cursor.
- Depth-aware and normalized distance contact check.
- Hand-specific tracking: identifies which hand is touching which ear.
- Monotonic time-based rate-limited adjustment with clamping [0.0, 1.0].
- Dynamic Island and HUD feedback integration.
"""

import logging
import time
from enum import Enum, auto

from config import (
    EAR_VOLUME_ENABLED,
    EAR_VOLUME_ARM_DURATION_S,
    EAR_VOLUME_SPEED_PER_SEC,
    EAR_VOLUME_UPDATE_INTERVAL_S,
    EAR_VOLUME_PROXIMITY_THRESHOLD,
    EAR_VOLUME_RELEASE_THRESHOLD,
    EAR_VOLUME_MAX_DEPTH_DELTA,
    EAR_VOLUME_CONFIDENCE_MIN,
    EAR_VOLUME_LOST_TIMEOUT_S,
)
from core.tracker import Landmark, HandsResult
from core.face_tracker import FaceResult
from core.audio_controller import AudioController
from core.gestures.lip_mute_gesture import dist2d, is_index_pointing_upward

logger = logging.getLogger(__name__)


class EarVolumeDirection(Enum):
    NONE = auto()
    VOLUME_UP = auto()
    VOLUME_DOWN = auto()


class EarVolumeState(Enum):
    IDLE = auto()
    CANDIDATE = auto()
    ACTIVE_VOLUME_UP = auto()
    ACTIVE_VOLUME_DOWN = auto()
    RELEASING = auto()


class EarVolumeGestureRecognizer:
    """
    Recognizes ear-touch gestures and performs smooth, rate-limited continuous volume adjustments.
    Enforces a strict state machine so candidate detection never interferes with cursor movement.
    """

    def __init__(self, audio_controller: AudioController | None = None) -> None:
        self._audio = audio_controller or AudioController()
        self._state = EarVolumeState.IDLE
        self._direction = EarVolumeDirection.NONE
        self._candidate_direction = EarVolumeDirection.NONE
        self._candidate_start_time: float | None = None
        self._touching_hand: str | None = None  # 'RIGHT' or 'LEFT'
        self._last_adjustment_time: float | None = None
        self._last_update_call_time: float | None = None
        self._last_valid_time: float | None = None
        self._callback = None
        self._current_volume: float = 0.50
        logger.info("EarVolumeGestureRecognizer initialized (enabled=%s)", EAR_VOLUME_ENABLED)

    @property
    def state(self) -> EarVolumeState:
        return self._state

    @property
    def direction(self) -> EarVolumeDirection:
        return self._direction

    @property
    def is_active(self) -> bool:
        """
        True ONLY when confirmed active and continuously adjusting volume.
        CRITICAL: Never True during CANDIDATE state so candidate proximity never locks cursor.
        """
        return self._state in (EarVolumeState.ACTIVE_VOLUME_UP, EarVolumeState.ACTIVE_VOLUME_DOWN)

    @property
    def is_candidate(self) -> bool:
        """True when in candidate arming dwell."""
        return self._state == EarVolumeState.CANDIDATE

    @property
    def touching_hand(self) -> str | None:
        """The hand ('RIGHT' or 'LEFT') currently touching an ear, or None."""
        return self._touching_hand

    @property
    def current_volume(self) -> float:
        return self._current_volume

    @property
    def status_text(self) -> str:
        vol_pct = int(self._current_volume * 100.0)
        if self._state == EarVolumeState.ACTIVE_VOLUME_UP:
            return f"VOL UP {vol_pct}%"
        elif self._state == EarVolumeState.ACTIVE_VOLUME_DOWN:
            return f"VOL DOWN {vol_pct}%"
        elif self._state == EarVolumeState.CANDIDATE:
            arrow = "UP" if self._candidate_direction == EarVolumeDirection.VOLUME_UP else "DOWN"
            return f"ARMING {arrow}..."
        elif self._state == EarVolumeState.RELEASING:
            return "RELEASING"
        return "READY"

    def set_callback(self, cb) -> None:
        """Sets callback: cb(direction_name: str, volume_scalar: float, is_active: bool)."""
        self._callback = cb

    def _evaluate_ear_contact(
        self, hands: HandsResult, face: FaceResult
    ) -> tuple[EarVolumeDirection, float, str | None]:
        """
        Calculates proximity of index fingertips to anatomical right and left ears.
        Uses depth delta verification, confidence checks, and hysteresis.
        Returns (detected_direction, min_norm_dist, touching_hand_name).
        """
        fw = max(face.face_width, 1e-4)
        r_ear = face.right_ear_pos
        l_ear = face.left_ear_pos

        if r_ear is None or l_ear is None:
            return EarVolumeDirection.NONE, 999.0, None

        if getattr(face, 'confidence', 1.0) < EAR_VOLUME_CONFIDENCE_MIN:
            return EarVolumeDirection.NONE, 999.0, None

        # Hysteresis threshold: release threshold when actively held, proximity threshold otherwise
        threshold = (
            EAR_VOLUME_RELEASE_THRESHOLD
            if self.is_active
            else EAR_VOLUME_PROXIMITY_THRESHOLD
        )

        r_contact = False
        l_contact = False
        r_hand = None
        l_hand = None
        min_dist = 999.0

        for hand_lms, hand_name, score in (
            (hands.right, "RIGHT", hands.right_score),
            (hands.left, "LEFT", hands.left_score),
        ):
            if hand_lms is None or score < EAR_VOLUME_CONFIDENCE_MIN:
                continue

            # Index finger must be extended / pointing
            if not is_index_pointing_upward(hand_lms):
                continue

            index_tip = hand_lms[8]

            # Conflict prevention: reject if index tip is close to lips
            if face.lip_center is not None:
                d_lip = dist2d(index_tip, face.lip_center) / fw
                if d_lip < 0.30:
                    continue

            d_r = dist2d(index_tip, r_ear) / fw
            d_l = dist2d(index_tip, l_ear) / fw

            # Relative depth check: if hand is significantly in front of the ear in Z space,
            # reject as foreground pass-through
            if hasattr(index_tip, 'z') and len(r_ear) >= 3 and len(l_ear) >= 3:
                dz_r = abs(index_tip.z - r_ear[2]) / fw
                dz_l = abs(index_tip.z - l_ear[2]) / fw
            else:
                dz_r = 0.0
                dz_l = 0.0

            if d_r <= threshold and dz_r <= EAR_VOLUME_MAX_DEPTH_DELTA:
                r_contact = True
                r_hand = hand_name
                min_dist = min(min_dist, d_r)

            if d_l <= threshold and dz_l <= EAR_VOLUME_MAX_DEPTH_DELTA:
                l_contact = True
                l_hand = hand_name
                min_dist = min(min_dist, d_l)

        # Disambiguation: ambiguous if both ears are touched simultaneously
        if r_contact and l_contact:
            return EarVolumeDirection.NONE, min_dist, None
        elif r_contact:
            return EarVolumeDirection.VOLUME_UP, min_dist, r_hand
        elif l_contact:
            return EarVolumeDirection.VOLUME_DOWN, min_dist, l_hand

        return EarVolumeDirection.NONE, min_dist, None

    def process(
        self, hands: HandsResult | None, face: FaceResult | None, now: float | None = None
    ) -> bool:
        """
        Evaluates current landmarks, drives state machine, and applies continuous volume adjustments.
        Returns True if actively adjusting volume (is_active).
        """
        if not EAR_VOLUME_ENABLED:
            return False

        if now is None:
            now = time.perf_counter()

        # Check for missing tracking landmarks
        if face is None or not face.detected or hands is None:
            if self._last_valid_time and (now - self._last_valid_time) < EAR_VOLUME_LOST_TIMEOUT_S:
                # Brief frame dropout tolerance while actively adjusting
                return self.is_active

            # Tracking lost -> reset state immediately
            if self.is_active or self._state != EarVolumeState.IDLE:
                self._stop_adjustment(now)
            return False

        detected_dir, dist, touching_hand = self._evaluate_ear_contact(hands, face)

        if detected_dir != EarVolumeDirection.NONE:
            self._last_valid_time = now
            self._touching_hand = touching_hand

            if self.is_active:
                # Currently active in continuous volume adjustment
                if detected_dir == self._direction:
                    self._apply_continuous_adjustment(now)
                else:
                    # Direction flipped while active -> stop adjustment
                    self._stop_adjustment(now)

            elif self._state == EarVolumeState.CANDIDATE:
                # Arming dwell check
                if detected_dir == self._candidate_direction:
                    elapsed = now - (self._candidate_start_time or now)
                    if elapsed >= EAR_VOLUME_ARM_DURATION_S:
                        # Dwell satisfied! Enter active volume state
                        if detected_dir == EarVolumeDirection.VOLUME_UP:
                            self._state = EarVolumeState.ACTIVE_VOLUME_UP
                            self._direction = EarVolumeDirection.VOLUME_UP
                        else:
                            self._state = EarVolumeState.ACTIVE_VOLUME_DOWN
                            self._direction = EarVolumeDirection.VOLUME_DOWN

                        self._candidate_direction = EarVolumeDirection.NONE
                        self._candidate_start_time = None
                        self._last_adjustment_time = now
                        self._last_update_call_time = now
                        self._current_volume = self._audio.get_volume_scalar()
                        logger.info("Ear volume ACTIVE: %s (%s hand), starting_vol=%.2f%%",
                                    self._state.name, self._touching_hand, self._current_volume * 100.0)
                        self._notify_callback(now, is_active=True)
                else:
                    # Candidate direction changed: restart candidate dwell
                    self._candidate_direction = detected_dir
                    self._candidate_start_time = now

            else:
                # IDLE -> enter CANDIDATE dwell
                self._state = EarVolumeState.CANDIDATE
                self._candidate_direction = detected_dir
                self._candidate_start_time = now

        else:
            # No ear contact detected
            if self.is_active:
                self._stop_adjustment(now)
            elif self._state == EarVolumeState.CANDIDATE:
                # Contact broken before arming: return to IDLE cleanly without ever locking cursor
                self._state = EarVolumeState.IDLE
                self._candidate_direction = EarVolumeDirection.NONE
                self._candidate_start_time = None
                self._touching_hand = None

        return self.is_active

    def _apply_continuous_adjustment(self, now: float) -> None:
        """Applies monotonic time-based volume delta clamped between 0.0 and 1.0."""
        if self._last_update_call_time is None:
            self._last_update_call_time = now
            return

        dt = now - self._last_update_call_time
        if dt < EAR_VOLUME_UPDATE_INTERVAL_S:
            return

        self._last_update_call_time = now
        self._last_adjustment_time = now

        # Delta based on actual elapsed time: speed * dt
        rate = EAR_VOLUME_SPEED_PER_SEC
        delta = (rate * dt) if self._direction == EarVolumeDirection.VOLUME_UP else -(rate * dt)

        # Apply to audio controller
        new_vol = self._audio.adjust_volume(delta)
        self._current_volume = new_vol

        # Notify UI overlay
        self._notify_callback(now, is_active=True)

    def _stop_adjustment(self, now: float) -> None:
        """Stops adjustment promptly and notifies UI overlay."""
        was_active = self.is_active
        self._state = EarVolumeState.IDLE
        self._direction = EarVolumeDirection.NONE
        self._candidate_direction = EarVolumeDirection.NONE
        self._candidate_start_time = None
        self._touching_hand = None
        self._last_adjustment_time = None
        self._last_valid_time = None

        if was_active:
            logger.info("Ear volume released. Final volume: %.2f%%", self._current_volume * 100.0)
            self._notify_callback(now, is_active=False)

    def _notify_callback(self, now: float, is_active: bool) -> None:
        if self._callback:
            dir_name = self._direction.name if is_active else "NONE"
            try:
                self._callback(dir_name, self._current_volume, is_active)
            except Exception as e:
                logger.error("Error in ear volume callback: %s", e)
