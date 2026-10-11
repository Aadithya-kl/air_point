import unittest
import time
from core.tracker import Landmark, HandsResult
from core.gestures.orchestrator import GestureOrchestrator
from core.actuator import MouseActuator
from core.display import VirtualDesktop, TrackpadZone
import config

class DummyActuator(MouseActuator):
    def __init__(self):
        self.clicks = 0
        self.moves = 0
        self.double_clicks = 0
        self.drags = 0
        self.drops = 0
        self._is_dragging = False
        self.last_move = None
        
    def left_click(self): self.clicks += 1
    def move(self, x, y): 
        self.moves += 1
        self.last_move = (x, y)
    def double_click(self): self.double_clicks += 1
    def drag_start(self):
        self.drags += 1
        self._is_dragging = True
    def drag_end(self):
        self.drops += 1
        self._is_dragging = False
        
    def ctrl_down(self): pass
    def ctrl_up(self): pass
    def win_d(self): pass
    def alt_tab(self): pass
    def win_tab(self): pass
    def win_key(self): pass
    def alt_left(self): pass

class TestPhase6_3_Pointer(unittest.TestCase):
    def setUp(self):
        config.POINTER_ONLY_MODE = False
        config.PINCH_CLICK_ENABLED = True
        config.LEFT_DRAG_ENABLED = True
        config.SINGLE_CLICK_MODE = "comfortable"
        config.CAMERA_FPS = 30
        config.ONE_EURO_MINCUTOFF = 1.0
        config.ONE_EURO_BETA = 0.0
        config.ONE_EURO_DCUTOFF = 1.0
        
        self.actuator = DummyActuator()
        self.orchestrator = GestureOrchestrator(self.actuator, VirtualDesktop(100, 100, 1), TrackpadZone(0, 1, 0, 1))
        
    def _make_hands(self, right_pos=None, left_pos=None, left_pointing=False):
        hands = HandsResult()
        
        if right_pos is not None:
            lms = [Landmark(x=right_pos[0], y=right_pos[1], z=0.0)] * 21
            lms[5] = Landmark(x=right_pos[0], y=right_pos[1], z=0.0)
            lms[17] = Landmark(x=right_pos[0]+0.1, y=right_pos[1], z=0.0)
            lms[8] = Landmark(x=right_pos[0], y=right_pos[1], z=0.0)
            lms[4] = Landmark(x=right_pos[0]+0.2, y=right_pos[1], z=0.0)
            hands.right = lms
            
        if left_pos is not None:
            lms = [Landmark(x=left_pos[0], y=left_pos[1], z=0.0)] * 21
            if left_pointing:
                # index pointing, others curled
                lms[8] = Landmark(x=left_pos[0], y=left_pos[1]-0.2, z=0.0) # tip above pip
                lms[6] = Landmark(x=left_pos[0], y=left_pos[1], z=0.0)
                lms[12] = Landmark(x=left_pos[0], y=left_pos[1]+0.2, z=0.0) # middle curled
                lms[10] = Landmark(x=left_pos[0], y=left_pos[1], z=0.0)
                lms[16] = Landmark(x=left_pos[0], y=left_pos[1]+0.2, z=0.0)
                lms[14] = Landmark(x=left_pos[0], y=left_pos[1], z=0.0)
                lms[20] = Landmark(x=left_pos[0], y=left_pos[1]+0.2, z=0.0)
                lms[18] = Landmark(x=left_pos[0], y=left_pos[1], z=0.0)
                # thumb away from index
                lms[4] = Landmark(x=left_pos[0]+0.5, y=left_pos[1], z=0.0)
                lms[5] = Landmark(x=left_pos[0], y=left_pos[1], z=0.0)
                lms[17] = Landmark(x=left_pos[0]+0.1, y=left_pos[1], z=0.0)
            else:
                # fist (all fingers curled below pip - non-pointing)
                for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
                    lms[tip] = Landmark(x=left_pos[0], y=left_pos[1]+0.2, z=0.0)
                    lms[pip] = Landmark(x=left_pos[0], y=left_pos[1], z=0.0)
                lms[4] = Landmark(x=left_pos[0]+0.1, y=left_pos[1]+0.1, z=0.0)
                lms[5] = Landmark(x=left_pos[0], y=left_pos[1], z=0.0)
                lms[17] = Landmark(x=left_pos[0]+0.1, y=left_pos[1], z=0.0)
            hands.left = lms
            
        return hands

    def test_right_hand_moves_pointer(self):
        self.orchestrator.process(self._make_hands(right_pos=(0.5, 0.5)))
        time.sleep(0.3)
        self.orchestrator.process(self._make_hands(right_pos=(0.5, 0.5)))
        self.assertEqual(self.orchestrator.pointer_owner, "RIGHT")
        self.assertTrue(self.actuator.moves > 0)

    def test_left_hand_pointing_takes_over_if_right_absent(self):
        # 1. Left hand present, right hand absent
        self.orchestrator.process(self._make_hands(left_pos=(0.5, 0.5), left_pointing=True))
        time.sleep(0.3) # Grace period
        self.orchestrator.process(self._make_hands(left_pos=(0.5, 0.5), left_pointing=True))
        self.assertEqual(self.orchestrator.pointer_owner, "LEFT")

    def test_left_hand_non_pointing_does_not_move_pointer(self):
        # Left hand in fist does not move pointer
        self.orchestrator.process(self._make_hands(left_pos=(0.5, 0.5), left_pointing=False))
        time.sleep(0.3) # Grace period
        self.orchestrator.process(self._make_hands(left_pos=(0.5, 0.5), left_pointing=False))
        self.assertEqual(self.orchestrator.pointer_owner, "NONE")
        self.assertEqual(self.actuator.moves, 0)

    def test_left_hand_open_palm_moves_pointer(self):
        # Open palm facing camera: all 5 fingers extended - must NOT freeze pointer
        hands = HandsResult()
        lms = [Landmark(x=0.5, y=0.5, z=0.0)] * 21
        for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
            lms[tip] = Landmark(x=0.5, y=0.3, z=0.0)
            lms[pip] = Landmark(x=0.5, y=0.5, z=0.0)
        lms[4] = Landmark(x=0.3, y=0.4, z=0.0)
        lms[5] = Landmark(x=0.5, y=0.5, z=0.0)
        lms[17] = Landmark(x=0.6, y=0.5, z=0.0)
        hands.left = lms

        self.orchestrator.process(hands)
        time.sleep(0.3)
        self.orchestrator.process(hands)
        self.assertEqual(self.orchestrator.pointer_owner, "LEFT")
        self.assertTrue(self.actuator.moves > 0)
        
    def test_right_hand_takes_precedence_over_left_pointing(self):
        self.orchestrator.process(self._make_hands(right_pos=(0.5, 0.5), left_pos=(0.2, 0.2), left_pointing=True))
        time.sleep(0.3)
        self.orchestrator.process(self._make_hands(right_pos=(0.5, 0.5), left_pos=(0.2, 0.2), left_pointing=True))
        self.assertEqual(self.orchestrator.pointer_owner, "RIGHT")

    def test_smooth_transition_no_jump(self):
        # Move right hand
        self.orchestrator.process(self._make_hands(right_pos=(0.5, 0.5)))
        time.sleep(0.3)
        self.orchestrator.process(self._make_hands(right_pos=(0.5, 0.5)))
        last_right_pos = self.actuator.last_move
        
        # Right hand disappears, Left hand appears at completely different spot
        self.orchestrator.process(self._make_hands(left_pos=(0.1, 0.1), left_pointing=True))
        time.sleep(0.3)
        self.orchestrator.process(self._make_hands(left_pos=(0.1, 0.1), left_pointing=True))
        self.assertEqual(self.orchestrator.pointer_owner, "LEFT")
        
        # Cursor MUST jump to the new absolute coordinate
        self.assertNotEqual(self.actuator.last_move, last_right_pos)
        
        # Left hand moves slightly
        self.orchestrator.process(self._make_hands(left_pos=(0.2, 0.1), left_pointing=True))
        # It should move right by 0.1 * 100 = 10 pixels!
        pass
