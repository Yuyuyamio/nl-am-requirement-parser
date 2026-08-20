import json
import tempfile
import unittest
import zipfile

from pathlib import Path

import am_print_executor.x1c_preprint_dry_run as d


class X1CPreprintDryRunTests(
    unittest.TestCase
):

    def make_artifact(
        self,
        root: Path,
        bed: str = "Textured PEI Plate",
    ) -> Path:

        path = (
            root
            / "job.gcode.3mf"
        )

        with zipfile.ZipFile(
            path,
            "w",
        ) as zf:
            zf.writestr(
                "Metadata/"
                "project_settings.config",
                json.dumps(
                    {
                        "curr_bed_type":
                            bed,
                    }
                ),
            )

            zf.writestr(
                "Metadata/plate_1.gcode",
                "T0\nM620 S0A\nT1\nM620 S1A\n",
            )

        return path


    def make_toolpath(
        self,
        root: Path,
    ) -> Path:

        path = (
            root
            / "toolpath.json"
        )

        path.write_text(
            json.dumps(
                {
                    "status":
                        "multimaterial_toolchange_validated",
                    "project_filament_count":
                        2,
                    "expected_tool_ids":
                        [0, 1],
                    "missing_m620_commands":
                        [],
                    "missing_tool_commands":
                        [],
                    "real_tool_transition_observed":
                        True,
                }
            ),
            encoding="utf-8",
        )

        return path


    def make_ams(
        self,
        root: Path,
        blockers=None,
    ) -> Path:

        path = (
            root
            / "ams.json"
        )

        path.write_text(
            json.dumps(
                {
                    "status":
                        "ams_mapping_resolved",

                    "inventory": {
                        "slots": [
                            {
                                "display_slot":
                                    "A1",
                                "wire_slot":
                                    0,
                            },
                            {
                                "display_slot":
                                    "A4",
                                "wire_slot":
                                    3,
                            },
                            {
                                "display_slot":
                                    "virtual",
                                "wire_slot":
                                    512,
                            },
                        ],
                    },

                    "resolution": {
                        "ams_mapping_logical":
                            [0, 3],
                        "blockers":
                            (
                                []
                                if blockers is None
                                else blockers
                            ),
                    },
                }
            ),
            encoding="utf-8",
        )

        return path


    def test_pass_builds_current_sha_and_wire_mapping(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            artifact = self.make_artifact(
                root
            )

            result = (
                d.build_preprint_report(
                    artifact=artifact,
                    toolpath_report=
                        self.make_toolpath(
                            root
                        ),
                    ams_report=
                        self.make_ams(
                            root
                        ),
                    build_plate=
                        "Textured PEI Plate",
                )
            )

            self.assertEqual(
                result["status"],
                "x1c_preprint_dry_run_pass",
            )

            self.assertEqual(
                result["ams"][
                    "logical_mapping"
                ],
                [0, 3],
            )

            self.assertEqual(
                result["ams"][
                    "x1c_wire_mapping"
                ],
                [0, 3, -1, -1, -1],
            )

            self.assertEqual(
                [
                    row["wire_slot"]
                    for row
                    in result["ams"][
                        "selected_inventory"
                    ]
                ],
                [0, 3],
            )


    def test_wrong_build_plate_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            with self.assertRaises(
                d.X1CPreprintDryRunError
            ):
                d.build_preprint_report(
                    artifact=
                        self.make_artifact(
                            root,
                            bed="Cool Plate",
                        ),
                    toolpath_report=
                        self.make_toolpath(
                            root
                        ),
                    ams_report=
                        self.make_ams(
                            root
                        ),
                    build_plate=
                        "Textured PEI Plate",
                )


    def test_ams_blocker_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            with self.assertRaises(
                d.X1CPreprintDryRunError
            ):
                d.build_preprint_report(
                    artifact=
                        self.make_artifact(
                            root
                        ),
                    toolpath_report=
                        self.make_toolpath(
                            root
                        ),
                    ams_report=
                        self.make_ams(
                            root,
                            blockers=[
                                "ambiguous"
                            ],
                        ),
                    build_plate=
                        "Textured PEI Plate",
                )


    def test_virtual_512_never_selected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            result = (
                d.build_preprint_report(
                    artifact=
                        self.make_artifact(
                            root
                        ),
                    toolpath_report=
                        self.make_toolpath(
                            root
                        ),
                    ams_report=
                        self.make_ams(
                            root
                        ),
                    build_plate=
                        "Textured PEI Plate",
                )
            )

            wires = [
                row["wire_slot"]
                for row
                in result["ams"][
                    "selected_inventory"
                ]
            ]

            self.assertNotIn(
                512,
                wires,
            )


if __name__ == "__main__":
    unittest.main()
