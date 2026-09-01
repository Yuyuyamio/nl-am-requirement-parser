from __future__ import annotations

import unittest

from am_print_executor.r5_printability_v2 import (
    prime_tower_geometry,
    r4_authoritative_prime_tower_evidence,
)


class R5PrintabilityV21Tests(unittest.TestCase):
    def setUp(self):
        self.bed = {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": 256.0,
            "max_y": 256.0,
        }

    def test_210_11_is_not_treated_as_centered_square(self):
        result = prime_tower_geometry(
            project_settings={
                "wipe_tower_x": 210.0,
                "wipe_tower_y": 11.0,
                "prime_tower_width": 35.0,
            },
            selected={
                "candidate_index": 3,
                "wipe_tower_x": 210.0,
                "wipe_tower_y": 11.0,
            },
            bed=self.bed,
            model_bounds=[[80, 80, 0], [170, 170, 100]],
        )

        self.assertEqual(result["x_span_mm"], [210.0, 245.0])
        self.assertTrue(result["x_span_inside_printable_bbox"])
        self.assertTrue(result["y_reference_inside_printable_bbox"])
        self.assertTrue(result["inside_printable_bbox"])

    def test_x_translation_plus_width_must_fit(self):
        result = prime_tower_geometry(
            project_settings={
                "wipe_tower_x": 230.0,
                "wipe_tower_y": 11.0,
                "prime_tower_width": 35.0,
            },
            selected={
                "candidate_index": 1,
                "wipe_tower_x": 230.0,
                "wipe_tower_y": 11.0,
            },
            bed=self.bed,
            model_bounds=[[80, 80, 0], [170, 170, 100]],
        )

        self.assertFalse(result["x_span_inside_printable_bbox"])
        self.assertFalse(result["inside_printable_bbox"])

    def test_r4_matching_successful_attempt_is_authoritative(self):
        result = r4_authoritative_prime_tower_evidence(
            {
                "status": "prime_tower_safe_position_resolved",
                "attempts": [
                    {
                        "candidate_index": 3,
                        "slice_succeeded": True,
                        "exit": {"signed": 0},
                    }
                ],
            },
            {
                "candidate_index": 3,
                "wipe_tower_x": 210.0,
                "wipe_tower_y": 11.0,
            },
        )

        self.assertTrue(
            result["authoritative_bambu_cli_validation_pass"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
