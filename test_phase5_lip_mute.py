# test_phase5_lip_mute.py
"""
Unit tests for Phase 5: Lip-Touch Windows System Audio Mute and Restore.
Tests:
  - AudioController mute, volume preservation, unmute, and volume restoration.
  - AudioController error handling and missing device tolerance.
  - LipMuteGestureRecognizer pose geometry, 0.6s hold requirement, and debouncing.
  - Rejection of brief touches and fast hand sweeps (stability checks).
  - Both left and right hand candidate support.
  - Face landmark presence requirement and cancellation on lost tracking.
  - Single-fire guarantee and release-rearm requirement.
  - GestureOrchestrator priority arbitration and cursor locking.
  - Dynamic Island toast feedback integration.
"""

import unittest
from unittest.mock import MagicMock, patch
import time

from core.tracker import Landmark, HandsResult
from core.face_tracker import FaceResult
from core.audio_controller import AudioController
from core.gestures.lip_mute_gesture import (
    LipMuteGestureRecognizer,
    LipMuteState,
    dist2d,
)
from core.display import VirtualDesktop, TrackpadZone
from core.actuator import MouseActuator
from core.gestures.orchestrator import GestureOrchestrator
from config import (
    LIP_MUTE_HOLD_DURATION_S,
    LIP_MUTE_PROXIMITY_THRESHOLD,
    LIP_MUTE_RELEASE_THRESHOLD,
    LIP_MUTE_REARM_DURATION_S,
)


def make_synthetic_hand(
    wrist=(0.5, 0.8, 0.0),
    index_tip=(0.5, 0.5, 0.0),
    is_pointing=True,
) -> list[Landmark]:
    """Generates 21 synthetic landmarks for a hand."""
    lms = [Landmark(x=wrist[0], y=wrist[1], z=wrist[2]) for _ in range(21)]
    # Set PIP and TIP for index
    lms[6] = Landmark(x=index_tip[0], y=index_tip[1] + 0.08, z=0.0)  # PIP
    lms[8] = Landmark(x=index_tip[0], y=index_tip[1], z=index_tip[2])  # TIP
    if not is_pointing:
        # Index tip curled below PIP
        lms[8] = Landmark(x=index_tip[0], y=index_tip[1] + 0.12, z=0.0)
    return lms


def make_synthetic_face(
    lip_center=(0.5, 0.5, 0.0),
    mouth_width=0.10,
    detected=True,
    confidence=1.0,
) -> FaceResult:
    """Generates synthetic FaceResult with specified lip center and scale."""
    if not detected:
        return FaceResult(detected=False)

    return FaceResult(
        detected=True,
        lip_center=lip_center,
        mouth_width=mouth_width,
        face_width=mouth_width * 2.5,
        confidence=confidence,
    )


class TestAudioController(unittest.TestCase):
    """Verifies AudioController Windows Core Audio operations using a mock endpoint."""

    def setUp(self):
        self.mock_endpoint = MagicMock()
        self._mute_val = 0
        def set_mute(val, _):
            self._mute_val = int(val)
        self.mock_endpoint.SetMute.side_effect = set_mute
        self.mock_endpoint.GetMute.side_effect = lambda: self._mute_val
        self.mock_endpoint.GetMasterVolumeLevelScalar.return_value = 0.65
        self.controller = AudioController(endpoint_override=self.mock_endpoint)

    def test_first_action_mutes_and_saves_volume(self):
        success, action = self.controller.toggle_mute()
        self.assertTrue(success)
        self.assertEqual(action, "MUTED")
        self.assertTrue(self.controller.muted_by_air_point)
        self.assertAlmostEqual(self.controller.saved_volume_scalar, 0.65)
        self.mock_endpoint.SetMute.assert_called_with(1, None)

    def test_second_action_unmutes_and_restores_volume(self):
        # First mute
        self.controller.toggle_mute()
        self.assertTrue(self.controller.muted_by_air_point)

        # Second action: restore
        success, action = self.controller.toggle_mute()
        self.assertTrue(success)
        self.assertEqual(action, "RESTORED")
        self.assertFalse(self.controller.muted_by_air_point)
        self.mock_endpoint.SetMute.assert_called_with(0, None)
        self.mock_endpoint.SetMasterVolumeLevelScalar.assert_called_with(0.65, None)

    def test_mute_verification_failure_handled(self):
        # Simulate endpoint refusing to mute
        self.mock_endpoint.SetMute.side_effect = None
        self.mock_endpoint.GetMute.side_effect = None
        self.mock_endpoint.GetMute.return_value = 0
        success = self.controller.mute()
        self.assertFalse(success)
        self.assertFalse(self.controller.muted_by_air_point)

    def test_missing_device_handled_gracefully(self):
        def raise_err():
            raise RuntimeError("No audio endpoint found")

        err_controller = AudioController()
        err_controller._get_endpoint = raise_err
        success, action = err_controller.toggle_mute()
        self.assertFalse(success)
        self.assertEqual(action, "FAILED")


