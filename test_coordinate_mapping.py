import unittest
from core.display import map_to_desktop, TrackpadZone, VirtualDesktop

class TestCoordinateMapping(unittest.TestCase):
    def setUp(self):
        self.trackpad = TrackpadZone(x_min=100, x_max=540, y_min=100, y_max=380) # Using 640x480 with 100 margin
        self.desktop = VirtualDesktop(total_width=1920, total_height=1080, monitor_count=1)

    def test_horizontal_mapping(self):
        # norm_x = 0.0 (Left edge of mirrored preview)
        # Should clamp to x_min and map to 0
        x, y = map_to_desktop(0.0, 0.5, self.trackpad, self.desktop)
        self.assertEqual(x, 0)
        
        # norm_x = 1.0 (Right edge of mirrored preview)
        # Should clamp to x_max and map to desktop width
        x, y = map_to_desktop(1.0, 0.5, self.trackpad, self.desktop)
        self.assertEqual(x, 1919)
        
        # norm_x = 0.5 (Center)
        # Should map to center
        x, y = map_to_desktop(0.5, 0.5, self.trackpad, self.desktop)
        self.assertEqual(x, 1919 // 2)

    def test_vertical_mapping(self):
        # norm_y = 0.0 (Top)
        x, y = map_to_desktop(0.5, 0.0, self.trackpad, self.desktop)
        self.assertEqual(y, 0)
        
        # norm_y = 1.0 (Bottom)
        x, y = map_to_desktop(0.5, 1.0, self.trackpad, self.desktop)
        self.assertEqual(y, 1079)
        
        # norm_y = 0.5 (Center)
        x, y = map_to_desktop(0.5, 0.5, self.trackpad, self.desktop)
        self.assertEqual(y, 1079 // 2)

if __name__ == "__main__":
    unittest.main()
