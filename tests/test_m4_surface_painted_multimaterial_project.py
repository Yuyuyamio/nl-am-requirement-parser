from __future__ import annotations

import unittest

import numpy as np

from am_print_executor.surface_painted_multimaterial_project import (
    encode_whole_triangle_paint,
    select_dorsal_accent_triangles,
    validate_semantic_rule,
)


class SurfacePaintTests(unittest.TestCase):
    def test_dual_material_bambu_codes(self):
        self.assertEqual(encode_whole_triangle_paint(1), "4")
        self.assertEqual(encode_whole_triangle_paint(2), "8")

    def test_rule_normalises_weights(self):
        rule = validate_semantic_rule(
            {
                "semantic_type": "surface_paint",
                "rule": "dorsal_accent_v1",
                "base_filament_id": 1,
                "accent_filament_id": 2,
                "accent_fraction": 0.18,
                "score": {
                    "world_z_weight": 3,
                    "upward_normal_weight": 1,
                },
            }
        )

        self.assertAlmostEqual(
            rule["score"]["world_z_weight"],
            0.75,
        )
        self.assertAlmostEqual(
            rule["score"]["upward_normal_weight"],
            0.25,
        )

    def test_selector_paints_requested_fraction(self):
        vertices = np.array(
            [
                [0, 0, 0],
                [1, 0, 0],
                [0, 1, 0],
                [0, 0, 1],
                [1, 0, 1],
                [0, 1, 1],
            ],
            dtype=float,
        )
        faces = np.array(
            [
                [0, 1, 2],
                [3, 4, 5],
                [0, 1, 3],
                [1, 4, 3],
                [0, 2, 3],
                [2, 5, 3],
            ],
            dtype=int,
        )

        result = select_dorsal_accent_triangles(
            vertices_world=vertices,
            faces=faces,
            accent_fraction=0.20,
            world_z_weight=0.75,
            upward_normal_weight=0.25,
        )

        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(len(result["selected_indices"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
