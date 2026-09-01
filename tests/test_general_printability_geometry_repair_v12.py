from __future__ import annotations

import tempfile
import unittest

from pathlib import Path

import trimesh

from am_print_executor.general_printability_geometry_repair import (
    build_printability_repair_request,
    plan_geometry_repair_candidates,
)


class GeneralRepairV12IntegrationTests(
    unittest.TestCase
):

    def test_facade_returns_serialisable_candidate_plans(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            mesh = trimesh.creation.box(
                extents=(
                    4.0,
                    4.0,
                    8.0,
                )
            )

            mesh.apply_translation(
                (
                    0.0,
                    0.0,
                    4.0,
                )
            )

            source = (
                root
                / "source.stl"
            )

            mesh.export(
                source
            )

            gate_report = {
                "status": "blocked",
                "blockers": [
                    "unsupported_extrusion_region"
                ],
                "dangerous_layer_count": 1,
                "total_bad_area_mm2": 0.16,
                "worst_bad_area_mm2": 0.16,
                "policy": {
                    "cell_mm": 0.4,
                    "line_width_mm": 0.4,
                    "configured_layer_height_mm": 0.2,
                    "measured_layer_height_mm": 0.2,
                    "self_support_xy_mm": 0.26,
                },
                "dangerous_layers": [
                    {
                        "z_mm": 5.0,
                        "issues": [
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.16,
                                "xy_bounds_mm":
                                    [[2.1, -0.2], [2.5, 0.2]],
                                "features":
                                    ["outer wall"],
                            }
                        ],
                    }
                ],
            }

            request = (
                build_printability_repair_request(
                    source_geometry=source,
                    gate_report=gate_report,
                )
            )

            plans = (
                plan_geometry_repair_candidates(
                    request
                )
            )

            self.assertEqual(
                len(plans),
                3,
            )

            self.assertTrue(
                all(
                    item[
                        "schema_version"
                    ]
                    == "gpr-v1.2"
                    for item in plans
                )
            )

            self.assertTrue(
                any(
                    item[
                        "status"
                    ]
                    == "candidate"
                    for item in plans
                )
            )


if __name__ == "__main__":
    unittest.main()
