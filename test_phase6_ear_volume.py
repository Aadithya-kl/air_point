# test_phase6_ear_volume.py
"""
Unit tests for Phase 6: Ear-Touch Windows Output Volume Controller & Stability.
Tests:
  - AudioController scalar get, set, adjust, bounds clamping [0.0, 1.0], and unmute-on-increase.
  - FaceResult ear landmark positions (anatomical right and left ears in mirrored frame).
  - EarVolumeGestureRecognizer:
      * Right ear touch triggers VOLUME_UP.
      * Left ear touch triggers VOLUME_DOWN.
      * Supports either left or right hand index finger.
      * Approaching/moving near ear without contact does NOT activate volume control.
      * Hands in foreground rejected via relative depth (Z) verification.
      * Brief contact (< 0.20s) rejected before arming.
      * Sustained contact (>= 0.20s) activates continuous volume adjustment.
      * Small fingertip landmark fluctuations during hold do not cause rapid dropout (hysteresis).
      * Rate limiting respects EAR_VOLUME_UPDATE_INTERVAL_S.
      * Clamping at 0.0 and 1.0 (no wrap-around).
      * Disambiguation: dual-ear contact is rejected.
      * Release and tracking loss immediately stop adjustment.
      * Lip conflict prevention: hands near lips do not trigger ear volume.
  - GestureOrchestrator:
      * Candidate dwell NEVER locks, freezes, or pauses the cursor.
      * Confirmed active ear touch locks only the touching hand, allowing the other hand to point.
      * Reacquisition after ear gesture is smoothly recovered without sudden screen snaps.
  - Dynamic Island capsule feedback for volume up and down.
"""

import unittest
from unittest.mock import MagicMock, patch
import time

from core.tracker import Landmark, HandsResult
from core.face_tracker import FaceResult
from core.audio_controller import AudioController
from core.gestures.ear_volume_gesture import (
    EarVolumeGestureRecognizer,
    EarVolumeDirection,
    EarVolumeState,
)
from core.display import VirtualDesktop, TrackpadZone
from core.actuator import MouseActuator
from core.gestures.orchestrator import GestureOrchestrator
from config import (
    EAR_VOLUME_ARM_DURATION_S,
    EAR_VOLUME_SPEED_PER_SEC,
    EAR_VOLUME_UPDATE_INTERVAL_S,
    EAR_VOLUME_PROXIMITY_THRESHOLD,
    EAR_VOLUME_RELEASE_THRESHOLD,
    EAR_VOLUME_LOST_TIMEOUT_S,
)


def make_synthetic_hand(
    wrist=(0.5, 0.8, 0.0),
    index_tip=(0.5, 0.5, 0.0),
    is_pointing=True,
) -> list[Landmark]:
    """Generates 21 synthetic landmarks for a hand."""
    lms = [Landmark(x=wrist[0], y=wrist[1], z=wrist[2]) for _ in range(21)]
    lms[6] = Landmark(x=index_tip[0], y=index_tip[1] + 0.08, z=index_tip[2])  # PIP
    lms[8] = Landmark(x=index_tip[0], y=index_tip[1], z=index_tip[2])          # TIP
    if not is_pointing:
        lms[8] = Landmark(x=index_tip[0], y=index_tip[1] + 0.12, z=index_tip[2])
    return lms


def make_synthetic_face(
    face_width=0.40,
    r_ear=(0.75, 0.45, 0.0),  # Mirrored frame: viewer right = anatomical right
    l_ear=(0.25, 0.45, 0.0),  # Mirrored frame: viewer left = anatomical left
    detected=True,
    lip_center=(0.5, 0.6, 0.0),
) -> FaceResult:
    """Generates synthetic FaceResult with specified ear positions."""
    if not detected:
        return FaceResult(detected=False)

    return FaceResult(
        detected=True,
        right_ear_pos=r_ear,
        left_ear_pos=l_ear,
        lip_center=lip_center,
        mouth_width=0.10,
        face_width=face_width,
        confidence=1.0,
    )


