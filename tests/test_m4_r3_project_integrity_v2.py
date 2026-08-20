from __future__ import annotations

import tempfile
import unittest
import zipfile

from pathlib import Path

from am_print_executor.r3_project_integrity_v2 import (
    LEGACY_MARKERS,
    require_members,
    scan_legacy_markers,
)


class R3ProjectIntegrityV2Tests(unittest.TestCase):
    def test_require_members_reports_missing(self):
        info = {
            "members": [
                "Metadata/project_settings.config",
            ]
        }

        result = require_members(
            info,
            required=[
                "Metadata/project_settings.config",
                "Metadata/slice_info.config",
            ],
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["missing"],
            ["Metadata/slice_info.config"],
        )

    def test_legacy_marker_scan_blocks_old_region_reference(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.3mf"

            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr(
                    "Metadata/model_settings.config",
                    "<metadata value='region_01.stl'/>",
                )

            result = scan_legacy_markers(path)

            self.assertFalse(result["passed"])
            self.assertEqual(
                result["hits"][0]["marker"],
                "region_01.stl",
            )

    def test_legacy_marker_scan_accepts_clean_project(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.3mf"

            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr(
                    "Metadata/model_settings.config",
                    "<metadata value='dog_surface_painted.stl'/>",
                )

            result = scan_legacy_markers(path)

            self.assertTrue(result["passed"])
            self.assertEqual(result["hits"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
