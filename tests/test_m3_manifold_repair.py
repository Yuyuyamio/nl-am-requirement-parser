import tempfile
import unittest
from pathlib import Path

import trimesh

from am_print_executor.manifold_repair import (
    repair_manifold,
)


class ManifoldRepairTests(
    unittest.TestCase
):
    def test_repairs_open_mesh_and_preserves_size(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)

            cube = (
                trimesh.creation.box(
                    extents=[
                        12.0,
                        10.0,
                        8.0,
                    ]
                )
            )

            # Remove one triangle to create
            # a deliberately open mesh.
            cube.update_faces(
                list(
                    range(
                        len(
                            cube.faces
                        ) - 1
                    )
                )
            )

            source = (
                d
                / "open.stl"
            )

            output = (
                d
                / "fixed.stl"
            )

            cube.export(
                source
            )

            result = (
                repair_manifold(
                    input_path=source,
                    output_path=output,
                )
            )

            self.assertEqual(
                result["status"],
                "complete",
            )

            self.assertTrue(
                output.is_file()
            )

            self.assertTrue(
                result[
                    "final"
                ][
                    "watertight"
                ]
            )

            self.assertEqual(
                result[
                    "final"
                ][
                    "boundary_edges"
                ],
                0,
            )

            self.assertEqual(
                result[
                    "final"
                ][
                    "nonmanifold_edges"
                ],
                0,
            )

            self.assertLess(
                result[
                    "maximum_extent_error_mm"
                ],
                0.02,
            )


if __name__ == "__main__":
    unittest.main()
