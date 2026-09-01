from __future__ import annotations

import unittest

from am_print_executor.gcode_support_continuity import (
    parse_extrusion_segments,
)


class MotionStateParserTests(unittest.TestCase):

    def test_spiral_lift_then_restore(self):
        text = """
G90
M83
G1 X0 Y0 Z0.2
; FEATURE: Outer wall
G3 Z0.4 I1 J0 P1
G1 Z0.2
G1 X1 Y0 E0.1
"""
        result = parse_extrusion_segments(text)

        self.assertEqual(
            len(result),
            1,
        )

        self.assertAlmostEqual(
            result[0].z,
            0.2,
        )

    def test_spiral_motion_can_advance_next_real_layer(self):
        text = """
G90
M83
G1 X0 Y0 Z0.2
; FEATURE: Outer wall
G3 Z0.6 I1 J0 P1
G1 E0.8
G1 X1 Y0 E0.1
"""
        result = parse_extrusion_segments(text)

        self.assertEqual(
            len(result),
            1,
        )

        self.assertAlmostEqual(
            result[0].z,
            0.6,
        )

    def test_positive_arc_extrusion_is_visible(self):
        text = """
G90
M83
G17
G1 X1 Y0 Z0.2
; FEATURE: Outer wall
G3 X-1 Y0 I-1 J0 E0.5
"""
        result = parse_extrusion_segments(text)

        self.assertGreater(
            len(result),
            4,
        )


if __name__ == "__main__":
    unittest.main()
