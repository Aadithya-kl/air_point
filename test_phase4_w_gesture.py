# test_phase4_w_gesture.py
"""
Unit tests for Phase 4: Two-Hand W Gesture to Open WhatsApp Desktop.
Tests:
  - Synthetic landmark generation for the reference W pose.
  - Geometry validation (finger extension, apex touching, lateral arrangement, palm facing).
  - Rejection tests (screenshot 10-finger pose, single hand, fingers curled/extended wrong, hands crossed).
  - State machine transitions (IDLE -> CANDIDATE -> CONFIRMED -> COOLDOWN -> IDLE).
  - Exact 0.7s hold duration requirement.
  - Single-fire guarantee (no multiple launches while pose is held).
  - Re-arm requirement after release.
  - AppLauncher protocol launch and fallback.
  - GestureOrchestrator integration and cursor locking during hold.
"""

import unittest
from unittest.mock import MagicMock, patch
import time

from core.tracker import Landmark, HandsResult
from core.gestures.w_gesture import (
    WState,
    WGestureRecognizer,
    check_w_pose,
    is_w_finger_configuration,
)
from core.app_launcher import AppLauncher
from core.actuator import MouseActuator
from core.display import VirtualDesktop, TrackpadZone
from core.gestures.orchestrator import GestureOrchestrator
from config import (
    W_HOLD_DURATION_S,
    W_GRACE_PERIOD_S,
    W_REARM_DURATION_S,
)


def make_hand(
    wrist_xy=(0.5, 0.7),
    is_right=False,
    index_ext=True,
    middle_ext=False,
    ring_ext=False,
    pinky_ext=True,
    index_tip_xy=None,
    pinky_tip_xy=None,
    palm_facing=True,
) -> list[Landmark]:
    """Generates a list of 21 synthetic landmarks representing a hand pose."""
    wx, wy = wrist_xy
    lms = [Landmark(x=wx, y=wy, z=0.0) for _ in range(21)]

    # MCP bases (palm knuckles)
    # 5: Index MCP, 9: Middle MCP, 13: Ring MCP, 17: Pinky MCP
    spread = 0.03 if not is_right else -0.03
    lms[5]  = Landmark(x=wx + spread * 1.0, y=wy - 0.10, z=0.0)  # index MCP
    lms[9]  = Landmark(x=wx + spread * 0.3, y=wy - 0.11, z=0.0)  # middle MCP
    lms[13] = Landmark(x=wx - spread * 0.3, y=wy - 0.10, z=0.0)  # ring MCP
    lms[17] = Landmark(x=wx - spread * 1.0, y=wy - 0.09, z=0.0)  # pinky MCP

    # Thumb:
    # Mirrored camera:
    # Left hand: palm facing means thumb is to right of pinky (lm[4].x > lm[20].x)
    # Right hand: palm facing means thumb is to left of pinky (lm[4].x < lm[20].x)
    if not is_right:
        if palm_facing:
            thumb_x = wx + 0.05
        else:
            thumb_x = (pinky_tip_xy[0] - 0.05) if pinky_tip_xy else (wx - 0.10)
        lms[2] = Landmark(x=wx + (thumb_x - wx) * 0.4, y=wy - 0.05, z=0.0)
        lms[3] = Landmark(x=wx + (thumb_x - wx) * 0.7, y=wy - 0.07, z=0.0)
        lms[4] = Landmark(x=thumb_x, y=wy - 0.08, z=0.0)
    else:
        if palm_facing:
            thumb_x = wx - 0.05
        else:
            thumb_x = (pinky_tip_xy[0] + 0.05) if pinky_tip_xy else (wx + 0.10)
        lms[2] = Landmark(x=wx + (thumb_x - wx) * 0.4, y=wy - 0.05, z=0.0)
        lms[3] = Landmark(x=wx + (thumb_x - wx) * 0.7, y=wy - 0.07, z=0.0)
        lms[4] = Landmark(x=thumb_x, y=wy - 0.08, z=0.0)

    # Index (6: PIP, 8: TIP)
    if index_tip_xy:
        itx, ity = index_tip_xy
        lms[6] = Landmark(x=(lms[5].x + itx) / 2.0, y=(lms[5].y + ity) / 2.0, z=0.0)
        lms[8] = Landmark(x=itx, y=ity, z=0.0)
    elif index_ext:
        lms[6] = Landmark(x=lms[5].x + spread * 0.5, y=wy - 0.15, z=0.0)
        lms[8] = Landmark(x=lms[5].x + spread * 1.0, y=wy - 0.22, z=0.0)
    else:
        # Curled
        lms[6] = Landmark(x=lms[5].x, y=wy - 0.08, z=0.0)
        lms[8] = Landmark(x=lms[5].x, y=wy - 0.04, z=0.0)

    # Middle (10: PIP, 12: TIP)
    if middle_ext:
        lms[10] = Landmark(x=lms[9].x, y=wy - 0.16, z=0.0)
        lms[12] = Landmark(x=lms[9].x, y=wy - 0.24, z=0.0)
    else:
        # Curled down into palm
        lms[10] = Landmark(x=lms[9].x, y=wy - 0.08, z=0.0)
        lms[12] = Landmark(x=lms[9].x, y=wy - 0.04, z=0.0)

    # Ring (14: PIP, 16: TIP)
    if ring_ext:
        lms[14] = Landmark(x=lms[13].x, y=wy - 0.15, z=0.0)
        lms[16] = Landmark(x=lms[13].x, y=wy - 0.22, z=0.0)
    else:
        # Curled down into palm
        lms[14] = Landmark(x=lms[13].x, y=wy - 0.08, z=0.0)
        lms[16] = Landmark(x=lms[13].x, y=wy - 0.04, z=0.0)

    # Pinky (18: PIP, 20: TIP)
    if pinky_tip_xy:
        ptx, pty = pinky_tip_xy
        lms[18] = Landmark(x=(lms[17].x + ptx) / 2.0, y=(lms[17].y + pty) / 2.0, z=0.0)
        lms[20] = Landmark(x=ptx, y=pty, z=0.0)
    elif pinky_ext:
        lms[18] = Landmark(x=lms[17].x - spread * 0.5, y=wy - 0.13, z=0.0)
        lms[20] = Landmark(x=lms[17].x - spread * 1.0, y=wy - 0.20, z=0.0)
    else:
        lms[18] = Landmark(x=lms[17].x, y=wy - 0.07, z=0.0)
        lms[20] = Landmark(x=lms[17].x, y=wy - 0.03, z=0.0)

    return lms


