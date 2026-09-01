from __future__ import annotations

import unittest

from am_print_executor.gcode_printability_gate import (
    _parse_declared_layer_heights,
)


class VariableLayerHeightTests(unittest.TestCase):

    def test_change_layer_metadata(self):
        text = """
; CHANGE_LAYER
; Z_HEIGHT: 19.6
; LAYER_HEIGHT: 0.4
; FEATURE: Outer wall
; LINE_WIDTH: 0.46434
; LAYER_HEIGHT: 0.2
"""

        result = (
            _parse_declared_layer_heights(
                text
            )
        )

        self.assertEqual(
            result,
            {
                19.6: 0.4,
            },
        )

    def test_path_local_height_does_not_override_layer_height(self):
        text = """
; CHANGE_LAYER
; Z_HEIGHT: 10
; LAYER_HEIGHT: 0.35
; FEATURE: Outer wall
; LAYER_HEIGHT: 0.1
"""

        result = (
            _parse_declared_layer_heights(
                text
            )
        )

        self.assertEqual(
            result[10.0],
            0.35,
        )


if __name__ == "__main__":
    unittest.main()
