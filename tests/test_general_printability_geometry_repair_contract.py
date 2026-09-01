from __future__ import annotations

import hashlib
import tempfile
import unittest

from pathlib import Path

import trimesh

from am_print_executor.general_printability_geometry_repair import (
    RepairBudget,
    build_printability_repair_request,
)


class GeneralPrintabilityRepairContractTests(
    unittest.TestCase
):

    def test_build_request_is_read_only_and_generic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            mesh = trimesh.creation.box(
                extents=(
                    10.0,
                    8.0,
                    12.0,
                )
            )

            mesh.apply_translation(
                (
                    0.0,
                    0.0,
                    6.0,
                )
            )

            geometry = (
                root
                / "source.stl"
            )

            mesh.export(
                geometry
            )

            before = hashlib.sha256(
                geometry.read_bytes()
            ).hexdigest()

            gate_report = {
                "status": "blocked",
                "blockers": [
                    "unsupported_extrusion_region:z=6.000"
                ],
                "dangerous_layer_count": 1,
                "total_bad_area_mm2": 0.16,
                "worst_bad_area_mm2": 0.16,
                "longest_bad_bridge_mm": 0.0,
                "policy": {
                    "cell_mm": 0.4,
                    "line_width_mm": 0.42,
                    "configured_layer_height_mm": 0.2,
                    "measured_layer_height_mm": 0.2,
                },
                "dangerous_layers": [
                    {
                        "z_mm": 6.0,
                        "issues": [
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.16,
                                "xy_bounds_mm":
                                    [[4.0, 1.0], [4.4, 1.4]],
                                "features":
                                    ["outer wall"],
                            }
                        ],
                    }
                ],
            }

            flat_base = {
                "base_flatness_passed": True,
                "bed_contact_area_mm2": 80.0,
                "contact_patch_count": 1,
                "maximum_plane_deviation_mm": 0.01,
            }

            request = (
                build_printability_repair_request(
                    source_geometry=geometry,
                    gate_report=gate_report,
                    flat_base_report=flat_base,
                    slicer="bambu",
                    critical_regions=[
                        {
                            "kind":
                                "recess",
                            "name":
                                "protected_test_recess",
                        }
                    ],
                )
            )

            after = hashlib.sha256(
                geometry.read_bytes()
            ).hexdigest()

            self.assertEqual(
                before,
                after,
            )

            self.assertEqual(
                request.schema_version,
                "gpr-v1.1",
            )

            self.assertEqual(
                request.module,
                "GENERAL_PRINTABILITY_GEOMETRY_REPAIR",
            )

            self.assertEqual(
                request.repair_mode,
                "deterministic_local_additive",
            )

            self.assertEqual(
                request.gate_status,
                "blocked",
            )

            self.assertEqual(
                len(
                    request.blocker_clusters
                ),
                1,
            )

            self.assertEqual(
                request.blocker_clusters[
                    0
                ].classification,
                "local_overhang",
            )

            self.assertTrue(
                request.protected_base[
                    "verified"
                ]
            )

            self.assertAlmostEqual(
                request.target_height_mm,
                12.0,
                places=4,
            )

            self.assertEqual(
                request.critical_regions[
                    0
                ][
                    "name"
                ],
                "protected_test_recess",
            )

            self.assertIn(
                "subtractive_overhang_cutter",
                request.forbidden_operations,
            )

            self.assertIn(
                "sample_specific_coordinate_patch",
                request.forbidden_operations,
            )

    def test_default_budget_matches_frozen_gpr_limits(self):
        budget = RepairBudget()

        self.assertEqual(
            budget.maximum_repair_rounds,
            2,
        )

        self.assertEqual(
            budget.maximum_candidates_per_round,
            3,
        )

        self.assertAlmostEqual(
            budget.maximum_modified_volume_ratio,
            0.05,
        )

        self.assertAlmostEqual(
            budget.maximum_bbox_dimension_change_ratio,
            0.03,
        )

        self.assertAlmostEqual(
            budget.maximum_height_change_ratio,
            0.005,
        )

        self.assertTrue(
            budget.additive_only
        )

        self.assertTrue(
            budget.preserve_flat_base
        )

        self.assertTrue(
            budget.preserve_critical_regions
        )


if __name__ == "__main__":
    unittest.main()