def make_w_hands(
    touching=True,
    left_facing=True,
    right_facing=True,
    left_conf=0.95,
    right_conf=0.95,
) -> HandsResult:
    """Generates two hands matching the reference W gesture."""
    touch_gap = 0.01 if touching else 0.25

    # Left hand: wrist at (0.42, 0.70), index tip at (0.485 - gap/2, 0.48)
    left_index_tip = (0.50 - touch_gap / 2.0, 0.48)
    left_pinky_tip = (0.35, 0.46)

    left_lm = make_hand(
        wrist_xy=(0.42, 0.70),
        is_right=False,
        index_ext=True,
        middle_ext=False,
        ring_ext=False,
        pinky_ext=True,
        index_tip_xy=left_index_tip,
        pinky_tip_xy=left_pinky_tip,
        palm_facing=left_facing,
    )

    # Right hand: wrist at (0.58, 0.70), index tip at (0.50 + gap/2, 0.48)
    right_index_tip = (0.50 + touch_gap / 2.0, 0.48)
    right_pinky_tip = (0.65, 0.46)

    right_lm = make_hand(
        wrist_xy=(0.58, 0.70),
        is_right=True,
        index_ext=True,
        middle_ext=False,
        ring_ext=False,
        pinky_ext=True,
        index_tip_xy=right_index_tip,
        pinky_tip_xy=right_pinky_tip,
        palm_facing=right_facing,
    )

    return HandsResult(
        left=left_lm,
        right=right_lm,
        left_score=left_conf,
        right_score=right_conf,
    )