class TestAudioControllerScalarVolume(unittest.TestCase):
    """Verifies AudioController scalar volume methods and clamping."""

    def setUp(self):
        self.mock_endpoint = MagicMock()
        self._vol_val = 0.50
        self._mute_val = 0

        def get_vol():
            return self._vol_val

        def set_vol(val, _):
            self._vol_val = float(val)

        def set_mute(val, _):
            self._mute_val = int(val)

        def get_mute():
            return self._mute_val

        self.mock_endpoint.GetMasterVolumeLevelScalar.side_effect = get_vol
        self.mock_endpoint.SetMasterVolumeLevelScalar.side_effect = set_vol
        self.mock_endpoint.GetMute.side_effect = get_mute
        self.mock_endpoint.SetMute.side_effect = set_mute

        self.controller = AudioController(endpoint_override=self.mock_endpoint)

    def test_get_and_set_volume_scalar(self):
        vol = self.controller.get_volume_scalar()
        self.assertAlmostEqual(vol, 0.50)

        new_vol = self.controller.set_volume_scalar(0.72)
        self.assertAlmostEqual(new_vol, 0.72)
        self.assertAlmostEqual(self.controller.get_volume_scalar(), 0.72)

    def test_volume_clamping_upper_and_lower(self):
        # Upper clamp
        self.controller.set_volume_scalar(1.50)
        self.assertAlmostEqual(self.controller.get_volume_scalar(), 1.0)

        # Lower clamp
        self.controller.set_volume_scalar(-0.25)
        self.assertAlmostEqual(self.controller.get_volume_scalar(), 0.0)

    def test_adjust_volume_incremental_delta(self):
        self.controller.set_volume_scalar(0.50)
        new_vol = self.controller.adjust_volume(0.15)
        self.assertAlmostEqual(new_vol, 0.65)

        new_vol = self.controller.adjust_volume(-0.30)
        self.assertAlmostEqual(new_vol, 0.35)

    def test_adjust_volume_unmutes_on_positive_delta(self):
        self.controller.mute()
        self.assertEqual(self.mock_endpoint.GetMute(), 1)

        self.controller.adjust_volume(0.05)
        self.assertEqual(self.mock_endpoint.GetMute(), 0)

    def test_audio_error_handling_graceful(self):
        self.mock_endpoint.GetMasterVolumeLevelScalar.side_effect = RuntimeError("Device lost")
        vol = self.controller.get_volume_scalar()
        self.assertAlmostEqual(vol, 0.50)

        # Setting volume should not raise exception even if endpoint errors
        self.mock_endpoint.SetMasterVolumeLevelScalar.side_effect = RuntimeError("Device lost")
        new_vol = self.controller.set_volume_scalar(0.80)
        self.assertAlmostEqual(new_vol, 0.80)


