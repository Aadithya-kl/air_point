import unittest
import math
from core.tracker import Landmark, HandsResult
from core.gestures.screenshot import ScreenshotController, ScreenshotState
from core.overlay import get_capsule_path
import config

class MockActuator:
    def __init__(self):
        self.screenshots = 0
        self.last_path = None

    def take_screenshot(self, directory, pre_cb=None, post_cb=None):
        self.screenshots += 1
        self.last_path = f"{directory}/test.png"
        return self.last_path


class TestPhase3ScreenshotTimer(unittest.TestCase):
    def setUp(self):
        config.SCREENSHOT_ENABLED = True
        config.SCREENSHOT_HOLD_DURATION_S = 3.0
        config.SCREENSHOT_GRACE_PERIOD_S = 0.10
        config.SCREENSHOT_HAND_CONFIDENCE_MIN = 0.85
        config.SCREENSHOT_REARM_DURATION_S = 0.50

        self.actuator = MockActuator()
        self.ss = ScreenshotController(self.actuator)

    def _make_valid_two_hands(self):
        """Creates two valid open-palm hands facing camera with high confidence."""
        res = HandsResult()
        
        # Left Hand
        l_lms = [Landmark(x=0.5, y=0.5, z=0.0) for _ in range(21)]
        l_lms[0] = Landmark(x=0.5, y=1.0, z=0.0)
        for finger_idx in [8, 12, 16, 20]:
            l_lms[finger_idx - 2].y = 0.6
            l_lms[finger_idx].y = 0.2
        l_lms[2].y = 0.6
        l_lms[4].y = 0.2
        # Left palm facing camera: thumb (4) x > pinky (20) x
        l_lms[4].x = 0.8
        l_lms[20].x = 0.2
        res.left = l_lms
        res.left_score = 0.95

        # Right Hand
        r_lms = [Landmark(x=0.5, y=0.5, z=0.0) for _ in range(21)]
        r_lms[0] = Landmark(x=0.5, y=1.0, z=0.0)
        for finger_idx in [8, 12, 16, 20]:
            r_lms[finger_idx - 2].y = 0.6
            r_lms[finger_idx].y = 0.2
        r_lms[2].y = 0.6
        r_lms[4].y = 0.2
        # Right palm facing camera: thumb (4) x < pinky (20) x
        r_lms[4].x = 0.2
        r_lms[20].x = 0.8
        res.right = r_lms
        res.right_score = 0.95

        return res

    def test_newly_valid_hold_begins_at_3_seconds_and_0_progress(self):
        """1 & 2: A newly valid hold begins at 3.0s remaining with 0.0 progress."""
        hands = self._make_valid_two_hands()
        t0 = 100.0
        self.ss.process(hands, t0)

        self.assertEqual(self.ss._state, ScreenshotState.HOLDING)
        self.assertAlmostEqual(self.ss.remaining, 3.0, places=2)
        self.assertAlmostEqual(self.ss.progress, 0.0, places=2)
        self.assertAlmostEqual(self.ss.elapsed, 0.0, places=2)
        self.assertIn("3.0s", self.ss.hud_text)
        self.assertNotIn("-3", self.ss.hud_text)
        self.assertNotIn("- 3", self.ss.hud_text)

    def test_progress_after_one_second(self):
        """3: After one second, 2.0s remain and progress is 1/3 (0.333)."""
        hands = self._make_valid_two_hands()
        self.ss.process(hands, 100.0)
        self.ss.process(hands, 101.0)

        self.assertEqual(self.ss._state, ScreenshotState.HOLDING)
        self.assertAlmostEqual(self.ss.remaining, 2.0, places=2)
        self.assertAlmostEqual(self.ss.progress, 1.0 / 3.0, places=2)
        self.assertAlmostEqual(self.ss.elapsed, 1.0, places=2)
        self.assertIn("2.0s", self.ss.hud_text)

    def test_progress_after_two_seconds(self):
        """4: After two seconds, 1.0s remains and progress is 2/3 (0.667)."""
        hands = self._make_valid_two_hands()
        self.ss.process(hands, 100.0)
        self.ss.process(hands, 102.0)

        self.assertEqual(self.ss._state, ScreenshotState.HOLDING)
        self.assertAlmostEqual(self.ss.remaining, 1.0, places=2)
        self.assertAlmostEqual(self.ss.progress, 2.0 / 3.0, places=2)
        self.assertAlmostEqual(self.ss.elapsed, 2.0, places=2)
        self.assertIn("1.0s", self.ss.hud_text)

    def test_completion_at_three_seconds(self):
        """5: At three seconds, remaining is 0, progress is 1.0, and screenshot captures."""
        hands = self._make_valid_two_hands()
        self.ss.process(hands, 100.0)
        self.ss.process(hands, 103.0)

        self.assertEqual(self.ss._state, ScreenshotState.CAPTURED)
        self.assertAlmostEqual(self.ss.remaining, 0.0, places=2)
        self.assertAlmostEqual(self.ss.progress, 1.0, places=2)
        self.assertEqual(self.actuator.screenshots, 1)

    def test_remaining_never_negative_and_progress_never_exceeds_one(self):
        """6 & 7: Remaining never becomes negative and progress never exceeds [0.0, 1.0]."""
        hands = self._make_valid_two_hands()
        self.ss.process(hands, 100.0)
        self.ss.process(hands, 105.0)  # 5 seconds elapsed

        self.assertGreaterEqual(self.ss.remaining, 0.0)
        self.assertLessEqual(self.ss.progress, 1.0)
        self.assertGreaterEqual(self.ss.progress, 0.0)

    def test_holding_does_not_reset_start_timestamp(self):
        """8: Holding the gesture continuously preserves start timestamp."""
        hands = self._make_valid_two_hands()
        self.ss.process(hands, 100.0)
        start_ts = self.ss._hold_start_time

        for t in [100.2, 100.5, 100.8, 101.2, 101.5]:
            self.ss.process(hands, t)
            self.assertEqual(self.ss._hold_start_time, start_ts, "Start timestamp must not reset on frames")

    def test_interruption_resets_timer(self):
        """9: Interruption past grace period resets timer to 3.0s and 0 progress."""
        hands = self._make_valid_two_hands()
        self.ss.process(hands, 100.0)
        self.ss.process(hands, 101.5)  # 1.5s held

        # Drop right hand
        invalid_hands = HandsResult()
        invalid_hands.left = hands.left
        invalid_hands.left_score = 0.95
        invalid_hands.right = None

        self.ss.process(invalid_hands, 101.55)  # within grace period (0.05s)
        self.assertEqual(self.ss._state, ScreenshotState.HOLDING)

        self.ss.process(invalid_hands, 101.75)  # past grace period (0.20s > 0.10s)
        self.assertEqual(self.ss._state, ScreenshotState.READY)
        self.assertEqual(self.ss.progress, 0.0)
        self.assertEqual(self.ss.remaining, 3.0)

        # Re-starting hold starts clean from 3.0s
        self.ss.process(hands, 105.0)
        self.assertEqual(self.ss._state, ScreenshotState.HOLDING)
        self.assertEqual(self.ss.progress, 0.0)
        self.assertEqual(self.ss.remaining, 3.0)
        self.assertEqual(self.ss._hold_start_time, 105.0)

    def test_single_capture_no_retrigger_until_rearmed(self):
        """10: One successful hold saves exactly 1 screenshot and requires release to rearm."""
        hands = self._make_valid_two_hands()
        self.ss.process(hands, 100.0)
        self.ss.process(hands, 103.1)
        self.assertEqual(self.actuator.screenshots, 1)

        # Still holding hands
        self.ss.process(hands, 104.0)
        self.ss.process(hands, 105.0)
        self.assertEqual(self.actuator.screenshots, 1, "Must never retrigger while held")

        # Release both hands
        empty_hands = HandsResult()
        self.ss.process(empty_hands, 106.0)
        self.ss.process(empty_hands, 106.6)  # past 0.5s rearm
        self.assertEqual(self.ss._state, ScreenshotState.READY)

        # Second hold now triggers second capture
        self.ss.process(hands, 107.0)
        self.ss.process(hands, 110.1)
        self.assertEqual(self.actuator.screenshots, 2)

    def test_capsule_path_empty_at_zero_and_full_at_one(self):
        """Progress outline is empty at 0.0 and closed at 1.0."""
        # Progress 0.0 -> no segments drawn
        segs_0 = get_capsule_path(220, 44, 0.0)
        self.assertEqual(len(segs_0), 0)

        # Progress 0.5 -> half capsule
        segs_half = get_capsule_path(220, 44, 0.5)
        self.assertGreater(len(segs_half), 0)

        # Progress 1.0 -> 5 segments (top-right line, right arc, bottom line, left arc, top-left line)
        segs_full = get_capsule_path(220, 44, 1.0)
        self.assertEqual(len(segs_full), 5)


if __name__ == "__main__":
    unittest.main()
