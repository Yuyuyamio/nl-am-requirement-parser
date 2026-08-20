import tempfile
import unittest
from pathlib import Path

from am_print_executor.m3_manufacturability_pipeline import (
    inspect_gcode_3mf,
)


class M3ManufacturabilityPipelineTests(
    unittest.TestCase
):

    def test_missing_artifact_blocks_cleanly(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            missing = (
                Path(td)
                / "does_not_exist.gcode.3mf"
            )

            result = inspect_gcode_3mf(
                missing,
                support_required=True,
            )

            self.assertEqual(
                result["status"],
                "blocked",
            )

            self.assertIn(
                "slice_artifact_missing",
                result["blockers"],
            )


if __name__ == "__main__":
    unittest.main()
