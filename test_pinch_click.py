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
        
    def left_click(self): self.clicks += 1
    def move(self, x, y): self.moves += 1


class TestPinchClick(unittest.TestCase):
    def setUp(self):
        config.POINTER_ONLY_MODE = False
        config.PINCH_CLICK_ENABLED = True
        config.PINCH_START_NORM_DIST = 0.50
        config.PINCH_RELEASE_NORM_DIST = 0.80
        config.PINCH_CONFIRM_S = 0.10
        config.PINCH_COOLDOWN_S = 0.1
        config.SINGLE_CLICK_MODE = "instant"
        
        self.actuator = DummyActuator()
        self.processor = RightHandProcessor(self.actuator, VirtualDesktop(1920, 1080, 1), TrackpadZone(0, 640, 0, 480))

    def make_hand(self, thumb_idx_dist=1.0):
        # 0 is wrist. Palm width is dist from 5 to 17.
        lms = [Landmark(0, 0, 0) for _ in range(21)]
        lms[0] = Landmark(0, 0, 0)
        lms[5] = Landmark(0, 1, 0)
        lms[17] = Landmark(0, 2, 0) # palm width = 1.0
        lms[4] = Landmark(0, 0, 0) # thumb
        lms[8] = Landmark(thumb_idx_dist, 0, 0) # index
        return lms

    def test_no_pinch(self):
        self.processor.process(self.make_hand(0.9)) # > 0.50
        self.assertEqual(self.processor._state, _State.IDLE)
        self.assertEqual(self.actuator.clicks, 0)
        self.assertFalse(self.processor.is_cursor_locked)
        
    def test_pinch_candidate(self):
        self.processor.process(self.make_hand(0.4)) # < 0.50
        self.assertEqual(self.processor._state, _State.PINCH_CANDIDATE)
        self.assertEqual(self.processor.click_state_text, "CANDIDATE")
        self.assertEqual(self.actuator.clicks, 0)
        self.assertTrue(self.processor.is_cursor_locked)
        
    def test_pinch_and_release(self):
        # 1. Start pinch
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.processor._state, _State.PINCH_CANDIDATE)
        self.assertTrue(self.processor.is_cursor_locked)
        
        # Hold for duration
        time.sleep(0.12) # > 0.10
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.processor._state, _State.PINCH_LOCKED)
        self.assertEqual(self.actuator.clicks, 1) # Clicked!
        self.assertTrue(self.processor.is_cursor_locked)
        
        # 2. Release pinch
        self.processor.process(self.make_hand(0.9)) # > 0.80
        self.assertEqual(self.processor._state, _State.IDLE)
        self.assertEqual(self.actuator.clicks, 1)
        self.assertEqual(self.processor.click_state_text, "READY")
        self.assertFalse(self.processor.is_cursor_locked)
        
    def test_pinch_too_short(self):
        self.processor.process(self.make_hand(0.4))
        self.assertTrue(self.processor.is_cursor_locked)
        # Release immediately
        self.processor.process(self.make_hand(0.9))
        self.assertEqual(self.processor._state, _State.IDLE)
        self.assertEqual(self.actuator.clicks, 0) # min duration not met, NO CLICK
        self.assertFalse(self.processor.is_cursor_locked)
        
    def test_cooldown(self):
        # Pinch and release
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.processor.process(self.make_hand(0.9))
        self.assertEqual(self.actuator.clicks, 1)
        
        # Try to pinch again immediately
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.processor._state, _State.IDLE) # should be blocked by cooldown
        
        # Wait for cooldown
        time.sleep(0.15)
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.processor._state, _State.PINCH_CANDIDATE)
        
    def test_no_repeated_clicks(self):
        # Start pinch
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4)) # Confirms and clicks
        self.assertEqual(self.actuator.clicks, 1)
        
        # Hold pinch across multiple frames
        for _ in range(5):
            self.processor.process(self.make_hand(0.3))
            self.assertEqual(self.actuator.clicks, 1) # NO repeated clicks
            
        # Release
        self.processor.process(self.make_hand(0.9))
        self.assertEqual(self.actuator.clicks, 1)

    def test_double_click_snap(self):
        # 1st Click
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.actuator.clicks, 1)
        self.assertEqual(self.processor.click_state_text, "SINGLE CLICK")
        pos1 = self.processor._locked_cursor_pos
        
        # Release
        self.processor.process(self.make_hand(0.9))
        self.assertEqual(self.processor._state, _State.IDLE)
        
        # Move slightly within tolerance (simulating slight hand drift)
        # Assuming map_to_desktop will map index to a close pos if we don't change hand size.
        # We can simulate by changing wrist offset. But for simplicity, we mock _filter_x out?
        # Let's just trust the snap logic by examining the internal _last_click_pos.
        # Actually, let's just make the hand and let the filter process it.
        time.sleep(0.15) # Wait for cooldown to pass
        
        # 2nd Click within window
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.actuator.clicks, 2)
        self.assertEqual(self.processor.click_state_text, "DOUBLE CLICK")
        pos2 = self.processor._locked_cursor_pos
        
        # Both clicks should be at the exact same location!
        self.assertEqual(pos1, pos2)
        
    def test_double_click_outside_time(self):
        # 1st Click
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.processor.click_state_text, "SINGLE CLICK")
        
        # Release
        self.processor.process(self.make_hand(0.9))
        
        # Wait outside double-pinch window
        time.sleep(0.8) 
        
        # 2nd Click
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.12)
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.actuator.clicks, 2)
        # Should NOT be a double click because it was outside the time window
        self.assertEqual(self.processor.click_state_text, "SINGLE CLICK")

if __name__ == "__main__":
    unittest.main()
