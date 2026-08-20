import json
import tempfile
import unittest
import zipfile

from pathlib import Path

from am_print_executor.multimaterial_toolchange_validator import (
    validate_multimaterial_gcode,
)


class MultimaterialToolchangeValidatorTests(
    unittest.TestCase
):

    def make_artifact(
        self,
        root: Path,
        gcode: str,
        filament_count: int = 2,
    ) -> Path:

        path = root / "test.gcode.3mf"

        settings = {
            "filament_settings_id": [
                f"Material {index}"
                for index in range(
                    filament_count
                )
            ],

            "filament_colour": [
                f"#{index:06X}"
                for index in range(
                    filament_count
                )
            ],
        }

        with zipfile.ZipFile(
            path,
            "w",
        ) as zf:

            zf.writestr(
                "Metadata/project_settings.config",
                json.dumps(settings),
            )

            zf.writestr(
                "Metadata/plate_1.gcode",
                gcode,
            )

        return path


    def test_real_two_material_commands_pass(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            artifact = self.make_artifact(
                root,
                """
M620 S0A
T0
G1 X10 Y10
M620 S1A
T1
G1 X20 Y20
M620 S0A
T0
""",
            )

            result = (
                validate_multimaterial_gcode(
                    artifact
                )
            )

            self.assertTrue(
                result["passed"]
            )

            self.assertEqual(
                result[
                    "distinct_expected_tools_seen"
                ],
                [0, 1],
            )


    def test_metadata_only_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            artifact = self.make_artifact(
                root,
                """
T0
M620 S0A
G1 X10 Y10
""",
            )

            result = (
                validate_multimaterial_gcode(
                    artifact
                )
            )

            self.assertFalse(
                result["passed"]
            )

            self.assertIn(
                1,
                result[
                    "missing_tool_commands"
                ],
            )


    def test_missing_m620_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            artifact = self.make_artifact(
                root,
                """
T0
T1
""",
            )

            result = (
                validate_multimaterial_gcode(
                    artifact
                )
            )

            self.assertFalse(
                result["passed"]
            )


    def test_three_material_generic_case(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            artifact = self.make_artifact(
                root,
                """
M620 S0A
T0
M620 S1A
T1
M620 S2A
T2
""",
                filament_count=3,
            )

            result = (
                validate_multimaterial_gcode(
                    artifact
                )
            )

            self.assertTrue(
                result["passed"]
            )

            self.assertEqual(
                result[
                    "expected_tool_ids"
                ],
                [0, 1, 2],
            )


if __name__ == "__main__":
    unittest.main()
