from __future__ import annotations

import unittest

import numpy as np
import trimesh

from am_print_executor.rigid_multimaterial_project import (
    classify_partition_policy,
    pairwise_centroid_signature,
)


class RigidMultiMaterialTests(unittest.TestCase):
    def test_ratio_partition_is_not_implicitly_production_semantics(self):
        result = classify_partition_policy(
            {"partition": {"mode": "ratio", "cut_ratios": [0.6]}}
        )
        self.assertTrue(result["synthetic_geometric_partition"])
        self.assertFalse(result["explicit_material_semantics"])

    def test_explicit_material_semantics_can_authorize_partition(self):
        result = classify_partition_policy(
            {
                "partition": {
                    "mode": "ratio",
                    "intent": "explicit_geometric_material_partition",
                }
            }
        )
        self.assertFalse(result["synthetic_geometric_partition"])
        self.assertTrue(result["explicit_material_semantics"])

    def test_pairwise_centroid_signature_ignores_common_translation(self):
        a = trimesh.creation.box(extents=(1, 1, 1))
        b = trimesh.creation.box(extents=(1, 1, 1))
        b.apply_translation((10, 3, 2))

        before = pairwise_centroid_signature([a, b])

        a2 = a.copy()
        b2 = b.copy()

        transform = np.eye(4)
        transform[:3, :3] = np.array(
            [
                [0.0, -1.0, 0.0],
                [1.0,  0.0, 0.0],
                [0.0,  0.0, 1.0],
            ]
        )
        a2.apply_transform(transform)
        b2.apply_transform(transform)
        a2.apply_translation((50, 60, 70))
        b2.apply_translation((50, 60, 70))

        after = pairwise_centroid_signature([a2, b2])

        self.assertAlmostEqual(before[0], after[0], places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
