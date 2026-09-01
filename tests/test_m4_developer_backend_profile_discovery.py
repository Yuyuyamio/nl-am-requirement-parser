from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from am_print_executor import developer_mode_backend_v1120 as backend


class DeveloperBackendProfileDiscoveryTests(unittest.TestCase):

    def test_project_root_environment_override(self):
        with tempfile.TemporaryDirectory() as td:
            expected = Path(td).resolve()

            with mock.patch.dict(
                os.environ,
                {"NL_AM_PROJECT_ROOT": str(expected)},
                clear=False,
            ):
                observed = backend._resolve_project_root()

            self.assertEqual(observed, expected)

    def test_project_root_falls_back_to_repository(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            observed = backend._resolve_project_root()

        expected = Path(backend.__file__).resolve().parents[2]
        self.assertEqual(observed, expected)

    def test_discovers_x1c_profiles_from_studio_install(self):
        with tempfile.TemporaryDirectory() as td:
            studio_dir = Path(td) / "Bambu Studio"
            studio_dir.mkdir(parents=True)

            studio = studio_dir / "bambu-studio.exe"
            studio.write_bytes(b"")

            root = (
                studio_dir
                / "resources"
                / "profiles"
                / "BBL"
            )

            machine = (
                root
                / "machine"
                / "Bambu Lab X1 Carbon 0.4 nozzle.json"
            )

            process = (
                root
                / "process"
                / "0.20mm Standard @BBL X1C.json"
            )

            preferred_filament = (
                root
                / "filament"
                / "Bambu PLA Basic @BBL X1C.json"
            )

            fallback_filament = (
                root
                / "filament"
                / "Generic PLA @base.json"
            )

            for path in (
                machine,
                process,
                preferred_filament,
                fallback_filament,
            ):
                path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                path.write_text(
                    "{}",
                    encoding="utf-8",
                )

            result = backend.discover_bambu_profiles(studio)

            self.assertEqual(
                result["machine"],
                machine.resolve(),
            )

            self.assertEqual(
                result["process"],
                process.resolve(),
            )

            self.assertEqual(
                result["filament"],
                preferred_filament.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
