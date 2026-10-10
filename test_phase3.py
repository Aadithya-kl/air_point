import unittest
import time
from core.gestures.right_hand import RightHandProcessor, _State
from core.tracker import Landmark
from core.display import VirtualDesktop, TrackpadZone
import config

class MockActuator:
    def __init__(self):
        self.left_clicks = 0
        self.right_clicks = 0
        self.double_clicks = 0
        self.zoom_ins = 0
        self.zoom_outs = 0
        self.moves = 0
    def move(self, x, y): self.moves += 1
    def left_click(self): self.left_clicks += 1
    def right_click(self): self.right_clicks += 1
    def double_click(self): self.double_clicks += 1
    def zoom_in(self): self.zoom_ins += 1
    def zoom_out(self): self.zoom_outs += 1
    def mag_zoom_in(self): self.zoom_ins += 1
    def mag_zoom_out(self): self.zoom_outs += 1

class TestPhase3(unittest.TestCase):
    def setUp(self):
        config.POINTER_ONLY_MODE = False
        config.PINCH_CLICK_ENABLED = True
        config.PINCH_START_NORM_DIST = 0.50
        config.PINCH_RELEASE_NORM_DIST = 0.80
        config.PINCH_CONFIRM_S = 0.10
        config.PINCH_COOLDOWN_S = 0.1
        config.SINGLE_CLICK_MODE = "comfortable"
        config.DOUBLE_CLICK_GESTURE_WINDOW_MS = 300 # Keep short for tests
        config.ZOOM_ENABLED = True
        config.ZOOM_ACTIVATION_DURATION_S = 0.1
        config.ZOOM_EXIT_DURATION_S = 0.1
        config.ZOOM_COOLDOWN_S = 0.05
        
        self.actuator = MockActuator()
        self.processor = RightHandProcessor(self.actuator, VirtualDesktop(1920, 1080, 1), TrackpadZone(0, 640, 0, 480))

    def make_hand(self, thumb_idx_dist=1.0, idx_mid_dist=1.0, is_peace=False):
        lms = [Landmark(0, 0, 0) for _ in range(21)]
        lms[0] = Landmark(0, 0, 0)
        lms[5] = Landmark(0, 1, 0)
        lms[17] = Landmark(0, 2, 0) # palm width = 1.0
        
        lms[4] = Landmark(0, 0, 0) # thumb
        lms[8] = Landmark(thumb_idx_dist, 0, 0) # index
        lms[12] = Landmark(idx_mid_dist, 0, 0) # mid
        
        if is_peace:
            # Peace sign = index & middle extended, others bent
            # We must spoof the conditions for utils.is_peace_sign
            # Index & middle pip < tip (extended) -> roughly tip.y < pip.y (if 0 is wrist)
            lms[5].y = 0.5; lms[6].y = 0.4; lms[7].y = 0.3; lms[8].y = 0.1
            lms[9].y = 0.5; lms[10].y = 0.4; lms[11].y = 0.3; lms[12].y = 0.1
            
            # Others bent (tip > pip)
            lms[13].y = 0.5; lms[14].y = 0.6; lms[15].y = 0.7; lms[16].y = 0.8
            lms[17].y = 0.5; lms[18].y = 0.6; lms[19].y = 0.7; lms[20].y = 0.8
            
            # Thumb bent
            lms[2].y = 0.5; lms[3].y = 0.6; lms[4].y = 0.7
            
            # Distance between index & mid
            lms[8].x = 0.0
            lms[12].x = idx_mid_dist
            
            # Fix palm width to 1.0
            lms[17].x = 1.0
            lms[5].x = 0.0
            
        return lms

    def test_comfortable_single_click(self):
        # 1. Pinch
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.15)
        self.processor.process(self.make_hand(0.4)) # confirmed -> CLICK_PENDING
        self.assertEqual(self.processor._state, _State.CLICK_PENDING)
        self.assertEqual(self.actuator.left_clicks, 0)
        
        # 2. Release
        self.processor.process(self.make_hand(0.9))
        self.assertEqual(self.processor._state, _State.CLICK_PENDING) # STILL PENDING
        
        # 3. Wait for window to expire
        time.sleep(0.4)
        self.processor.process(self.make_hand(0.9))
        self.assertEqual(self.processor._state, _State.IDLE)
        self.assertEqual(self.actuator.left_clicks, 1)

    def test_comfortable_double_click(self):
        # 1. First Pinch
        self.processor.process(self.make_hand(0.4))
        time.sleep(0.15)
        self.processor.process(self.make_hand(0.4)) # confirmed -> PENDING
        self.processor.process(self.make_hand(0.9)) # release
        
        time.sleep(0.15) # cooldown
        
        # 2. Second Pinch
        self.processor.process(self.make_hand(0.4))
        self.assertEqual(self.processor._state, _State.PINCH_CANDIDATE)
        time.sleep(0.15)
        self.processor.process(self.make_hand(0.4)) # confirmed -> DOUBLE CLICK
        
        self.assertEqual(self.processor._state, _State.PINCH_LOCKED)
        self.assertEqual(self.actuator.left_clicks, 0)
        self.assertEqual(self.actuator.double_clicks, 1)

    def test_zoom_activation_and_spread(self):
        # Ensure we start idle
        self.processor.process(self.make_hand())
        self.assertEqual(self.processor._state, _State.IDLE)
        
        # 1. Enter peace sign
        hand = self.make_hand(is_peace=True, idx_mid_dist=0.2)
        self.processor.process(hand)
        self.assertEqual(self.processor._state, _State.ZOOM_ACTIVATING)
        
        # 2. Hold peace sign
        time.sleep(0.15)
        self.processor.process(hand)
        self.assertEqual(self.processor._state, _State.ZOOM_ACTIVE)
        
        # 3. Spread fingers (distance increases beyond baseline + 0.15)
        hand2 = self.make_hand(is_peace=True, idx_mid_dist=0.4)
        time.sleep(0.1) # wait a tiny bit
        self.processor.process(hand2)
        self.assertEqual(self.actuator.zoom_ins, 1)
        self.assertEqual(self.actuator.zoom_outs, 0)
        
        # 4. Bring fingers closer (distance decreases)
        hand3 = self.make_hand(is_peace=True, idx_mid_dist=0.1)
        time.sleep(0.5) # wait for zoom cooldown
        self.processor.process(hand3)
        self.assertEqual(self.actuator.zoom_ins, 1)
        self.assertEqual(self.actuator.zoom_outs, 1)
        
        # 5. Exit zoom mode by making fist/normal hand
        hand_normal = self.make_hand(is_peace=False)
        self.processor.process(hand_normal)
        time.sleep(0.15)
        self.processor.process(hand_normal)
        self.assertEqual(self.processor._state, _State.IDLE)

if __name__ == "__main__":
    unittest.main()