class TestWGesturePoseValidation(unittest.TestCase):
    """Verifies geometric recognition of the W pose and rejection of invalid poses."""

    def test_valid_w_pose_recognized(self):
        hands = make_w_hands(touching=True)
        valid, reason = check_w_pose(hands)
        self.assertTrue(valid, f"Expected valid W pose, got reason: {reason}")
        self.assertEqual(reason, "W_POSE_VALID")

    def test_reject_single_hand(self):
        hands = make_w_hands(touching=True)
        hands.left = None
        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)
        self.assertEqual(reason, "BOTH_HANDS_REQUIRED")

        hands = make_w_hands(touching=True)
        hands.right = None
        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)
        self.assertEqual(reason, "BOTH_HANDS_REQUIRED")

    def test_reject_low_confidence(self):
        hands = make_w_hands(touching=True, left_conf=0.40)
        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)
        self.assertEqual(reason, "LOW_CONFIDENCE")

    def test_reject_palms_facing_away(self):
        hands = make_w_hands(touching=True, left_facing=False)
        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)
        self.assertEqual(reason, "PALMS_NOT_FACING")

    def test_reject_screenshot_open_palms(self):
        """Ten extended fingers (screenshot gesture) must never trigger W gesture."""
        left_lm = make_hand(wrist_xy=(0.4, 0.7), is_right=False, index_ext=True, middle_ext=True, ring_ext=True, pinky_ext=True)
        right_lm = make_hand(wrist_xy=(0.6, 0.7), is_right=True, index_ext=True, middle_ext=True, ring_ext=True, pinky_ext=True)
        hands = HandsResult(left=left_lm, right=right_lm, left_score=0.95, right_score=0.95)

        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)
        self.assertIn("FINGERS_MISMATCH", reason)

    def test_reject_index_tips_not_touching(self):
        hands = make_w_hands(touching=False)
        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)
        self.assertIn("INDEX_TIPS_NOT_TOUCHING", reason)

    def test_reject_middle_finger_extended(self):
        hands = make_w_hands(touching=True)
        # Extend middle finger on right hand
        hands.right[12].y = hands.right[8].y - 0.05
        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)
        self.assertEqual(reason, "RIGHT_HAND_FINGERS_MISMATCH")

    def test_reject_pinky_curled(self):
        hands = make_w_hands(touching=True)
        # Curl pinky on left hand
        hands.left[20].y = hands.left[18].y + 0.05
        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)
        self.assertEqual(reason, "LEFT_HAND_FINGERS_MISMATCH")

    def test_reject_hands_crossed(self):
        """Hands where left pinky is positioned to the right of right pinky."""
        hands = make_w_hands(touching=True)
        # Swap x positions of pinkies
        hands.left[20].x = 0.80
        hands.right[20].x = 0.20
        valid, reason = check_w_pose(hands)
        self.assertFalse(valid)


