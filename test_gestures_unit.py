import time
import unittest
from core.tracker import Landmark
from core.gestures.right_hand import RightHandProcessor, _State
from core.actuator import MouseActuator
from core.display import VirtualDesktop, TrackpadZone
import config

class DummyActuator(MouseActuator):
    def __init__(self):
        self.clicks = 0
        self.moves = 0
        self.last_move = None
        
    def left_click(self): self.clicks += 1
    def move(self, x, y):
        self.moves += 1
        self.last_move = (x, y)


class TestPhase1(unittest.TestCase):
    def setUp(self):
        # Force POINTER_ONLY_MODE just in case
        config.POINTER_ONLY_MODE = True
        self.actuator = DummyActuator()
        self.processor = RightHandProcessor(self.actuator, VirtualDesktop(1920, 1080, 1), TrackpadZone(0.1, 0.9, 0.1, 0.9))

    def make_hand(self, x, y):
        lms = [Landmark(x, y, 0) for _ in range(21)]
        return lms

    def test_no_mouse_actions_except_move(self):
        lms = self.make_hand(0.5, 0.5)
        self.processor.process(lms)
        # Verify it moved
        self.assertGreater(self.actuator.moves, 0)
        # Verify no clicks
        self.assertEqual(self.actuator.clicks, 0)
        self.assertEqual(self.processor._state, _State.IDLE)

    def test_missing_landmarks_reset(self):
        self.processor.process(self.make_hand(0.5, 0.5))
        self.assertIsNotNone(self.processor._filter_x._last_time)
        
        # Tracking lost
        self.processor.process(None)
        # Filters should be reset
        self.assertIsNone(self.processor._filter_x._last_time)
        self.assertIsNone(self.processor._filter_y._last_time)

    def test_fingertip_extraction(self):
        lms = self.make_hand(0.5, 0.5)
        self.processor.process(lms)
        self.assertIn(4, self.processor.visual_tips)
        self.assertIn(8, self.processor.visual_tips)
        self.assertIn(12, self.processor.visual_tips)
        self.assertIn(16, self.processor.visual_tips)
        self.assertIn(20, self.processor.visual_tips)
        self.assertEqual(len(self.processor.visual_tips), 5)

if __name__ == "__main__":
    unittest.main()