class TestLipMuteGestureRecognizer(unittest.TestCase):
    """Verifies lip mute gesture detection, stability, single-fire, and rearm logic."""

    def setUp(self):
        self.mock_audio = MagicMock(spec=AudioController)
        self.mock_audio.toggle_mute.return_value = (True, "MUTED")
        self.recognizer = LipMuteGestureRecognizer(audio_controller=self.mock_audio)

    def test_lip_touch_for_required_hold_activates_once(self):
        t0 = 100.0
        face = make_synthetic_face(lip_center=(0.5, 0.5, 0.0), mouth_width=0.10)
        # Finger right at lips: distance = 0.0
        hand_lms = make_synthetic_hand(index_tip=(0.5, 0.5, 0.0))
        hands = HandsResult(right=hand_lms)

        # Frame 1: Enter CANDIDATE
        self.recognizer.process(hands, face, now=t0)
        self.assertEqual(self.recognizer.state, LipMuteState.CANDIDATE)
        self.mock_audio.toggle_mute.assert_not_called()

        # Frame 2: Halfway through hold (0.3s)
        self.recognizer.process(hands, face, now=t0 + 0.30)
        self.assertEqual(self.recognizer.state, LipMuteState.CANDIDATE)
        self.mock_audio.toggle_mute.assert_not_called()

        # Frame 3: Completed hold duration (0.61s)
        self.recognizer.process(hands, face, now=t0 + 0.61)
        self.assertEqual(self.recognizer.state, LipMuteState.CONFIRMED)
        self.mock_audio.toggle_mute.assert_called_once()

    def test_brief_touch_does_not_activate(self):
        t0 = 100.0
        face = make_synthetic_face(lip_center=(0.5, 0.5, 0.0), mouth_width=0.10)
        hand_lms = make_synthetic_hand(index_tip=(0.5, 0.5, 0.0))
        hands = HandsResult(right=hand_lms)

        # Touch begins
        self.recognizer.process(hands, face, now=t0)
        self.assertEqual(self.recognizer.state, LipMuteState.CANDIDATE)

        # Finger removed at 0.25s (far from lips)
        hand_away = make_synthetic_hand(index_tip=(0.5, 0.8, 0.0))
        hands_away = HandsResult(right=hand_away)
        self.recognizer.process(hands_away, face, now=t0 + 0.25)
        self.assertEqual(self.recognizer.state, LipMuteState.IDLE)
        self.mock_audio.toggle_mute.assert_not_called()

    def test_fast_hand_sweep_rejected_by_stability_check(self):
        t0 = 100.0
        face = make_synthetic_face(lip_center=(0.5, 0.5, 0.0), mouth_width=0.10)
        # Starts near left lip corner
        hand_start = make_synthetic_hand(index_tip=(0.46, 0.5, 0.0))
        hands_1 = HandsResult(right=hand_start)
        self.recognizer.process(hands_1, face, now=t0)
        self.assertEqual(self.recognizer.state, LipMuteState.CANDIDATE)

        # Rapidly sweeps across mouth (drift > 0.45 mouth widths)
        hand_sweep = make_synthetic_hand(index_tip=(0.54, 0.5, 0.0))
        hands_2 = HandsResult(right=hand_sweep)
        self.recognizer.process(hands_2, face, now=t0 + 0.15)
        self.assertEqual(self.recognizer.state, LipMuteState.IDLE)
        self.mock_audio.toggle_mute.assert_not_called()

    def test_left_hand_can_activate(self):
        t0 = 100.0
        face = make_synthetic_face(lip_center=(0.5, 0.5, 0.0), mouth_width=0.10)
        left_lms = make_synthetic_hand(index_tip=(0.5, 0.5, 0.0))
        hands = HandsResult(left=left_lms)

        self.recognizer.process(hands, face, now=t0)
        self.assertEqual(self.recognizer.state, LipMuteState.CANDIDATE)
        self.assertEqual(self.recognizer.candidate_hand, "LEFT")

        self.recognizer.process(hands, face, now=t0 + 0.65)
        self.assertEqual(self.recognizer.state, LipMuteState.CONFIRMED)
        self.mock_audio.toggle_mute.assert_called_once()

    def test_gesture_does_not_activate_without_face(self):
        t0 = 100.0
        hand_lms = make_synthetic_hand(index_tip=(0.5, 0.5, 0.0))
        hands = HandsResult(right=hand_lms)

        # No face provided
        self.recognizer.process(hands, face=None, now=t0)
        self.assertEqual(self.recognizer.state, LipMuteState.IDLE)

        # Face detected = False
        face_undetected = make_synthetic_face(detected=False)
        self.recognizer.process(hands, face=face_undetected, now=t0 + 0.1)
        self.assertEqual(self.recognizer.state, LipMuteState.IDLE)
        self.mock_audio.toggle_mute.assert_not_called()

    def test_lost_landmarks_cancel_candidate_hold(self):
        t0 = 100.0
        face = make_synthetic_face(lip_center=(0.5, 0.5, 0.0), mouth_width=0.10)
        hand_lms = make_synthetic_hand(index_tip=(0.5, 0.5, 0.0))
        hands = HandsResult(right=hand_lms)

        self.recognizer.process(hands, face, now=t0)
        self.assertEqual(self.recognizer.state, LipMuteState.CANDIDATE)

        # Face suddenly lost at 0.3s
        self.recognizer.process(hands, face=None, now=t0 + 0.3)
        self.assertEqual(self.recognizer.state, LipMuteState.IDLE)

    def test_held_pose_does_not_repeat_toggle(self):
        t0 = 100.0
        face = make_synthetic_face(lip_center=(0.5, 0.5, 0.0), mouth_width=0.10)
        hand_lms = make_synthetic_hand(index_tip=(0.5, 0.5, 0.0))
        hands = HandsResult(right=hand_lms)

        # Trigger activation
        self.recognizer.process(hands, face, now=t0)
        self.recognizer.process(hands, face, now=t0 + 0.65)
        self.assertEqual(self.recognizer.state, LipMuteState.CONFIRMED)
        self.assertEqual(self.mock_audio.toggle_mute.call_count, 1)

        # Keep holding for 2 more seconds
        for offset in (1.0, 1.5, 2.0, 2.5):
            self.recognizer.process(hands, face, now=t0 + offset)
            self.assertEqual(self.mock_audio.toggle_mute.call_count, 1)

    def test_rearm_requires_confirmed_release(self):
        t0 = 100.0
        face = make_synthetic_face(lip_center=(0.5, 0.5, 0.0), mouth_width=0.10)
        hand_touch = make_synthetic_hand(index_tip=(0.5, 0.5, 0.0))
        hand_away = make_synthetic_hand(index_tip=(0.5, 0.8, 0.0))

        # First trigger
        self.recognizer.process(HandsResult(right=hand_touch), face, now=t0)
        self.recognizer.process(HandsResult(right=hand_touch), face, now=t0 + 0.65)
        self.assertEqual(self.mock_audio.toggle_mute.call_count, 1)

        # Move hand away for only 0.1s (< 0.30s rearm duration)
        self.recognizer.process(HandsResult(right=hand_away), face, now=t0 + 0.70)
        self.assertEqual(self.recognizer.state, LipMuteState.WAITING_RELEASE)

        # Touch again before rearm completes -> stays WAITING_RELEASE, no trigger
        self.recognizer.process(HandsResult(right=hand_touch), face, now=t0 + 0.80)
        self.assertEqual(self.recognizer.state, LipMuteState.WAITING_RELEASE)

        # Move hand away for full 0.35s
        self.recognizer.process(HandsResult(right=hand_away), face, now=t0 + 0.90)
        self.recognizer.process(HandsResult(right=hand_away), face, now=t0 + 1.25)
        self.assertEqual(self.recognizer.state, LipMuteState.IDLE)

        # Now touch again -> activates second toggle (restore)
        self.mock_audio.toggle_mute.return_value = (True, "RESTORED")
        self.recognizer.process(HandsResult(right=hand_touch), face, now=t0 + 1.30)
        self.recognizer.process(HandsResult(right=hand_touch), face, now=t0 + 1.95)
        self.assertEqual(self.mock_audio.toggle_mute.call_count, 2)


