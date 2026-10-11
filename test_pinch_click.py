import unittest
import time
from core.tracker import Landmark
from core.gestures.right_hand import RightHandProcessor, _State
from core.actuator import MouseActuator
from core.display import VirtualDesktop, TrackpadZone
import config

class DummyActuator(MouseActuator):
    def __init__(self):
        self.clicks = 0
        self.moves = 0
        self.double_clicks = 0
        
    def left_click(self): self.clicks += 1
    def move(self, x, y): self.moves += 1
    def double_click(self): self.double_clicks += 1
    def drag_start(self): pass
    def drag_end(self): pass

class TestPinchClick(unittest.TestCase):
    def setUp(self):
        config.POINTER_ONLY_MODE = False
        config.PINCH_CLICK_ENABLED = True
        config.DRAG_ENABLED = False
        config.PINCH_START_NORM_DIST = 0.50
        config.PINCH_RELEASE_NORM_DIST = 0.80
        config.PINCH_CONFIRM_S = 0.10
        config.PINCH_COOLDOWN_S = 0.1
        config.SINGLE_CLICK_MODE = "instant"
        
        self.actuator = DummyActuator()
        self.processor = RightHandProcessor(self.actuator, VirtualDesktop(1920, 1080, 1), TrackpadZone(0, 640, 0, 480))

    def make_hand(self, pinch_dist_norm):
        from core.tracker import HandsResult
        lms = [Landmark(x=0.5, y=0.5, z=0.0)] * 21
        lms[5] = Landmark(x=0.4, y=0.5, z=0.0)
        lms[17] = Landmark(x=0.6, y=0.5, z=0.0)
        lms[8] = Landmark(x=0.5, y=0.5, z=0.0)
        lms[4] = Landmark(x=0.5 + (0.2 * pinch_dist_norm), y=0.5, z=0.0)
        return lms

    def test_pinch_candidate(self):
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.processor._state, _State.PINCH_CANDIDATE)
        time.sleep(0.05)
        self.processor.process(self.make_hand(0.9)) # release early
        self.assertEqual(self.processor._state, _State.IDLE)
        self.assertEqual(self.actuator.clicks, 0) # Not confirmed yet

    def test_pinch_and_release(self):
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.processor.process(self.make_hand(0.9)) # Release it
        self.assertEqual(self.processor._state, _State.IDLE)
        self.assertEqual(self.actuator.clicks, 1)

    def test_cooldown(self):
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.processor.process(self.make_hand(0.9)) # Clicked and Released
        
        self.processor.process(self.make_hand(0.4)) # Try again immediately
        self.assertEqual(self.processor.click_state_text, "COOLDOWN")

    def test_no_repeated_clicks(self):
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        
        # Hold pinch across multiple frames
        for _ in range(5):
            self.processor.process(self.make_hand(0.3))
            
        self.processor.process(self.make_hand(0.9)) # Release
        self.assertEqual(self.actuator.clicks, 1) # NO repeated clicks

    def test_double_click_snap(self):
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.processor.process(self.make_hand(0.9)) # Release 1
        pos1 = self.processor._locked_cursor_pos
        
        time.sleep(0.15) 
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.processor.process(self.make_hand(0.9)) # Release 2
        
        self.assertEqual(self.actuator.clicks, 2)
        pass

    def test_double_click_outside_time(self):
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.processor.process(self.make_hand(0.9))
        
        time.sleep(0.8) 
        
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.processor.process(self.make_hand(0.9))
        
        self.assertEqual(self.processor.click_state_text, "READY")
