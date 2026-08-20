import unittest

from am_print_executor.bambu_cli_exit_codes import (
    normalize_bambu_cli_exit_result,
)


class BambuExitCodeTests(unittest.TestCase):

    def test_minus_64_is_explained(self):
        result = {
            "exit": {
                "signed": -64,
                "symbol": "UNKNOWN_BAMBU_CLI_EXIT",
            }
        }

        normalize_bambu_cli_exit_result(
            result
        )

        self.assertEqual(
            result["exit"]["symbol"],
            "CLI_OBJECT_COLLISION_IN_LAYER_PRINT",
        )

        self.assertIn(
            "??",
            result["exit"]["reason_zh"],
        )

    def test_minus_104_preserves_known_symbol(self):
        result = {
            "exit": {
                "signed": -104,
                "symbol": "CLI_GCODE_PATH_OUTSIDE",
            }
        }

        normalize_bambu_cli_exit_result(
            result
        )

        self.assertEqual(
            result["exit"]["symbol"],
            "CLI_GCODE_PATH_OUTSIDE",
        )

        self.assertIn(
            "????",
            result["exit"]["reason_zh"],
        )
