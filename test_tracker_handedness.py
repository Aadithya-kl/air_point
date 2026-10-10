import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from core.tracker import HandTracker, HandsResult

class TestHandTrackerHandedness(unittest.TestCase):
    @patch("core.tracker.HandLandmarker.create_from_options")
    def test_handedness_mapping(self, mock_create):
        # Setup mock detector
        mock_detector = MagicMock()
        mock_create.return_value = mock_detector
        
        tracker = HandTracker()
        
        # 1. Simulate MediaPipe detecting what it thinks is a "Right" hand.
        # Because we feed it a mirrored frame (selfie), MediaPipe sees a "Right" hand
        # only when the anatomical hand is LEFT.
        mock_result_left_anatomical = MagicMock()
        mock_result_left_anatomical.hand_landmarks = [[MagicMock(x=0.5, y=0.5, z=0.5, visibility=1.0)]]
        
        mock_handedness = MagicMock()
        mock_handedness.category_name = "Right"
        mock_handedness.score = 0.9
        mock_result_left_anatomical.handedness = [[mock_handedness]]
        
        mock_detector.detect_for_video.return_value = mock_result_left_anatomical
        
        # Process a dummy frame
        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        res = tracker.process(dummy_frame)
        
        # It should assign to the 'left' property because of the inversion logic
        self.assertIsNotNone(res.left)
        self.assertIsNone(res.right)
        
        # 2. Simulate MediaPipe detecting what it thinks is a "Left" hand.
        # In a mirrored frame, an anatomical RIGHT hand looks like a left hand.
        mock_result_right_anatomical = MagicMock()
        mock_result_right_anatomical.hand_landmarks = [[MagicMock(x=0.5, y=0.5, z=0.5, visibility=1.0)]]
        
        mock_handedness2 = MagicMock()
        mock_handedness2.category_name = "Left"
        mock_handedness2.score = 0.8
        mock_result_right_anatomical.handedness = [[mock_handedness2]]
        
        mock_detector.detect_for_video.return_value = mock_result_right_anatomical
        
        res2 = tracker.process(dummy_frame)
        
        # It should assign to the 'right' property
        self.assertIsNotNone(res2.right)
        self.assertIsNone(res2.left)

if __name__ == "__main__":
    unittest.main()
