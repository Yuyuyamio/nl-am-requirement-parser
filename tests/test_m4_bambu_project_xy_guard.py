import json
import tempfile
import unittest
import zipfile

from pathlib import Path
from unittest import mock

from am_print_executor.bambu_project_xy_guard import (
    ProjectXYPlacementError,
    ensure_project_xy_on_bed,
)


MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
<resources><object id="2" type="model"/></resources>
<build>
<item objectid="2"
 transform="1 0 0 0 1 0 0 0 1 0 0 0"
 printable="1"/>
</build>
</model>
"""


def make_project(path):
    settings = {
        "printable_area": [
            [0, 0],
            [256, 0],
            [256, 256],
            [0, 256],
        ]
    }

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps(settings),
        )
        zf.writestr(
            "3D/3dmodel.model",
            MODEL,
        )


class XYGuardTests(unittest.TestCase):

    def test_outside_is_recentred(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p.3mf"
            make_project(p)

            before = {
                "bounds_mm": [
                    [-40, -30, 0],
                    [40, 30, 100],
                ]
            }

            after = {
                "bounds_mm": [
                    [88, 98, 0],
                    [168, 158, 100],
                ]
            }

            with mock.patch(
                "am_print_executor.printability_gate."
                "inspect_geometry",
                side_effect=[before, after],
            ):
                result = ensure_project_xy_on_bed(p)

            self.assertTrue(result["changed"])
            self.assertAlmostEqual(
                result["translation_mm"][0],
                128.0,
            )
            self.assertAlmostEqual(
                result["translation_mm"][1],
                128.0,
            )

    def test_inside_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p.3mf"
            make_project(p)

            geometry = {
                "bounds_mm": [
                    [80, 80, 0],
                    [160, 160, 100],
                ]
            }

            with mock.patch(
                "am_print_executor.printability_gate."
                "inspect_geometry",
                return_value=geometry,
            ):
                result = ensure_project_xy_on_bed(p)

            self.assertFalse(result["changed"])

    def test_oversize_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p.3mf"
            make_project(p)

            geometry = {
                "bounds_mm": [
                    [0, 0, 0],
                    [300, 300, 100],
                ]
            }

            with mock.patch(
                "am_print_executor.printability_gate."
                "inspect_geometry",
                return_value=geometry,
            ):
                with self.assertRaises(
                    ProjectXYPlacementError
                ):
                    ensure_project_xy_on_bed(p)