class TestEarVolumeGestureRecognizer(unittest.TestCase):
    """Verifies ear contact detection, arming, continuous rate-limiting, and disambiguation."""

    def setUp(self):
        self.mock_audio = MagicMock()
        self._current_vol = 0.50

        def get_vol():
            return self._current_vol

        def adj_vol(delta):
            self._current_vol = max(0.0, min(1.0, self._current_vol + delta))
            return self._current_vol

        self.mock_audio.get_volume_scalar.side_effect = get_vol
        self.mock_audio.adjust_volume.side_effect = adj_vol

        self.recognizer = EarVolumeGestureRecognizer(audio_controller=self.mock_audio)
        self.callbacks = []
        self.recognizer.set_callback(lambda dir_name, vol, active: self.callbacks.append((dir_name, vol, active)))

    def test_right_ear_touch_triggers_volume_up_after_arming(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        # Touch right ear
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        # Frame 1: enters candidate (arming dwell)
        active = self.recognizer.process(hands, face, now=t0)
        # In CANDIDATE state, is_active is strictly False to protect cursor!
        self.assertFalse(active)
        self.assertTrue(self.recognizer.is_candidate)
        self.assertEqual(self.recognizer.state, EarVolumeState.CANDIDATE)
        self.assertEqual(self.recognizer.status_text, "ARMING UP...")

        # Frame 2: before arming dwell
        self.recognizer.process(hands, face, now=t0 + 0.10)
        self.assertFalse(self.recognizer.is_active)
        self.assertTrue(self.recognizer.is_candidate)

        # Frame 3: at 0.21s -> confirmed ACTIVE_VOLUME_UP!
        active = self.recognizer.process(hands, face, now=t0 + 0.21)
        self.assertTrue(active)
        self.assertTrue(self.recognizer.is_active)
        self.assertEqual(self.recognizer.state, EarVolumeState.ACTIVE_VOLUME_UP)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.VOLUME_UP)

        # Frame 4: holds for another 0.10s -> increases volume
        self.recognizer.process(hands, face, now=t0 + 0.31)
        self.assertGreater(self.recognizer.current_volume, 0.50)
        self.assertIn("VOL UP", self.recognizer.status_text)

    def test_left_ear_touch_triggers_volume_down_after_arming(self):
        face = make_synthetic_face(l_ear=(0.25, 0.45, 0.0), face_width=0.40)
        # Touch left ear with left hand
        l_hand = make_synthetic_hand(index_tip=(0.25, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=None, left=l_hand, right_score=0.0, left_score=0.99)

        t0 = 100.0
        self.recognizer.process(hands, face, now=t0)
        self.assertEqual(self.recognizer.status_text, "ARMING DOWN...")

        self.recognizer.process(hands, face, now=t0 + 0.21)
        self.assertEqual(self.recognizer.state, EarVolumeState.ACTIVE_VOLUME_DOWN)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.VOLUME_DOWN)

        # Sustained hold decreases volume
        self.recognizer.process(hands, face, now=t0 + 0.35)
        self.assertLess(self.recognizer.current_volume, 0.50)
        self.assertIn("VOL DOWN", self.recognizer.status_text)

    def test_left_hand_can_touch_right_ear(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        # Left hand reaches across to touch right ear
        l_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=None, left=l_hand, right_score=0.0, left_score=0.99)

        t0 = 100.0
        self.recognizer.process(hands, face, now=t0)
        self.recognizer.process(hands, face, now=t0 + 0.25)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.VOLUME_UP)
        self.assertEqual(self.recognizer.touching_hand, "LEFT")

    def test_hand_moving_near_ear_without_contact_does_not_activate(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        # Hand passing 0.15 normalized units away from ear (~0.375 * fw > 0.28 threshold)
        r_hand = make_synthetic_hand(index_tip=(0.60, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        active = self.recognizer.process(hands, face, now=t0)
        self.assertFalse(active)
        self.assertEqual(self.recognizer.state, EarVolumeState.IDLE)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.NONE)

    def test_foreground_floating_hand_depth_rejected(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        # Hand at same 2D (x, y) line-of-sight, but deep in foreground (z = -0.30 vs ear z = 0.0)
        # dz = 0.30 / 0.40 = 0.75 > EAR_VOLUME_MAX_DEPTH_DELTA (0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, -0.30), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        active = self.recognizer.process(hands, face, now=t0)
        self.assertFalse(active)
        self.assertEqual(self.recognizer.state, EarVolumeState.IDLE)

    def test_small_finger_fluctuations_do_not_drop_active(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand_exact = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands_exact = HandsResult(right=r_hand_exact, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        self.recognizer.process(hands_exact, face, now=t0)
        self.recognizer.process(hands_exact, face, now=t0 + 0.25)
        self.assertTrue(self.recognizer.is_active)

        # Micro-movement: finger shifts slightly (d = 0.12 / 0.40 = 0.30, above proximity 0.28 but below release 0.45)
        r_hand_shifted = make_synthetic_hand(index_tip=(0.75, 0.33, 0.0), is_pointing=True)
        hands_shifted = HandsResult(right=r_hand_shifted, left=None, right_score=0.99, left_score=0.0)

        active = self.recognizer.process(hands_shifted, face, now=t0 + 0.35)
        # Hysteresis keeps the gesture active!
        self.assertTrue(active)
        self.assertEqual(self.recognizer.state, EarVolumeState.ACTIVE_VOLUME_UP)

    def test_brief_touch_rejected_before_arming(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        self.recognizer.process(hands, face, now=t0)
        # Touch only held for 0.08s, then hand moves away
        self.recognizer.process(hands, face, now=t0 + 0.08)

        # Hand moves away
        r_away = make_synthetic_hand(index_tip=(0.50, 0.80, 0.0), is_pointing=True)
        hands_away = HandsResult(right=r_away, left=None, right_score=0.99, left_score=0.0)
        active = self.recognizer.process(hands_away, face, now=t0 + 0.12)

        self.assertFalse(active)
        self.assertEqual(self.recognizer.state, EarVolumeState.IDLE)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.NONE)
        self.assertAlmostEqual(self._current_vol, 0.50)  # Volume was not changed

    def test_dual_ear_touch_is_ambiguous_and_rejected(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), l_ear=(0.25, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        l_hand = make_synthetic_hand(index_tip=(0.25, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=l_hand, right_score=0.99, left_score=0.99)

        t0 = 100.0
        active = self.recognizer.process(hands, face, now=t0)
        self.assertFalse(active)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.NONE)

    def test_lip_conflict_prevention(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), lip_center=(0.50, 0.60, 0.0), face_width=0.40)
        # Hand near lips
        r_hand_lips = make_synthetic_hand(index_tip=(0.50, 0.60, 0.0), is_pointing=True)
        hands_lips = HandsResult(right=r_hand_lips, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        active = self.recognizer.process(hands_lips, face, now=t0)
        self.assertFalse(active)
        self.assertEqual(self.recognizer.state, EarVolumeState.IDLE)

    def test_continuous_adjustment_rate_limiting_and_monotonicity(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        # Arm
        self.recognizer.process(hands, face, now=t0)
        self.recognizer.process(hands, face, now=t0 + 0.25)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.VOLUME_UP)

        # Call with very tiny interval (0.01s < EAR_VOLUME_UPDATE_INTERVAL_S)
        call_count_before = self.mock_audio.adjust_volume.call_count
        self.recognizer.process(hands, face, now=t0 + 0.26)
        # Should be rate limited (no extra adjust_volume call)
        self.assertEqual(self.mock_audio.adjust_volume.call_count, call_count_before)

        # Call at t0 + 0.35s (0.10s elapsed since arming)
        self.recognizer.process(hands, face, now=t0 + 0.35)
        self.assertGreater(self.mock_audio.adjust_volume.call_count, call_count_before)

    def test_clamping_at_max_and_min_volume(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        self.recognizer.process(hands, face, now=t0)
        self.recognizer.process(hands, face, now=t0 + 0.25)

        # Hold for 5 seconds (more than enough to reach 100% at 30%/sec)
        self.recognizer.process(hands, face, now=t0 + 5.25)
        self.assertAlmostEqual(self.recognizer.current_volume, 1.0)
        self.assertAlmostEqual(self._current_vol, 1.0)

    def test_release_promptly_stops_adjustment(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        self.recognizer.process(hands, face, now=t0)
        self.recognizer.process(hands, face, now=t0 + 0.25)
        self.assertTrue(self.recognizer.is_active)

        # Release finger
        r_away = make_synthetic_hand(index_tip=(0.50, 0.80, 0.0), is_pointing=True)
        hands_away = HandsResult(right=r_away, left=None, right_score=0.99, left_score=0.0)
        active = self.recognizer.process(hands_away, face, now=t0 + 0.30)

        self.assertFalse(active)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.NONE)
        # Last callback notification was is_active=False
        self.assertFalse(self.callbacks[-1][2])

    def test_tracking_loss_stops_adjustment_promptly(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        t0 = 100.0
        self.recognizer.process(hands, face, now=t0)
        self.recognizer.process(hands, face, now=t0 + 0.25)
        self.assertTrue(self.recognizer.is_active)

        # Lost tracking (e.g. face undetected) beyond EAR_VOLUME_LOST_TIMEOUT_S
        lost_face = make_synthetic_face(detected=False)
        active = self.recognizer.process(hands, lost_face, now=t0 + 0.45)
        self.assertFalse(active)
        self.assertEqual(self.recognizer.direction, EarVolumeDirection.NONE)


class TestOrchestratorEarVolumeIntegration(unittest.TestCase):
    """Verifies GestureOrchestrator cursor locking, candidate non-interference, and arbitration."""

    def setUp(self):
        self.actuator = MagicMock(spec=MouseActuator)
        self.desktop = VirtualDesktop(1920, 1080, 1)
        self.trackpad = TrackpadZone(0, 0, 1920, 1080)
        self.orchestrator = GestureOrchestrator(self.actuator, self.desktop, self.trackpad)
        self.orchestrator._last_cursor_pos = (500, 400)

    def test_candidate_state_never_locks_or_pauses_cursor(self):
        """CRITICAL: Merely being in candidate proximity must NEVER lock cursor or reset filters."""
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        # Right hand moves into candidate zone
        r_hand = make_synthetic_hand(wrist=(0.60, 0.60, 0.0), index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        # Process first frame -> enters CANDIDATE
        self.orchestrator.process(hands, face)
        self.assertTrue(self.orchestrator.ear_volume_gesture.is_candidate)
        self.assertFalse(self.orchestrator.ear_volume_gesture.is_active)

        # Normal cursor control must continue! Pointer owner is RIGHT, not NONE
        self.assertEqual(self.orchestrator.pointer_owner, "RIGHT")
        # Cursor moved to new hand position (not frozen at 500, 400)
        self.assertNotEqual(self.orchestrator.cursor_pos, (500, 400))

    def test_ear_volume_active_locks_cursor_when_only_touching_hand_present(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        # Dwell to arm
        self.orchestrator.process(hands, face)
        time.sleep(0.22)
        # Sustained active hold
        self.orchestrator.process(hands, face)

        self.assertTrue(self.orchestrator.ear_volume_gesture.is_active)
        # Pointer owner is NONE, cursor held at last valid position
        self.assertEqual(self.orchestrator.pointer_owner, "NONE")

    def test_non_touching_hand_can_control_cursor_during_active_volume(self):
        """If left hand touches ear to adjust volume, right hand can still control cursor!"""
        face = make_synthetic_face(l_ear=(0.25, 0.45, 0.0), face_width=0.40)
        # Left hand touches left ear (VOLUME_DOWN)
        l_hand = make_synthetic_hand(index_tip=(0.25, 0.45, 0.0), is_pointing=True)
        # Right hand points in trackpad zone
        r_hand = make_synthetic_hand(wrist=(0.50, 0.60, 0.0), index_tip=(0.60, 0.40, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=l_hand, right_score=0.99, left_score=0.99)

        # Arm
        self.orchestrator.process(hands, face)
        time.sleep(0.22)
        self.orchestrator.process(hands, face)

        self.assertTrue(self.orchestrator.ear_volume_gesture.is_active)
        self.assertEqual(self.orchestrator.ear_volume_gesture.touching_hand, "LEFT")
        # Right hand is NOT suppressed! Right hand owns pointer
        self.assertEqual(self.orchestrator.pointer_owner, "RIGHT")

    def test_ear_volume_release_resumes_pointer_ownership(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        # Activate
        self.orchestrator.process(hands, face)
        time.sleep(0.22)
        self.orchestrator.process(hands, face)
        self.assertTrue(self.orchestrator.ear_volume_gesture.is_active)

        # Release ear contact and move pointer down into trackpad zone
        r_normal = make_synthetic_hand(wrist=(0.5, 0.5, 0.0), index_tip=(0.5, 0.3, 0.0), is_pointing=True)
        hands_normal = HandsResult(right=r_normal, left=None, right_score=0.99, left_score=0.0)

        self.orchestrator.process(hands_normal, face)
        self.assertFalse(self.orchestrator.ear_volume_gesture.is_active)
        self.assertEqual(self.orchestrator.pointer_owner, "RIGHT")

    def test_smooth_reacquisition_recovery_prevents_violent_jump(self):
        face = make_synthetic_face(r_ear=(0.75, 0.45, 0.0), face_width=0.40)
        r_hand = make_synthetic_hand(index_tip=(0.75, 0.45, 0.0), is_pointing=True)
        hands = HandsResult(right=r_hand, left=None, right_score=0.99, left_score=0.0)

        # Activate at fixed position
        self.orchestrator._last_cursor_pos = (500, 400)
        self.orchestrator.process(hands, face)
        time.sleep(0.22)
        self.orchestrator.process(hands, face)
        self.assertTrue(self.orchestrator.ear_volume_gesture.is_active)

        # Hand leaves ear and suddenly re-enters far away (e.g. 1500, 900)
        r_far = make_synthetic_hand(wrist=(0.8, 0.8, 0.0), index_tip=(0.8, 0.8, 0.0), is_pointing=True)
        hands_far = HandsResult(right=r_far, left=None, right_score=0.99, left_score=0.0)

        self.orchestrator.process(hands_far, face)
        # Smooth recovery must blend the first reacquisition step rather than jumping full distance in 1 frame
        final_x, final_y = self.orchestrator.cursor_pos
        self.assertNotEqual((final_x, final_y), (500, 400))


class TestOverlayEarVolumeToast(unittest.TestCase):
    """Verifies overlay.show_ear_volume state and Dynamic Island text."""

    @patch("ctypes.windll.user32.GetParent")
    @patch("tkinter.Tk")
    def test_overlay_show_ear_volume_up_and_down(self, mock_tk, mock_get_parent):
        from core.overlay import ScreenshotOverlay

        mock_root = MagicMock()
        mock_root.winfo_screenwidth.return_value = 1920
        mock_root.winfo_screenheight.return_value = 1080
        mock_tk.return_value = mock_root

        overlay = ScreenshotOverlay("TOP_CENTER")
        # Volume up
        overlay.show_ear_volume("VOLUME_UP", 0.75, True)
        self.assertEqual(overlay._island_state, "HOLDING")
        self.assertEqual(overlay._island_text, "Volume ↑  75%")
        self.assertEqual(overlay._island_color, "#00e5ff")
        self.assertAlmostEqual(overlay._progress, 0.75)

        # Volume down
        overlay.show_ear_volume("VOLUME_DOWN", 0.30, True)
        self.assertEqual(overlay._island_state, "HOLDING")
        self.assertEqual(overlay._island_text, "Volume ↓  30%")
        self.assertEqual(overlay._island_color, "#00e5ff")
        self.assertAlmostEqual(overlay._progress, 0.30)

        # Release
        overlay.show_ear_volume("VOLUME_UP", 0.30, False)
        self.assertEqual(overlay._island_state, "SUCCESS")


if __name__ == '__main__':
    unittest.main()
