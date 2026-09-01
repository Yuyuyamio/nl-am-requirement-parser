import tempfile
import unittest
from pathlib import Path

import trimesh

from am_print_executor.manifold_repair import (
    load_triangle_mesh,
    topology_stats,
)


class StlTopologyNormalizationTests(unittest.TestCase):

    def test_exported_watertight_stl_remains_watertight_after_normalized_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cube.stl"

            mesh = trimesh.creation.box(
                extents=[10.0, 8.0, 6.0]
            )

            mesh.export(path)

            loaded = load_triangle_mesh(path)
            stats = topology_stats(loaded)

            self.assertTrue(stats["watertight"])
            self.assertEqual(stats["boundary_edges"], 0)
            self.assertEqual(stats["nonmanifold_edges"], 0)
            self.assertEqual(len(loaded.vertices), 8)


if __name__ == "__main__":
    unittest.main()
