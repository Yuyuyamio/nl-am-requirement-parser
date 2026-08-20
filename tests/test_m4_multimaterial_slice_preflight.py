import json
import tempfile
import unittest
import zipfile

from pathlib import Path

from am_print_executor.multimaterial_slice_preflight import (
    classify_ab_result,
    classify_bambu_exit_code,
    signed_windows_exit_code,
)


class MultimaterialSlicePreflightTests(
    unittest.TestCase
):

    def test_windows_unsigned_minus_104(
        self,
    ):
        self.assertEqual(
            signed_windows_exit_code(
                4294967192
            ),
            -104,
        )


    def test_minus_104_symbol(
        self,
    ):
        result = (
            classify_bambu_exit_code(
                4294967192
            )
        )

        self.assertEqual(
            result["signed"],
            -104,
        )

        self.assertEqual(
            result["symbol"],
            "CLI_GCODE_PATH_OUTSIDE",
        )


    def test_direct_pass_isolates_override_path(
        self,
    ):
        result = classify_ab_result(
            override_exit=-104,
            direct_result={
                "exit": {
                    "raw": 0,
                    "signed": 0,
                    "symbol":
                        "CLI_SUCCESS",
                },
                "direct_slice_succeeded":
                    True,
            },
        )

        self.assertEqual(
            result["diagnosis"],
            "override_profile_path_regression",
        )


    def test_direct_minus_104_points_to_project_path(
        self,
    ):
        result = classify_ab_result(
            override_exit=-104,
            direct_result={
                "exit": {
                    "raw":
                        4294967192,

                    "signed":
                        -104,

                    "symbol":
                        "CLI_GCODE_PATH_OUTSIDE",
                },

                "direct_slice_succeeded":
                    False,
            },
        )

        self.assertEqual(
            result["diagnosis"],
            (
                "project_layout_or_generated_"
                "path_outside"
            ),
        )


    def test_different_direct_failure_stays_distinct(
        self,
    ):
        result = classify_ab_result(
            override_exit=-104,
            direct_result={
                "exit": {
                    "raw": -66,
                    "signed": -66,
                    "symbol":
                        "CLI_FILAMENT_CAN_NOT_MAP",
                },
                "direct_slice_succeeded":
                    False,
            },
        )

        self.assertEqual(
            result["diagnosis"],
            "different_direct_slice_failure",
        )


if __name__ == "__main__":
    unittest.main()
