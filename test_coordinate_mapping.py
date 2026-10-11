import unittest
import numpy as np
from core.display import VirtualDesktop, TrackpadZone, map_to_desktop
from config import CAMERA_WIDTH, CAMERA_HEIGHT

class TestCoordinateMapping(unittest.TestCase):
    def setUp(self):
        # Fake 1920x1080 desktop
        self.desktop = VirtualDesktop(1920, 1080, 1)
        # Fake trackpad zone (100 margin on 640x480 camera)
        self.trackpad = TrackpadZone(100, 540, 100, 380)

    def test_top_left(self):
        # Hand at top-left of the trackpad zone (norm_x = 100/640, norm_y = 100/480)
        norm_x = self.trackpad.x_min / float(CAMERA_WIDTH)
        norm_y = self.trackpad.y_min / float(CAMERA_HEIGHT)
        x, y = map_to_desktop(norm_x, norm_y, self.trackpad, self.desktop)
        self.assertEqual((x, y), (0, 0))

    def test_top_right(self):
        norm_x = self.trackpad.x_max / float(CAMERA_WIDTH)
        norm_y = self.trackpad.y_min / float(CAMERA_HEIGHT)
        x, y = map_to_desktop(norm_x, norm_y, self.trackpad, self.desktop)
        self.assertEqual((x, y), (1919, 0))

    def test_bottom_left(self):
        norm_x = self.trackpad.x_min / float(CAMERA_WIDTH)
        norm_y = self.trackpad.y_max / float(CAMERA_HEIGHT)
        x, y = map_to_desktop(norm_x, norm_y, self.trackpad, self.desktop)
        self.assertEqual((x, y), (0, 1079))

    def test_bottom_right(self):
        norm_x = self.trackpad.x_max / float(CAMERA_WIDTH)
        norm_y = self.trackpad.y_max / float(CAMERA_HEIGHT)
        x, y = map_to_desktop(norm_x, norm_y, self.trackpad, self.desktop)
        self.assertEqual((x, y), (1919, 1079))

    def test_center(self):
        norm_x = (self.trackpad.x_min + self.trackpad.x_max) / 2.0 / float(CAMERA_WIDTH)
        norm_y = (self.trackpad.y_min + self.trackpad.y_max) / 2.0 / float(CAMERA_HEIGHT)
        x, y = map_to_desktop(norm_x, norm_y, self.trackpad, self.desktop)
        # Center of 1919 is 959, 1079 is 539
        self.assertEqual((x, y), (959, 539))

    def test_clamping_outside_trackpad(self):
        # Hand completely outside the trackpad (extreme edge of camera frame)
        # Should clamp to 0 and 1919/1079
        x, y = map_to_desktop(0.0, 0.0, self.trackpad, self.desktop)
        self.assertEqual((x, y), (0, 0))
        
        x, y = map_to_desktop(1.0, 1.0, self.trackpad, self.desktop)
        self.assertEqual((x, y), (1919, 1079))
        
if __name__ == '__main__':
    unittest.main()
