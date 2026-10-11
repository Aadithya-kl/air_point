# test_phase7_1_volume_visualizer.py
"""
Unit tests for Air Point Phase 7.1: Dynamic Island Volume Visualizer.
Tests:
  - Volume increase opens the island.
  - Volume decrease opens the island.
  - Displayed percentage follows the actual audio-controller volume scalar [0-100%].
  - Bar heights correspond to the volume percentage (low, medium, high scaling).
  - Muted state displays the appropriate muted icon and inactive bars.
  - Repeated updates reuse the same island without flicker or recreation.
  - Ending the gesture dismisses the island after a short delay.
  - Existing screenshot, mute, WhatsApp, and reacquisition HUD events still work.
  - Animation and timers clean up correctly on shutdown.
  - AudioController.is_muted property correctly reports mute state.
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
from core.audio_controller import AudioController


class TestPhase71VolumeVisualizer(unittest.TestCase):
    """Verifies Phase 7.1 Dynamic Island volume visualizer requirements."""

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

    def test_volume_increase_opens_island(self):
        """Verifies right-ear volume increase opens the Dynamic Island with '+' indicator."""
        self.overlay.show_ear_volume("VOLUME_UP", 0.65, is_active=True)
        self.assertEqual(self.overlay._island_state, "HOLDING")
        self.assertEqual(self.overlay._island_mode, "VOLUME")
        self.assertEqual(self.overlay.volume_direction, "VOLUME_UP")
        self.assertEqual(self.overlay.volume_pct, 65)
        self.assertEqual(self.overlay._island_color, "#00e5ff")
        self.assertEqual(self.overlay._island_icon, "VOL_UP")
        self.assertAlmostEqual(self.overlay._progress, 0.65)
        self.mock_root.deiconify.assert_called()
        self.mock_root.lift.assert_called()

    def test_volume_decrease_opens_island(self):
        """Verifies left-ear volume decrease opens the Dynamic Island with '−' indicator."""
        self.overlay.show_ear_volume("VOLUME_DOWN", 0.42, is_active=True)
        self.assertEqual(self.overlay._island_state, "HOLDING")
        self.assertEqual(self.overlay._island_mode, "VOLUME")
        self.assertEqual(self.overlay.volume_direction, "VOLUME_DOWN")
        self.assertEqual(self.overlay.volume_pct, 42)
        self.assertEqual(self.overlay._island_color, "#00e5ff")
        self.assertEqual(self.overlay._island_icon, "VOL_DOWN")
        self.assertAlmostEqual(self.overlay._progress, 0.42)
        self.mock_root.deiconify.assert_called()
        self.mock_root.lift.assert_called()

    def test_displayed_percentage_follows_audio_controller_volume(self):
        """Verifies volume percentage accurately reflects actual audio scalar [0-100%]."""
        test_cases = [
            (0.00, 0, "0%"),
            (0.18, 18, "18%"),
            (0.50, 50, "50%"),
            (0.68, 68, "68%"),
            (1.00, 100, "100%"),
            (-0.10, 0, "0%"),    # Clamping below 0
            (1.20, 100, "100%"),  # Clamping above 1
        ]
        for scalar, expected_pct, expected_str in test_cases:
            self.overlay.show_ear_volume("VOLUME_UP", scalar, is_active=True)
            self.assertEqual(self.overlay.volume_pct, expected_pct)
            self.assertEqual(self.overlay.displayed_volume_pct, expected_str)

    def test_sound_bar_heights_correspond_to_volume_percentage(self):
        """Verifies vertical bar heights strictly scale with the actual volume percentage."""
        # Low volume (15%)
        bars_low = self.overlay.get_sound_bar_heights(0.15, is_muted=False, is_active=False)
        # Medium volume (50%)
        bars_med = self.overlay.get_sound_bar_heights(0.50, is_muted=False, is_active=False)
        # High volume (90%)
        bars_high = self.overlay.get_sound_bar_heights(0.90, is_muted=False, is_active=False)
        # Max volume (100%)
        bars_max = self.overlay.get_sound_bar_heights(1.00, is_muted=False, is_active=False)

        self.assertEqual(len(bars_low), 7)
        self.assertEqual(len(bars_med), 7)
        self.assertEqual(len(bars_high), 7)

        # Monotonic height scaling: every bar grows as volume rises
        for i in range(7):
            self.assertLess(bars_low[i], bars_med[i])
            self.assertLess(bars_med[i], bars_high[i])
            self.assertLessEqual(bars_high[i], bars_max[i])

        # Center bar (index 3) is peak of symmetric wave profile
        self.assertEqual(bars_max[3], 18.0)
        self.assertGreater(bars_med[3], bars_med[0])
        self.assertAlmostEqual(bars_med[0], bars_med[6])  # Symmetry

    def test_muted_state_displays_muted_icon_and_inactive_bars(self):
        """Verifies muted state displays the muted speaker icon and flat/inactive bars."""
        self.overlay.show_ear_volume("VOLUME_DOWN", 0.40, is_active=True, is_muted=True)
        self.assertTrue(self.overlay.is_volume_muted)
        self.assertEqual(self.overlay._island_icon, "MUTE")
        self.assertEqual(self.overlay._island_color, "#ffaa00")

        # Inactive bars: all bars flat (2.0px)
        bars_muted = self.overlay.get_sound_bar_heights(0.40, is_muted=True, is_active=True)
        self.assertEqual(bars_muted, [2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0])

    def test_repeated_updates_reuse_same_island_without_flicker(self):
        """Verifies repeated updates while active reuse the same HUD in place without re-creating."""
        # Initial activation
        self.overlay.show_ear_volume("VOLUME_UP", 0.50, is_active=True)
        self.assertEqual(self.overlay.volume_pct, 50)
        self.assertEqual(self.overlay._island_state, "HOLDING")

        # Second continuous update (e.g. volume increases to 54%)
        self.overlay.show_ear_volume("VOLUME_UP", 0.54, is_active=True)
        self.assertEqual(self.overlay.volume_pct, 54)
        self.assertEqual(self.overlay._island_state, "HOLDING")
        self.assertFalse(self.overlay._is_hidden)

        # Third continuous update (e.g. volume increases to 58%)
        self.overlay.show_ear_volume("VOLUME_UP", 0.58, is_active=True)
        self.assertEqual(self.overlay.volume_pct, 58)
        self.assertEqual(self.overlay._island_state, "HOLDING")

    def test_ending_gesture_dismisses_island_after_short_delay(self):
        """Verifies ending the gesture keeps the island briefly visible, then retracts smoothly."""
        t0 = 100.0
        with patch("time.perf_counter", return_value=t0):
            self.overlay.show_ear_volume("VOLUME_UP", 0.70, is_active=True)
            self.overlay.current_y = self.overlay.y_visible
            self.assertEqual(self.overlay._island_state, "HOLDING")

            # Gesture released
            self.overlay.show_ear_volume("VOLUME_UP", 0.70, is_active=False)
            self.assertEqual(self.overlay._island_state, "SUCCESS")
            # Remains visible until dwell completes (~0.8s)
            self.assertGreater(self.overlay._toast_until, t0)

        # Before dwell expires (at t0 + 0.4s) -> remains in SUCCESS
        with patch("time.perf_counter", return_value=t0 + 0.4):
            self.overlay.update_state()
            self.assertEqual(self.overlay._island_state, "SUCCESS")

        # After dwell expires (at t0 + 1.2s) -> enters RETRACTING
        with patch("time.perf_counter", side_effect=[t0 + 1.2, t0 + 1.3, t0 + 1.4]):
            self.overlay.update_state()
            self.assertEqual(self.overlay._island_state, "RETRACTING")

            # Simulate completing retraction upward
            self.overlay.current_y = self.overlay.y_hidden
            self.overlay.update_state()
            self.assertEqual(self.overlay._island_state, "IDLE")
            self.assertTrue(self.overlay._is_hidden)
            self.mock_root.withdraw.assert_called()

    def test_existing_hud_events_still_work(self):
        """Verifies all Phase 1-7 toasts (screenshot, app launch, mute/unmute, tracking) remain functional."""
        def reset_toast():
            self.overlay._island_state = "IDLE"
            self.overlay._current_priority = 0
            self.overlay._toast_until = 0.0

        # Screenshot saved
        self.overlay.show_success_toast()
        self.assertEqual(self.overlay._island_state, "SUCCESS")
        self.assertEqual(self.overlay._island_text, "Screenshot saved")
        self.assertEqual(self.overlay._island_icon, "CHECK")
        self.assertEqual(self.overlay._island_mode, "")

        # WhatsApp launch
        reset_toast()
        self.overlay.show_app_toast("WhatsApp", success=True)
        self.assertEqual(self.overlay._island_text, "Opening WhatsApp")
        self.assertEqual(self.overlay._island_icon, "APP")

        # Lip mute / unmute
        reset_toast()
        self.overlay.show_audio_toast("MUTED")
        self.assertEqual(self.overlay._island_text, "Muted")
        self.assertEqual(self.overlay._island_icon, "MUTE")

        reset_toast()
        self.overlay.show_audio_toast("UNMUTED")
        self.assertEqual(self.overlay._island_text, "Unmuted")
        self.assertEqual(self.overlay._island_icon, "UNMUTE")

        # Hand lost and reacquired
        reset_toast()
        self.overlay.show_tracking_toast("LOST")
        self.assertEqual(self.overlay._island_text, "Hand lost")
        self.assertEqual(self.overlay._island_icon, "HAND_LOST")

        reset_toast()
        self.overlay.show_tracking_toast("REACQUIRED")
        self.assertEqual(self.overlay._island_text, "Hand reacquired")
        self.assertEqual(self.overlay._island_icon, "HAND_FOUND")

    def test_clean_resource_destruction_on_shutdown(self):
        """Verifies overlay.close() destroys the Tkinter root without errors."""
        self.overlay.close()
        self.mock_root.destroy.assert_called_once()

    def test_audio_controller_is_muted_property(self):
        """Verifies AudioController.is_muted returns accurate endpoint mute state."""
        mock_endpoint = MagicMock()
        mock_endpoint.GetMute.return_value = 1
        mock_endpoint.GetMasterVolumeLevelScalar.return_value = 0.50

        audio = AudioController(endpoint_override=mock_endpoint)
        self.assertTrue(audio.is_muted)

        mock_endpoint.GetMute.return_value = 0
        self.assertFalse(audio.is_muted)


if __name__ == "__main__":
    unittest.main()
