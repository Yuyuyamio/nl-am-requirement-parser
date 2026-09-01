import tempfile
import unittest
import zipfile

from pathlib import Path

import trimesh

from am_print_executor.printability_support_enforcer import (
    build_support_enforcer_tracks,
    write_support_enforcer_from_printability_report,
)


class PrintabilitySupportEnforcerTests(
    unittest.TestCase
):

    def _report(self):
        return {
            "policy": {
                "gate_semantics":
                    "layer_support_connectivity_v2",

                "measured_layer_height_mm":
                    0.2,
            },

            "dangerous_layers": [
                {
                    "z_mm": 8.4,
                    "issues": [
                        {
                            "kind":
                                "unsupported_extrusion_region",

                            "area_mm2":
                                0.32,

                            "xy_bounds_mm": [
                                [2.0, 3.0],
                                [3.2, 4.4],
                            ],
                        },
                        {
                            "kind":
                                "unsafe_bridge",

                            "area_mm2":
                                0.16,

                            "span_mm":
                                6.0,

                            "anchored_contact_count":
                                1,

                            "xy_bounds_mm": [
                                [5.0, 6.0],
                                [6.0, 7.0],
                            ],
                        },
                    ],
                }
            ],
        }

    def test_new_gate_feedback_maps_to_tracks(
        self,
    ):
        mapped = (
            build_support_enforcer_tracks(
                self._report()
            )
        )

        self.assertEqual(
            len(mapped["tracks"]),
            2,
        )

        self.assertEqual(
            mapped["tracks"][0][
                "source_kind"
            ],
            "unsupported_extrusion_region",
        )

        self.assertEqual(
            mapped["tracks"][0][
                "bbox_mm"
            ],
            [2.0, 3.0, 3.2, 4.4],
        )

        self.assertEqual(
            mapped["tracks"][1][
                "anchored_contact_count"
            ],
            1,
        )

    def test_project_contains_gate_directed_enforcers(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            model = (
                root
                / "model.stl"
            )

            mesh = trimesh.creation.box(
                extents=[
                    20.0,
                    20.0,
                    20.0,
                ]
            )

            mesh.apply_translation(
                [10.0, 10.0, 10.0]
            )

            mesh.export(model)

            output = (
                root
                / "gate_support.3mf"
            )

            result = (
                write_support_enforcer_from_printability_report(
                    model_path=model,
                    printability_report=self._report(),
                    output_path=output,
                )
            )

            self.assertTrue(
                output.is_file()
            )

            self.assertEqual(
                result[
                    "source_issue_count"
                ],
                2,
            )

            self.assertGreaterEqual(
                result[
                    "enforcer_count"
                ],
                2,
            )

            with zipfile.ZipFile(
                output,
                "r",
            ) as archive:
                config = archive.read(
                    "Metadata/"
                    "Slic3r_PE_model.config"
                ).decode(
                    "utf-8"
                )

            self.assertIn(
                'value="support_enforcer"',
                config,
            )


if __name__ == "__main__":
    unittest.main()