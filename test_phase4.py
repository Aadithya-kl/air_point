import unittest
import time
from core.gestures.orchestrator import GestureOrchestrator
from core.gestures.screenshot import ScreenshotState
from core.tracker import Landmark, HandsResult
from core.display import VirtualDesktop, TrackpadZone
import config

class MockActuator:
    def __init__(self):
        self.screenshots = 0
        self.moves = 0
        self.fail_screenshot = False

    def move(self, x, y):
        self.moves += 1

    def take_screenshot(self, directory, pre_cb=None, post_cb=None):
        if self.fail_screenshot:
            return None
        self.screenshots += 1
        return f"{directory}/test_screenshot.png"


class TestPhase4(unittest.TestCase):
    def setUp(self):
        config.POINTER_ONLY_MODE = False
        config.PINCH_CLICK_ENABLED = True
        config.SCREENSHOT_ENABLED = True
        config.SCREENSHOT_HOLD_DURATION_S = 3.0
        config.SCREENSHOT_GRACE_PERIOD_S = 0.10
        config.SCREENSHOT_HAND_CONFIDENCE_MIN = 0.85
        config.SCREENSHOT_REARM_DURATION_S = 0.50
        
        self.actuator = MockActuator()
        self.processor = GestureOrchestrator(self.actuator, VirtualDesktop(1920, 1080, 1), TrackpadZone(0, 640, 0, 480))
        self.ss = self.processor._screenshot
        
        # Override process internally to mock time manually without changing all tests
        self.original_process = self.processor.process
        self.mock_time = 0.0

    def process(self, hands_result):
        # We manually call _screenshot.process using our mock time instead of time.time()
        # Since orchestrator.process uses time.time(), we'll just mock time.time in the test context if possible, 
        # but the simplest is just testing the ScreenshotController directly.
        pass

    def make_open_palm(self, right=True, facing_camera=True, curl_one=False):
        lms = [Landmark(0, 0.5, 0) for _ in range(21)]
        lms[0] = Landmark(0.5, 1.0, 0)
        
        for finger_idx in [8, 12, 16, 20]:
            lms[finger_idx - 2].y = 0.6
            lms[finger_idx].y = 0.2
            
        lms[2].y = 0.6
        lms[4].y = 0.2
        
        if curl_one:
            lms[8].y = 0.8  # curl index
            
        if right:
            if facing_camera:
                lms[4].x = 0.2
                lms[20].x = 0.8
            else:
                lms[4].x = 0.8
                lms[20].x = 0.2
        else: # left hand
            if facing_camera:
                lms[4].x = 0.8
                lms[20].x = 0.2
            else:
                lms[4].x = 0.2
                lms[20].x = 0.8
                
        return lms

    def make_hands(self, left_valid=True, right_valid=True, left_conf=0.9, right_conf=0.9, curl_left=False, curl_right=False):
        res = HandsResult()
        if left_valid:
            res.left = self.make_open_palm(right=False, curl_one=curl_left)
            res.left_score = left_conf
        if right_valid:
            res.right = self.make_open_palm(right=True, curl_one=curl_right)
            res.right_score = right_conf
        return res

    def test_both_palms_start_countdown(self):
        self.ss.process(self.make_hands(), 0.0)
        self.assertEqual(self.ss._state, ScreenshotState.HOLDING)

    def test_single_palm_never_starts(self):
        self.ss.process(self.make_hands(left_valid=False), 0.0)
        self.assertEqual(self.ss._state, ScreenshotState.READY)
        
        self.ss.process(self.make_hands(right_valid=False), 0.0)
        self.assertEqual(self.ss._state, ScreenshotState.READY)

    def test_curled_finger_rejected(self):
        self.ss.process(self.make_hands(curl_left=True), 0.0)
        self.assertEqual(self.ss._state, ScreenshotState.READY)

    def test_invalid_palm_orientation_rejected(self):
        res = self.make_hands()
        # Flip right palm
        res.right[4].x = 0.8; res.right[20].x = 0.2
        self.ss.process(res, 0.0)
        self.assertEqual(self.ss._state, ScreenshotState.READY)

    def test_low_confidence_rejected(self):
        self.ss.process(self.make_hands(left_conf=0.5), 0.0)
        self.assertEqual(self.ss._state, ScreenshotState.READY)

    def test_capture_after_3_seconds(self):
        self.ss.process(self.make_hands(), 0.0)
        self.ss.process(self.make_hands(), 3.1)
        self.assertEqual(self.ss._state, ScreenshotState.CAPTURED)
        self.assertEqual(self.actuator.screenshots, 1)

    def test_no_repeated_capture(self):
        self.ss.process(self.make_hands(), 0.0)
        self.ss.process(self.make_hands(), 3.1)
        self.ss.process(self.make_hands(), 30.0)
        self.assertEqual(self.ss._state, ScreenshotState.CAPTURED)
        self.assertEqual(self.actuator.screenshots, 1)

    def test_grace_period_cancellation(self):
        self.ss.process(self.make_hands(), 0.0)
        self.ss.process(self.make_hands(), 0.95) # Still holding at 0.95
        self.ss.process(self.make_hands(left_valid=False), 1.0) # lose left hand (last seen 0.95)
        self.assertEqual(self.ss._state, ScreenshotState.HOLDING) # inside grace period (0.05s)
        self.ss.process(self.make_hands(left_valid=False), 1.1) # past grace period (0.15s)
        self.assertEqual(self.ss._state, ScreenshotState.READY)
        self.assertEqual(self.actuator.screenshots, 0)

    def test_rearm_requires_both_hands_released(self):
        self.ss.process(self.make_hands(), 0.0)
        self.ss.process(self.make_hands(), 3.1)
        self.assertEqual(self.ss._state, ScreenshotState.CAPTURED)
        
        # Release only right hand
        self.ss.process(self.make_hands(right_valid=False), 3.2)
        self.ss.process(self.make_hands(right_valid=False), 4.0) # past rearm duration
        
        # Should still be captured state waiting for FULL release
        self.assertEqual(self.ss._state, ScreenshotState.CAPTURED)
        
        # Release both
        self.ss.process(self.make_hands(left_valid=False, right_valid=False), 4.1)
        self.ss.process(self.make_hands(left_valid=False, right_valid=False), 5.0)
        self.assertEqual(self.ss._state, ScreenshotState.READY)

if __name__ == "__main__":
    unittest.main()