class TestOrchestratorLipIntegration(unittest.TestCase):
    """Verifies GestureOrchestrator priority arbitration and cursor locking for LipMute."""

    def setUp(self):
        self.actuator = MagicMock(spec=MouseActuator)
        self.desktop = VirtualDesktop(1920, 1080, 1)
        self.trackpad = TrackpadZone(0, 0, 1920, 1080)
        self.orchestrator = GestureOrchestrator(self.actuator, self.desktop, self.trackpad)
        # Mock audio controller inside orchestrator
        self.orchestrator._audio_controller = MagicMock(spec=AudioController)
        self.orchestrator._audio_controller.toggle_mute.return_value = (True, "MUTED")
        self.orchestrator._lip_gesture._audio = self.orchestrator._audio_controller

    def test_orchestrator_initializes_lip_gesture(self):
        self.assertIsNotNone(self.orchestrator.lip_gesture)
        self.assertEqual(self.orchestrator.lip_state, "IDLE")
        self.assertEqual(self.orchestrator.lip_status_text, "READY")

    def test_orchestrator_locks_cursor_during_lip_candidate(self):
        face = make_synthetic_face(lip_center=(0.5, 0.5, 0.0), mouth_width=0.10)
        hand_lms = make_synthetic_hand(index_tip=(0.5, 0.5, 0.0))
        hands = HandsResult(right=hand_lms)

        self.orchestrator._last_cursor_pos = (800, 600)
        self.orchestrator.process(hands, face)
        self.assertEqual(self.orchestrator.lip_state, "CANDIDATE")
        # Actuator should hold last known cursor position
        self.actuator.move.assert_called_with(800, 600)


