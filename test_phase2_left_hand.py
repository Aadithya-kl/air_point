import unittest
import time
from core.tracker import Landmark, HandsResult
from core.gestures.orchestrator import GestureOrchestrator
from core.gestures.left_hand import LeftHandProcessor, _LeftState
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
        self.keys_sent = []
        
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
        
    def ctrl_down(self): self.keys_sent.append("ctrl_down")
    def ctrl_up(self): self.keys_sent.append("ctrl_up")
    def win_d(self): self.keys_sent.append("win_d")
    def alt_tab(self): self.keys_sent.append("alt_tab")
    def win_tab(self): self.keys_sent.append("win_tab")
    def win_key(self): self.keys_sent.append("win_key")
    def alt_left(self): self.keys_sent.append("alt_left")


class TestPhase2LeftHand(unittest.TestCase):
    def setUp(self):
        config.POINTER_ONLY_MODE = False
        config.PINCH_CLICK_ENABLED = True
        config.LEFT_DRAG_ENABLED = True
        config.CAMERA_FPS = 30
        config.LEFT_DRAG_START_NORM_DIST = 0.55
        config.LEFT_DRAG_RELEASE_NORM_DIST = 0.75
        config.LEFT_DRAG_CONFIRM_S = 0.10
        
        self.actuator = DummyActuator()
        self.desktop = VirtualDesktop(1920, 1080, 1)
        self.trackpad = TrackpadZone(100, 540, 100, 380)
        self.orchestrator = GestureOrchestrator(self.actuator, self.desktop, self.trackpad)
        self.processor = LeftHandProcessor(self.actuator, self.desktop, self.trackpad)

    def _make_left_hand(self, x=0.5, y=0.5, pinch_norm=0.9, open_palm=True, fist=False):
        lms = [Landmark(x=x, y=y, z=0.0)] * 21
        lms[5] = Landmark(x=x - 0.1, y=y, z=0.0)
        lms[17] = Landmark(x=x + 0.1, y=y, z=0.0)
        lms[8] = Landmark(x=x, y=y, z=0.0)  # index tip
        lms[6] = Landmark(x=x, y=y + 0.1, z=0.0)  # index pip

        if fist:
            for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
                lms[tip] = Landmark(x=x, y=y + 0.2, z=0.0)
                lms[pip] = Landmark(x=x, y=y + 0.1, z=0.0)
            lms[4] = Landmark(x=x + 0.05, y=y + 0.1, z=0.0)
        elif open_palm:
            for tip, pip in [(12, 10), (16, 14), (20, 18)]:
                lms[tip] = Landmark(x=x, y=y - 0.1, z=0.0)
                lms[pip] = Landmark(x=x, y=y, z=0.0)
            lms[4] = Landmark(x=x - (0.2 * pinch_norm), y=y, z=0.0)
        else:
            # curled non-index
            for tip, pip in [(12, 10), (16, 14), (20, 18)]:
                lms[tip] = Landmark(x=x, y=y + 0.1, z=0.0)
                lms[pip] = Landmark(x=x, y=y, z=0.0)
            lms[4] = Landmark(x=x - (0.2 * pinch_norm), y=y, z=0.0)

        return lms

    def test_left_index_movement_with_open_palm_facing_camera(self):
        """1. Left index moves smoothly with palm completely open facing camera."""
        hand1 = self._make_left_hand(x=0.3, y=0.5, open_palm=True)
        self.processor.process(hand1)
        self.assertTrue(self.processor.is_pointing)
        self.assertIsNotNone(self.processor.cursor_pos)
        pos1 = self.processor.cursor_pos

        time.sleep(0.03)
        hand2 = self._make_left_hand(x=0.6, y=0.5, open_palm=True)
        self.processor.process(hand2)
        self.assertTrue(self.processor.is_pointing)
        pos2 = self.processor.cursor_pos
        self.assertGreater(pos2[0], pos1[0], "Cursor X must advance to the right")

    def test_changing_palm_orientation_does_not_freeze_tracking(self):
        """2. Changing palm orientation does not freeze tracking."""
        # Frame 1: open palm facing camera
        self.processor.process(self._make_left_hand(x=0.4, y=0.4, open_palm=True))
        self.assertTrue(self.processor.is_pointing)

        # Frame 2: fingers curled (pointing pose)
        time.sleep(0.03)
        self.processor.process(self._make_left_hand(x=0.45, y=0.4, open_palm=False))
        self.assertTrue(self.processor.is_pointing, "Tracking must NOT freeze when fingers curl")

        # Frame 3: open palm again
        time.sleep(0.03)
        self.processor.process(self._make_left_hand(x=0.5, y=0.4, open_palm=True))
        self.assertTrue(self.processor.is_pointing, "Tracking must NOT freeze when palm re-opens")

    def test_stationary_landmark_noise_stability(self):
        """3. Stationary landmark noise is smoothly filtered without jitter."""
        positions = []
        for i in range(10):
            noise_x = 0.5 + (0.001 if i % 2 == 0 else -0.001)
            time.sleep(0.02)
            self.processor.process(self._make_left_hand(x=noise_x, y=0.5))
            positions.append(self.processor.cursor_pos)

        deltas = [abs(positions[i][0] - positions[i-1][0]) for i in range(1, len(positions))]
        max_delta = max(deltas)
        self.assertLess(max_delta, 10, "Stationary jitter should be minimal")

    def test_fast_movement_responsive(self):
        """4. Fast movement tracks promptly without trailing."""
        self.processor.process(self._make_left_hand(x=0.2, y=0.5))
        pos_initial = self.processor.cursor_pos
        # Fast sweep from 0.2 to 0.7 across 5 frames (150ms)
        for step in range(1, 6):
            time.sleep(0.03)
            curr_x = 0.2 + (0.5 * step / 5.0)
            self.processor.process(self._make_left_hand(x=curr_x, y=0.5))
        pos = self.processor.cursor_pos
        self.assertGreater(pos[0], pos_initial[0], "Should track in direction of motion")

    def test_left_pinch_initiates_precisely_one_drag(self):
        """7. A left pinch initiates precisely one drag event."""
        # Idle open
        self.processor.process(self._make_left_hand(pinch_norm=0.9))
        self.assertEqual(self.actuator.drags, 0)
        self.assertEqual(self.processor.left_drag_state, "IDLE")

        # Pinch together (pinch_norm=0.4 < 0.55)
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        self.assertEqual(self.processor.left_drag_state, "GRAB_CANDIDATE")
        self.assertEqual(self.actuator.drags, 0)

        # Hold for confirmation (0.12s > 0.10s)
        time.sleep(0.12)
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        self.assertEqual(self.actuator.drags, 1)
        self.assertTrue(self.actuator._is_dragging)
        self.assertEqual(self.processor.left_drag_state, "DRAGGING")

    def test_holding_pinch_keeps_mouse_down_without_repeated_events(self):
        """8. Holding the pinch keeps the mouse button held without repeated down events."""
        # Enter drag
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        time.sleep(0.12)
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        self.assertEqual(self.actuator.drags, 1)

        # Hold for 10 frames
        for _ in range(10):
            time.sleep(0.02)
            self.processor.process(self._make_left_hand(pinch_norm=0.4))

        self.assertEqual(self.actuator.drags, 1, "Must never emit duplicate drag_start events while held")
        self.assertTrue(self.actuator._is_dragging)

    def test_releasing_pinch_produces_precisely_one_drop(self):
        """9. Releasing the pinch produces precisely one mouse-button release."""
        # Enter drag
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        time.sleep(0.12)
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        self.assertEqual(self.actuator.drags, 1)

        # Release (pinch_norm=0.85 > 0.75)
        time.sleep(0.02)
        self.processor.process(self._make_left_hand(pinch_norm=0.85))
        self.assertEqual(self.actuator.drops, 1)
        self.assertFalse(self.actuator._is_dragging)
        self.assertEqual(self.processor.drag_state_text, "DROP")

    def test_drag_hysteresis_band_prevents_accidental_drop(self):
        """Hysteresis band: landmark noise between 0.55 and 0.75 does not drop."""
        # Enter drag
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        time.sleep(0.12)
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        self.assertEqual(self.actuator.drags, 1)

        # Noise fluctuations to 0.65 (above start 0.55 but below release 0.75)
        for _ in range(5):
            time.sleep(0.02)
            self.processor.process(self._make_left_hand(pinch_norm=0.65))

        self.assertEqual(self.actuator.drops, 0, "Noise below release threshold must NOT drop")
        self.assertTrue(self.actuator._is_dragging)

    def test_lost_hand_during_drag_safely_releases(self):
        """10. Lost hand during dragging cannot leave mouse button stuck."""
        # Enter drag
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        time.sleep(0.12)
        self.processor.process(self._make_left_hand(pinch_norm=0.4))
        self.assertTrue(self.actuator._is_dragging)

        # Hand leaves camera view
        time.sleep(0.02)
        self.processor.process(None)
        self.assertFalse(self.actuator._is_dragging, "Mouse button must be safely released when hand lost")
        self.assertEqual(self.actuator.drops, 1)

    def test_left_hand_never_triggers_windows_shortcuts(self):
        """11. Left-hand gestures never trigger unintended Windows desktop shortcuts."""
        # Open palm
        self.processor.process(self._make_left_hand(open_palm=True))
        # Fist
        self.processor.process(self._make_left_hand(fist=True))
        # Drag pinch
        self.processor.process(self._make_left_hand(pinch_norm=0.4))

        self.assertEqual(len(self.actuator.keys_sent), 0, "No desktop-manipulating shortcuts should be sent")

    def test_two_hand_drag_right_controls_pointer_left_controls_drag(self):
        """Section 4/5: Right hand controls pointer while left pinch controls dragging."""
        hands = HandsResult()
        # Right hand at (0.3, 0.3)
        r_lms = [Landmark(x=0.3, y=0.3, z=0.0)] * 21
        r_lms[5] = Landmark(x=0.2, y=0.3, z=0.0)
        r_lms[17] = Landmark(x=0.4, y=0.3, z=0.0)
        r_lms[8] = Landmark(x=0.3, y=0.3, z=0.0)
        r_lms[4] = Landmark(x=0.5, y=0.3, z=0.0)
        hands.right = r_lms

        # Left hand open at (0.8, 0.8)
        hands.left = self._make_left_hand(x=0.8, y=0.8, pinch_norm=0.9)

        self.orchestrator.process(hands)
        self.assertEqual(self.orchestrator.pointer_owner, "RIGHT")
        self.assertFalse(self.actuator._is_dragging)
        initial_x = self.actuator.last_move[0]

        # Left hand pinches (0.4)
        hands.left = self._make_left_hand(x=0.8, y=0.8, pinch_norm=0.4)
        self.orchestrator.process(hands)  # Frame 1: Enters GRAB_CANDIDATE
        time.sleep(0.12)
        self.orchestrator.process(hands)  # Frame 2: Confirms hold -> DRAGGING
        self.assertTrue(self.actuator._is_dragging, "Left pinch initiates drag")
        self.assertEqual(self.orchestrator.pointer_owner, "RIGHT", "Right hand MUST retain pointer ownership")

        # Right hand moves to (0.6, 0.6) while left hand stays pinching
        r_lms = [Landmark(x=0.6, y=0.6, z=0.0)] * 21
        r_lms[5] = Landmark(x=0.5, y=0.6, z=0.0)
        r_lms[17] = Landmark(x=0.7, y=0.6, z=0.0)
        r_lms[8] = Landmark(x=0.6, y=0.6, z=0.0)
        r_lms[4] = Landmark(x=0.8, y=0.6, z=0.0)
        hands.right = r_lms

        time.sleep(0.02)
        self.orchestrator.process(hands)
        self.assertTrue(self.actuator._is_dragging)
        self.assertEqual(self.orchestrator.pointer_owner, "RIGHT")
        self.assertGreater(self.actuator.last_move[0], initial_x, "Cursor must follow right hand movement")

        # Left hand releases pinch
        hands.left = self._make_left_hand(x=0.8, y=0.8, pinch_norm=0.85)
        time.sleep(0.02)
        self.orchestrator.process(hands)
        self.assertFalse(self.actuator._is_dragging, "Releasing left pinch drops item")
        self.assertEqual(self.actuator.drops, 1)


if __name__ == "__main__":
    unittest.main()
