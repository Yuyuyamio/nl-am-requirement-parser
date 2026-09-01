from __future__ import annotations

import unittest

from am_print_executor.r5_printability_v2 import (
    box_inside,
    rectangles_overlap_xy,
)


class R5PrintabilityV2Tests(unittest.TestCase):
    def test_box_inside_bed(self):
        bed = {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": 256.0,
            "max_y": 256.0,
        }

        self.assertTrue(
            box_inside(
                box=[[10, 10], [100, 100]],
                bed=bed,
            )
        )

        self.assertFalse(
            box_inside(
                box=[[-1, 10], [100, 100]],
                bed=bed,
            )
        )

    def test_rectangle_overlap(self):
        self.assertTrue(
            rectangles_overlap_xy(
                [[0, 0], [10, 10]],
                [[8, 8], [20, 20]],
            )
        )

        self.assertFalse(
            rectangles_overlap_xy(
                [[0, 0], [10, 10]],
                [[20, 20], [30, 30]],
            )
        )

    def test_rectangle_clearance_can_create_overlap(self):
        self.assertFalse(
            rectangles_overlap_xy(
                [[0, 0], [10, 10]],
                [[12, 0], [20, 10]],
                clearance_mm=0.0,
            )
        )

        self.assertTrue(
            rectangles_overlap_xy(
                [[0, 0], [10, 10]],
                [[12, 0], [20, 10]],
                clearance_mm=2.1,
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
