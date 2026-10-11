# test_phase7_hud.py
"""
Unit tests for Air Point Phase 7: Dynamic Island Gesture Feedback HUD & Runtime Status.
Tests:
  - Correct visual feedback (icons, text, colors) for all actions:
    * Volume Up / Down
    * Audio Mute / Unmute
    * Screenshot Saved / Failed
    * WhatsApp Launch Initiated / Failed
    * Hand Lost / Hand Reacquired
    * System Paused / Resumed
  - Duplicate notification suppression for sustained or rapid gestures
  - Priority arbitration protecting critical action toasts from being overwritten by lower priority events
  - Honest failure reporting
  - Automatic expiration and clean retraction (never stuck on screen)
  - Runtime status card inspection
  - Clean resource destruction on shutdown
"""

import unittest
from unittest.mock import MagicMock, patch
import time

from core.overlay import (
    ScreenshotOverlay,
    PRIORITY_CRITICAL,
    PRIORITY_VOLUME,
    PRIORITY_TRACKING,
    PRIORITY_STATUS,
)


class TestPhase7DynamicIslandHUD(unittest.TestCase):
    """Verifies all gesture feedback toasts, priority handling, and runtime status."""

    def setUp(self):
        self.mock_tk_patcher = patch("tkinter.Tk")
        self.mock_get_parent_patcher = patch("ctypes.windll.user32.GetParent")
        
        self.mock_tk = self.mock_tk_patcher.start()
        self.mock_get_parent = self.mock_get_parent_patcher.start()

        self.mock_root = MagicMock()
        self.mock_root.winfo_screenwidth.return_value = 1920
        self.mock_root.winfo_screenheight.return_value = 1080
        self.mock_tk.return_value = self.mock_root

        self.overlay = ScreenshotOverlay("TOP_CENTER")

    def tearDown(self):
        self.mock_tk_patcher.stop()
        self.mock_get_parent_patcher.stop()

    def test_initial_dimensions_and_state(self):
        """Verifies HUD starts in clean IDLE state with standard compact capsule size."""
        self.assertEqual(self.overlay.w, 220)
        self.assertEqual(self.overlay.h, 44)
        self.assertEqual(self.overlay._island_state, "IDLE")
        self.assertTrue(self.overlay._is_hidden)

    def test_volume_up_feedback(self):
        """Verifies Volume ↑ feedback text, icon, and progress."""
        self.overlay.show_ear_volume("VOLUME_UP", 0.70, is_active=True)
        self.assertEqual(self.overlay._island_state, "HOLDING")
        self.assertEqual(self.overlay._island_text, "Volume ↑  70%")
        self.assertEqual(self.overlay._island_icon, "VOL_UP")
        self.assertEqual(self.overlay._island_color, "#00e5ff")
        self.assertAlmostEqual(self.overlay._progress, 0.70)
        self.mock_root.deiconify.assert_called()

    def test_volume_down_feedback(self):
        """Verifies Volume ↓ feedback text, icon, and progress."""
        self.overlay.show_ear_volume("VOLUME_DOWN", 0.45, is_active=True)
        self.assertEqual(self.overlay._island_state, "HOLDING")
        self.assertEqual(self.overlay._island_text, "Volume ↓  45%")
        self.assertEqual(self.overlay._island_icon, "VOL_DOWN")
        self.assertEqual(self.overlay._island_color, "#00e5ff")
        self.assertAlmostEqual(self.overlay._progress, 0.45)

    def test_volume_release_dwell_and_retraction(self):
        """Verifies volume release holds briefly in SUCCESS state before retracting."""
        self.overlay.show_ear_volume("VOLUME_UP", 0.80, is_active=True)
        self.assertEqual(self.overlay._island_state, "HOLDING")

        # Contact released
        self.overlay.show_ear_volume("VOLUME_UP", 0.80, is_active=False)
        self.assertEqual(self.overlay._island_state, "SUCCESS")
        self.assertGreater(self.overlay._toast_until, time.perf_counter())

    def test_audio_muted_feedback(self):
        """Verifies Muted toast shows amber capsule with mute icon."""
        self.overlay.show_audio_toast("MUTED", success=True)
        self.assertEqual(self.overlay._island_state, "SUCCESS")
        self.assertEqual(self.overlay._island_text, "Muted")
        self.assertEqual(self.overlay._island_icon, "MUTE")
        self.assertEqual(self.overlay._island_color, "#ff9900")

    def test_audio_unmuted_feedback(self):
        """Verifies Unmuted toast shows emerald capsule with unmute icon."""
        self.overlay.show_audio_toast("UNMUTED", success=True)
        self.assertEqual(self.overlay._island_state, "SUCCESS")
        self.assertEqual(self.overlay._island_text, "Unmuted")
        self.assertEqual(self.overlay._island_icon, "UNMUTE")
        self.assertEqual(self.overlay._island_color, "#00ff66")

    def test_screenshot_saved_feedback(self):
        """Verifies Screenshot saved toast shows emerald capsule with checkmark."""
        self.overlay.show_success_toast("test_shot.png")
        self.assertEqual(self.overlay._island_state, "SUCCESS")
        self.assertEqual(self.overlay._island_text, "Screenshot saved")
        self.assertEqual(self.overlay._island_icon, "CHECK")
        self.assertEqual(self.overlay._island_color, "#00ff66")

    def test_screenshot_failed_honest_reporting(self):
        """Verifies failed screenshot honestly reports Screenshot failed with red icon."""
        self.overlay.show_error_toast()
        self.assertEqual(self.overlay._island_state, "ERROR")
        self.assertEqual(self.overlay._island_text, "Screenshot failed")
        self.assertEqual(self.overlay._island_icon, "CROSS")
        self.assertEqual(self.overlay._island_color, "#ff4444")

    def test_whatsapp_launch_feedback(self):
        """Verifies WhatsApp launch displays brand green capsule and APP icon."""
        self.overlay.show_app_toast("WhatsApp", success=True)
        self.assertEqual(self.overlay._island_state, "SUCCESS")
        self.assertEqual(self.overlay._island_text, "Opening WhatsApp")
        self.assertEqual(self.overlay._island_icon, "APP")
        self.assertEqual(self.overlay._island_color, "#25D366")

    def test_whatsapp_failure_honest_reporting(self):
        """Verifies WhatsApp launch failure reports WhatsApp not found with error styling."""
        self.overlay.show_app_toast("WhatsApp", success=False)
        self.assertEqual(self.overlay._island_state, "ERROR")
        self.assertEqual(self.overlay._island_text, "WhatsApp not found")
        self.assertEqual(self.overlay._island_icon, "CROSS")
        self.assertEqual(self.overlay._island_color, "#ff4444")

    def test_hand_lost_and_reacquired_feedback(self):
        """Verifies tracking state transitions show Hand lost and Hand reacquired."""
        # Hand lost
        self.overlay.show_tracking_toast("LOST")
        self.assertEqual(self.overlay._island_state, "SUCCESS")
        self.assertEqual(self.overlay._island_text, "Hand lost")
        self.assertEqual(self.overlay._island_icon, "HAND_LOST")
        self.assertEqual(self.overlay._island_color, "#ffaa00")

        # Advance time to allow reacquisition
        t_future = time.perf_counter() + 2.0
        with patch("time.perf_counter", return_value=t_future):
            self.overlay.show_tracking_toast("REACQUIRED")
            self.assertEqual(self.overlay._island_state, "SUCCESS")
            self.assertEqual(self.overlay._island_text, "Hand reacquired")
            self.assertEqual(self.overlay._island_icon, "HAND_FOUND")
            self.assertEqual(self.overlay._island_color, "#00e5ff")

    def test_pause_and_resume_feedback(self):
        """Verifies pause and resume feedback show System Paused and System Resumed."""
        self.overlay.show_pause_toast(is_paused=True)
        self.assertEqual(self.overlay._island_text, "System Paused")
        self.assertEqual(self.overlay._island_icon, "PAUSE")
        self.assertEqual(self.overlay._island_color, "#ffaa00")

        t_future = time.perf_counter() + 2.0
        with patch("time.perf_counter", return_value=t_future):
            self.overlay.show_pause_toast(is_paused=False)
            self.assertEqual(self.overlay._island_text, "System Resumed")
            self.assertEqual(self.overlay._island_icon, "PLAY")
            self.assertEqual(self.overlay._island_color, "#00ff66")

    def test_duplicate_notification_suppression(self):
        """Verifies rapid duplicate triggers within 0.8s are ignored to prevent flickering."""
        now = 100.0
        with patch("time.perf_counter", return_value=now):
            self.overlay.show_app_toast("WhatsApp", success=True)
            self.assertEqual(self.overlay._island_text, "Opening WhatsApp")
            first_until = self.overlay._toast_until

        # Rapid identical call 0.2s later
        with patch("time.perf_counter", return_value=now + 0.20):
            self.overlay.show_app_toast("WhatsApp", success=True)
            # Expiration should remain from first call, not reset
            self.assertEqual(self.overlay._toast_until, first_until)

    def test_priority_arbitration_protects_critical_toasts(self):
        """Verifies high-priority action toasts (e.g. Screenshot saved) are not overwritten by low-priority tracking toasts."""
        now = 100.0
        with patch("time.perf_counter", return_value=now):
            # Critical event (Priority 40)
            self.overlay.show_success_toast()
            self.assertEqual(self.overlay._island_text, "Screenshot saved")
            self.assertEqual(self.overlay._current_priority, PRIORITY_CRITICAL)

            # Lower-priority tracking event (Priority 20) during active window
            self.overlay.show_tracking_toast("LOST")
            # Must remain Screenshot saved!
            self.assertEqual(self.overlay._island_text, "Screenshot saved")
            self.assertEqual(self.overlay._current_priority, PRIORITY_CRITICAL)

    def test_temporary_messages_expire_and_retract(self):
        """Verifies toasts smoothly retract and withdraw after expiration."""
        now = 100.0
        with patch("time.perf_counter", return_value=now):
            self.overlay.show_success_toast()
            self.assertEqual(self.overlay._island_state, "SUCCESS")
            # Toast is positioned at visible Y on screen
            self.overlay.current_y = self.overlay.y_visible

        # Move past toast duration
        now_expired = now + 5.0
        with patch("time.perf_counter", side_effect=[now_expired, now_expired + 0.1, now_expired + 0.2]):
            # Call update_state
            self.overlay.update_state()
            self.assertEqual(self.overlay._island_state, "RETRACTING")

            # Simulate full retraction movement
            self.overlay.current_y = self.overlay.y_hidden
            self.overlay.update_state()
            self.assertEqual(self.overlay._island_state, "IDLE")
            self.assertTrue(self.overlay._is_hidden)
            self.mock_root.withdraw.assert_called()

    def test_runtime_status_inspection(self):
        """Verifies runtime status card expands dimensions and formats telemetry."""
        status_info = {
            "fps": 31.4,
            "owner": "RIGHT",
            "tracking": "Both Hands",
            "paused": False,
        }
        self.overlay.show_runtime_status(status_info, duration=3.0)
        self.assertEqual(self.overlay._island_state, "STATUS")
        self.assertIn("31 FPS", self.overlay._island_text)
        self.assertIn("ACTIVE", self.overlay._island_text)
        self.assertIn("RIGHT", self.overlay._island_subtext)
        self.assertIn("Both Hands", self.overlay._island_subtext)
        self.assertEqual(self.overlay.w, 320)
        self.assertEqual(self.overlay.h, 52)

    def test_clean_hud_shutdown(self):
        """Verifies close() destroys root window cleanly."""
        self.overlay.close()
        self.mock_root.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