class TestWGestureStateMachine(unittest.TestCase):
    """Tests hold timer, one-time firing, cooldown, and re-arm sequences."""

    def setUp(self):
        self.mock_launcher = MagicMock(spec=AppLauncher)
        self.mock_launcher.open_whatsapp.return_value = True
        self.recognizer = WGestureRecognizer(launcher=self.mock_launcher)

    def test_hold_timer_confirms_after_0_7_seconds(self):
        hands = make_w_hands(touching=True)
        t0 = 100.0

        # Frame 1: Detection starts candidate
        fired, status = self.recognizer.process(hands, t0)
        self.assertFalse(fired)
        self.assertEqual(self.recognizer.state, WState.CANDIDATE)
        self.mock_launcher.open_whatsapp.assert_not_called()

        # Frame 2: Halfway through hold (0.35s)
        fired, status = self.recognizer.process(hands, t0 + 0.35)
        self.assertFalse(fired)
        self.assertEqual(self.recognizer.state, WState.CANDIDATE)
        self.assertAlmostEqual(self.recognizer.progress, 0.5, delta=0.05)
        self.mock_launcher.open_whatsapp.assert_not_called()

        # Frame 3: Reached hold duration (0.70s) -> Fires WhatsApp
        fired, status = self.recognizer.process(hands, t0 + W_HOLD_DURATION_S)
        self.assertTrue(fired)
        self.assertEqual(self.recognizer.state, WState.CONFIRMED)
        self.assertEqual(self.recognizer.progress, 1.0)
        self.mock_launcher.open_whatsapp.assert_called_once()

    def test_single_fire_guarantee_while_held(self):
        """Holding the W pose continuously for 5 seconds must ONLY launch WhatsApp once."""
        hands = make_w_hands(touching=True)
        t0 = 100.0

        # Run through to confirmation
        self.recognizer.process(hands, t0)
        fired, _ = self.recognizer.process(hands, t0 + W_HOLD_DURATION_S)
        self.assertTrue(fired)
        self.assertEqual(self.mock_launcher.open_whatsapp.call_count, 1)

        # Keep holding for 4 more seconds
        for offset in [0.8, 1.0, 2.0, 3.0, 4.0]:
            fired_again, status = self.recognizer.process(hands, t0 + offset)
            self.assertFalse(fired_again, f"Fired again at offset {offset}s!")
            self.assertEqual(self.recognizer.state, WState.CONFIRMED)
            self.assertEqual(self.mock_launcher.open_whatsapp.call_count, 1)

    def test_candidate_aborts_on_early_release(self):
        """Releasing before 0.7s resets to IDLE without launching WhatsApp."""
        hands_w = make_w_hands(touching=True)
        hands_empty = HandsResult()
        t0 = 100.0

        # Start candidate
        self.recognizer.process(hands_w, t0)
        self.assertEqual(self.recognizer.state, WState.CANDIDATE)

        # Hold for 0.3s then break pose
        self.recognizer.process(hands_w, t0 + 0.30)
        self.recognizer.process(hands_empty, t0 + 0.31 + W_GRACE_PERIOD_S + 0.05)

        self.assertEqual(self.recognizer.state, WState.IDLE)
        self.mock_launcher.open_whatsapp.assert_not_called()

    def test_rearm_sequence(self):
        """After confirmation, user must release hands for rearm duration before re-triggering."""
        hands_w = make_w_hands(touching=True)
        hands_empty = HandsResult()
        t0 = 100.0

        # Complete first activation
        self.recognizer.process(hands_w, t0)
        self.recognizer.process(hands_w, t0 + W_HOLD_DURATION_S)
        self.assertEqual(self.recognizer.state, WState.CONFIRMED)

        # Release hands
        t_release = t0 + W_HOLD_DURATION_S + 0.1
        self.recognizer.process(hands_empty, t_release)
        self.assertEqual(self.recognizer.state, WState.COOLDOWN)

        # Still in cooldown before W_REARM_DURATION_S
        self.recognizer.process(hands_empty, t_release + W_REARM_DURATION_S * 0.5)
        self.assertEqual(self.recognizer.state, WState.COOLDOWN)

        # Cooldown expires -> back to IDLE
        self.recognizer.process(hands_empty, t_release + W_REARM_DURATION_S + 0.05)
        self.assertEqual(self.recognizer.state, WState.IDLE)

        # Can now activate a second time
        t_second = t_release + W_REARM_DURATION_S + 1.0
        self.recognizer.process(hands_w, t_second)
        self.assertEqual(self.recognizer.state, WState.CANDIDATE)
        self.recognizer.process(hands_w, t_second + W_HOLD_DURATION_S)
        self.assertEqual(self.recognizer.state, WState.CONFIRMED)
        self.assertEqual(self.mock_launcher.open_whatsapp.call_count, 2)


class TestAppLauncherService(unittest.TestCase):
    """Verifies the AppLauncher service executes protocol and shell fallback safely."""

    @patch("os.startfile")
    def test_protocol_launch_success(self, mock_startfile):
        launcher = AppLauncher(whatsapp_uri="whatsapp:")
        res = launcher.open_whatsapp()
        self.assertTrue(res)
        mock_startfile.assert_called_once_with("whatsapp:")

    @patch("os.startfile", side_effect=OSError("Protocol not registered"))
    @patch("subprocess.Popen")
    def test_aumid_fallback_when_protocol_fails(self, mock_popen, mock_startfile):
        launcher = AppLauncher(
            whatsapp_uri="whatsapp:",
            whatsapp_aumid="5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
        )
        res = launcher.open_whatsapp()
        self.assertTrue(res)
        mock_startfile.assert_called_once()
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        self.assertEqual(args[0], "explorer.exe")
        self.assertIn("5319275A.WhatsAppDesktop", args[1])


