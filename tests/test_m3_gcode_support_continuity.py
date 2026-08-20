import unittest

from am_print_executor.gcode_support_continuity import (
    parse_extrusion_segments,
    inspect_support_continuity,
)


class SupportContinuityTests(
    unittest.TestCase
):

    def test_supported_layers_pass(self):
        gcode = """
M83
G90
; FEATURE:Outer wall
G1 Z0.2
G1 X10 Y10 E1
G1 X20 Y10 E1
G1 Z0.4
G1 X10 Y10
G1 X20 Y10 E1
"""

        result = inspect_support_continuity(
            parse_extrusion_segments(gcode),
            minimum_danger_area_mm2=0.5,
        )

        self.assertEqual(
            result["status"],
            "pass",
        )


    def test_floating_region_blocks(self):
        gcode = """
M83
G90
; FEATURE:Outer wall
G1 Z0.2
G1 X10 Y10 E1
G1 X20 Y10 E1

G1 Z0.4
G1 X50 Y50
G1 X60 Y50 E1
G1 X60 Y60 E1
G1 X50 Y60 E1
G1 X50 Y50 E1
"""

        result = inspect_support_continuity(
            parse_extrusion_segments(gcode),
            minimum_danger_area_mm2=0.5,
            max_unsupported_ratio=0.1,
        )

        self.assertEqual(
            result["status"],
            "blocked",
        )

        self.assertTrue(
            any(
                item.startswith(
                    "unsupported_region:"
                )
                for item in result[
                    "blockers"
                ]
            )
        )


    def test_support_below_region_passes(self):
        gcode = """
M83
G90

; FEATURE:Support
G1 Z0.2
G1 X50 Y50
G1 X60 Y50 E1
G1 X60 Y60 E1
G1 X50 Y60 E1
G1 X50 Y50 E1

; FEATURE:Outer wall
G1 Z0.4
G1 X50 Y50
G1 X60 Y50 E1
G1 X60 Y60 E1
G1 X50 Y60 E1
G1 X50 Y50 E1
"""

        result = inspect_support_continuity(
            parse_extrusion_segments(gcode),
            minimum_danger_area_mm2=0.5,
        )

        self.assertEqual(
            result["status"],
            "pass",
        )


if __name__ == "__main__":
    unittest.main()
