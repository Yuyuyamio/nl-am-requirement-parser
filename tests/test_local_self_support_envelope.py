from __future__ import annotations

import hashlib
import tempfile
import unittest

from pathlib import Path

import trimesh

from am_print_executor.general_printability_geometry_repair import (
    build_printability_repair_request,
)
from am_print_executor.local_self_support_envelope import (
    build_envelope_mesh,
    plan_local_self_support_envelopes,
)


class LocalSelfSupportEnvelopeTests(
    unittest.TestCase
):

    @staticmethod
    def source_mesh(
        root: Path,
    ) -> Path:
        mesh = trimesh.creation.box(
            extents=(
                2.0,
                8.0,
                10.0,
            )
        )

        mesh.apply_translation(
            (
                0.0,
                0.0,
                5.0,
            )
        )

        path = (
            root
            / "body.stl"
        )

        mesh.export(
            path
        )

        return path

    @staticmethod
    def gate_report(
        *,
        x0=1.35,
        x1=1.75,
        z=6.0,
    ):
        return {
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
                    "z_mm": z,
                    "issues": [
                        {
                            "kind":
                                "unsupported_extrusion_region",
                            "area_mm2": 0.16,
                            "xy_bounds_mm": [
                                [x0, -0.2],
                                [x1, 0.2],
                            ],
                            "features": [
                                "outer wall"
                            ],
                        }
                    ],
                }
            ],
        }

    def test_local_overhang_gets_watertight_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            source = self.source_mesh(
                root
            )

            before = hashlib.sha256(
                source.read_bytes()
            ).hexdigest()

            request = (
                build_printability_repair_request(
                    source_geometry=source,
                    gate_report=self.gate_report(),
                    slicer="bambu",
                )
            )

            plans = (
                plan_local_self_support_envelopes(
                    request
                )
            )

            candidates = [
                plan
                for plan in plans
                if plan.status
                == "candidate"
            ]

            self.assertGreaterEqual(
                len(candidates),
                1,
            )

            mesh = build_envelope_mesh(
                candidates[0]
            )

            self.assertTrue(
                mesh.is_watertight
            )

            self.assertGreater(
                mesh.volume,
                0.0,
            )

            self.assertGreater(
                mesh.bounds[0][2],
                0.59,
            )

            after = hashlib.sha256(
                source.read_bytes()
            ).hexdigest()

            self.assertEqual(
                before,
                after,
            )

    def test_remote_defect_has_no_local_anchor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            source = self.source_mesh(
                root
            )

            request = (
                build_printability_repair_request(
                    source_geometry=source,
                    gate_report=self.gate_report(
                        x0=8.0,
                        x1=8.4,
                    ),
                    slicer="bambu",
                )
            )

            plans = (
                plan_local_self_support_envelopes(
                    request
                )
            )

            self.assertTrue(
                all(
                    plan.status
                    == "rejected"
                    for plan in plans
                )
            )

            reasons = {
                plan.rejection_reason
                for plan in plans
            }

            self.assertIn(
                "no_local_source_anchor_within_depth_and_slope",
                reasons,
            )

    def test_near_base_cluster_is_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            source = self.source_mesh(
                root
            )

            request = (
                build_printability_repair_request(
                    source_geometry=source,
                    gate_report=self.gate_report(
                        z=0.8,
                    ),
                    slicer="bambu",
                )
            )

            plans = (
                plan_local_self_support_envelopes(
                    request
                )
            )

            self.assertTrue(
                all(
                    plan.status
                    == "rejected"
                    for plan in plans
                )
            )

            self.assertTrue(
                all(
                    plan.rejection_reason
                    == "too_close_to_protected_base"
                    for plan in plans
                )
            )


if __name__ == "__main__":
    unittest.main()