class TestOrchestratorWIntegration(unittest.TestCase):
    """Verifies GestureOrchestrator integration with WGestureRecognizer."""

    def setUp(self):
        self.actuator = MagicMock(spec=MouseActuator)
        self.desktop = VirtualDesktop(1920, 1080, 1)
        self.trackpad = TrackpadZone(0, 0, 1920, 1080)
        self.orchestrator = GestureOrchestrator(self.actuator, self.desktop, self.trackpad)
        # Mock the launcher inside orchestrator
        self.orchestrator._app_launcher.open_whatsapp = MagicMock(return_value=True)

    def test_orchestrator_initializes_w_gesture(self):
        self.assertIsNotNone(self.orchestrator.w_gesture)
        self.assertEqual(self.orchestrator.w_state, "IDLE")
        self.assertEqual(self.orchestrator.w_status_text, "READY")

    def test_orchestrator_locks_cursor_during_w_candidate(self):
        hands_w = make_w_hands(touching=True)
        self.orchestrator._last_cursor_pos = (500, 500)

        # Process frame into CANDIDATE
        self.orchestrator.process(hands_w)
        self.assertEqual(self.orchestrator.w_state, "CANDIDATE")
        # Mouse should be locked to last known position
        self.actuator.move.assert_called_with(500, 500)

    def test_screenshot_preempts_w_gesture(self):
        """When 10-finger open palm screenshot is active, W gesture is bypassed."""
        # 10 fingers extended
        left_lm = make_hand(wrist_xy=(0.4, 0.7), is_right=False, index_ext=True, middle_ext=True, ring_ext=True, pinky_ext=True)
        right_lm = make_hand(wrist_xy=(0.6, 0.7), is_right=True, index_ext=True, middle_ext=True, ring_ext=True, pinky_ext=True)
        hands_ss = HandsResult(left=left_lm, right=right_lm, left_score=0.95, right_score=0.95)

        self.orchestrator.process(hands_ss)
        self.assertEqual(self.orchestrator._screenshot._state.name, "HOLDING")
    def test_w_pose_matches_reference_photo_geometry(self):
        """Validates that the exact measured coordinates from the reference photo are recognized."""
        # Reference photo measured: avg_pw ~ 0.087, dx ~ 0.059, dy ~ 0.047 (2D dist ~ 0.076 -> gap ~ 0.87)
        left_lm = make_hand(
            wrist_xy=(0.42, 0.70),
            is_right=False,
            index_ext=True,
            middle_ext=False,
            ring_ext=False,
            pinky_ext=True,
            index_tip_xy=(0.455, 0.465),
            pinky_tip_xy=(0.330, 0.440),
            palm_facing=True,
        )
        right_lm = make_hand(
            wrist_xy=(0.58, 0.70),
            is_right=True,
            index_ext=True,
            middle_ext=False,
            ring_ext=False,
            pinky_ext=True,
            index_tip_xy=(0.514, 0.418),
            pinky_tip_xy=(0.670, 0.440),
            palm_facing=True,
        )
        hands = HandsResult(left=left_lm, right=right_lm, left_score=0.98, right_score=0.97)
        valid, reason = check_w_pose(hands)
        self.assertTrue(valid, f"Expected reference photo geometry to be valid, got: {reason}")


class TestDynamicIslandIntegration(unittest.TestCase):
    """Verifies that WhatsApp feedback uses the existing Dynamic Island capsule."""

    @patch("ctypes.windll.user32.GetParent")
    @patch("tkinter.Tk")
    def test_show_app_toast_opening_whatsapp(self, mock_tk, mock_get_parent):
        from core.overlay import ScreenshotOverlay
        # Mock Tk instance
        mock_root = MagicMock()
        mock_root.winfo_screenwidth.return_value = 1920
        mock_root.winfo_screenheight.return_value = 1080
        mock_tk.return_value = mock_root

        overlay = ScreenshotOverlay("TOP_CENTER")
        # Ensure initial capsule dimensions remain compact
        self.assertEqual(overlay.w, 220)
        self.assertEqual(overlay.h, 44)

        # Trigger app toast
        overlay.show_app_toast("WhatsApp", success=True)
        self.assertEqual(overlay._island_state, "SUCCESS")
        self.assertEqual(overlay._island_text, "Opening WhatsApp")
        self.assertEqual(overlay._progress, 1.0)
        mock_root.deiconify.assert_called()

    @patch("ctypes.windll.user32.GetParent")
    @patch("tkinter.Tk")
    def test_show_app_toast_error_graceful(self, mock_tk, mock_get_parent):
        from core.overlay import ScreenshotOverlay
        mock_root = MagicMock()
        mock_root.winfo_screenwidth.return_value = 1920
        mock_root.winfo_screenheight.return_value = 1080
        mock_tk.return_value = mock_root

        overlay = ScreenshotOverlay("TOP_CENTER")
        overlay.show_app_toast("WhatsApp", success=False)
        self.assertEqual(overlay._island_state, "ERROR")
        self.assertEqual(overlay._island_text, "WhatsApp not found")


if __name__ == "__main__":
    unittest.main()

