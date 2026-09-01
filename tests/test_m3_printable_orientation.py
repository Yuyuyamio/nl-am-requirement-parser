import json
import tempfile
import unittest

from pathlib import Path

import numpy as np
import trimesh

from am_print_executor.printable_orientation import (
    orient_for_printing,
)


class PrintableOrientationTests(
    unittest.TestCase
):

    def test_rotated_box_gets_valid_bed_pose(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            mesh = trimesh.creation.box(
                extents=[10.0, 20.0, 30.0]
            )

            transform = (
                trimesh.transformations.rotation_matrix(
                    np.deg2rad(37.0),
                    [1.0, 1.0, 0.3],
                )
            )

            mesh.apply_transform(transform)

            source = root / "source.stl"
            output = root / "oriented.stl"
            report = root / "report.json"

            mesh.export(source)

            result = orient_for_printing(
                input_path=source,
                output_path=output,
                report_path=report,
                preserve_upright=False,  # This test explicitly requests arbitrary mechanical reorientation.
            )

            self.assertEqual(
                result["status"],
                "printable_orientation_pass",
            )

            self.assertTrue(
                output.is_file()
            )

            selected = result["selected"]

            self.assertAlmostEqual(
                selected["z_min_mm"],
                0.0,
                places=5,
            )

            self.assertTrue(
                selected["bed_fit"]
            )

            self.assertTrue(
                selected["contact_pass"]
            )

            self.assertEqual(
                selected["blockers"],
                [],
            )


if __name__ == "__main__":
    unittest.main()
