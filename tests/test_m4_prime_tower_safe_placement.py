import unittest

from am_print_executor.prime_tower_safe_placement import (
    generate_candidates,
    parse_printable_area,
)


class PrimeTowerSafePlacementTests(
    unittest.TestCase
):

    def test_parse_bambu_x_format(
        self,
    ):
        points = parse_printable_area(
            [
                "0x0",
                "256x0",
                "256x256",
                "0x256",
            ]
        )

        self.assertEqual(
            points,
            [
                (0.0, 0.0),
                (256.0, 0.0),
                (256.0, 256.0),
                (0.0, 256.0),
            ],
        )


    def test_parse_nested_points(
        self,
    ):
        points = parse_printable_area(
            [
                [0, 0],
                [300, 0],
                [300, 300],
                [0, 300],
            ]
        )

        self.assertEqual(
            len(points),
            4,
        )


    def test_candidates_are_not_sample_specific(
        self,
    ):
        candidates = generate_candidates(
            bbox={
                "min_x": 0,
                "max_x": 300,
                "min_y": 0,
                "max_y": 300,
            },
            tower_width=40,
            brim_width=4,
            edge_margin_mm=6,
            grid_size=4,
        )

        self.assertEqual(
            len(candidates),
            16,
        )

        for x, y in candidates:
            self.assertGreaterEqual(
                x,
                10,
            )

            self.assertLessEqual(
                x + 40,
                290,
            )

            self.assertGreaterEqual(
                y,
                10,
            )

            self.assertLessEqual(
                y,
                290,
            )


    def test_different_bed_generates_different_candidates(
        self,
    ):
        first = generate_candidates(
            bbox={
                "min_x": 0,
                "max_x": 200,
                "min_y": 0,
                "max_y": 200,
            },
            tower_width=30,
            brim_width=3,
            edge_margin_mm=5,
            grid_size=3,
        )

        second = generate_candidates(
            bbox={
                "min_x": 0,
                "max_x": 400,
                "min_y": 0,
                "max_y": 400,
            },
            tower_width=30,
            brim_width=3,
            edge_margin_mm=5,
            grid_size=3,
        )

        self.assertNotEqual(
            first,
            second,
        )


if __name__ == "__main__":
    unittest.main()