class TestDynamicIslandAudioToast(unittest.TestCase):
    """Verifies Dynamic Island capsule shows correct state and text for audio actions."""

    @patch("ctypes.windll.user32.GetParent")
    @patch("tkinter.Tk")
    def test_show_audio_toast_muted(self, mock_tk, mock_get_parent):
        from core.overlay import ScreenshotOverlay
        mock_root = MagicMock()
        mock_root.winfo_screenwidth.return_value = 1920
        mock_root.winfo_screenheight.return_value = 1080
        mock_tk.return_value = mock_root

        overlay = ScreenshotOverlay("TOP_CENTER")
        overlay.show_audio_toast("MUTED", success=True)
        self.assertEqual(overlay._island_state, "SUCCESS")
        self.assertEqual(overlay._island_text, "Muted")
        self.assertEqual(overlay._island_color, "#ff9900")
        mock_root.deiconify.assert_called()

    @patch("ctypes.windll.user32.GetParent")
    @patch("tkinter.Tk")
    def test_show_audio_toast_restored(self, mock_tk, mock_get_parent):
        from core.overlay import ScreenshotOverlay
        mock_root = MagicMock()
        mock_root.winfo_screenwidth.return_value = 1920
        mock_root.winfo_screenheight.return_value = 1080
        mock_tk.return_value = mock_root

        overlay = ScreenshotOverlay("TOP_CENTER")
        overlay.show_audio_toast("RESTORED", success=True)
        self.assertEqual(overlay._island_state, "SUCCESS")
        self.assertEqual(overlay._island_text, "Volume restored")
        self.assertEqual(overlay._island_color, "#00ff66")

    @patch("ctypes.windll.user32.GetParent")
    @patch("tkinter.Tk")
    def test_show_audio_toast_failed(self, mock_tk, mock_get_parent):
        from core.overlay import ScreenshotOverlay
        mock_root = MagicMock()
        mock_root.winfo_screenwidth.return_value = 1920
        mock_root.winfo_screenheight.return_value = 1080
        mock_tk.return_value = mock_root

        overlay = ScreenshotOverlay("TOP_CENTER")
        overlay.show_audio_toast("FAILED", success=False)
        self.assertEqual(overlay._island_state, "ERROR")
        self.assertEqual(overlay._island_text, "Audio control failed")
        self.assertEqual(overlay._island_color, "#ff4444")


if __name__ == "__main__":
    unittest.main()
