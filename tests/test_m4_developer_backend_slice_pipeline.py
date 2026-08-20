from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import am_print_executor.developer_mode_backend_v1120 as backend


class BambuSlicePipelineTests(unittest.TestCase):

    def _files(self, root: Path):
        studio = root / "bambu-studio.exe"
        studio.write_bytes(b"fake")

        machine = root / "machine.json"
        process = root / "process.json"
        filament = root / "filament.json"

        for path in (machine, process, filament):
            path.write_text("{}", encoding="utf-8")

        return studio, machine, process, filament

    @staticmethod
    def _resolved(machine, process, filament):
        return {
            "machine": machine,
            "process": process,
            "filaments": [filament],
        }

    def test_stl_uses_one_authoritative_cli_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, machine, process, filament = self._files(root)

            source = root / "model.stl"
            source.write_bytes(b"solid x\nendsolid x\n")

            output = root / "result.gcode.3mf"
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(list(cmd))
                export_index = cmd.index("--export-3mf")
                Path(cmd[export_index + 1]).write_bytes(b"fake")

                return subprocess.CompletedProcess(
                    cmd, 0, stdout="ok", stderr=""
                )

            with (
                mock.patch.object(
                    backend,
                    "materialize_bambu_cli_profiles",
                    return_value=self._resolved(
                        machine, process, filament
                    ),
                ),
                mock.patch.object(
                    backend.subprocess,
                    "run",
                    side_effect=fake_run,
                ),
                mock.patch.object(
                    backend,
                    "inspect_gcode_3mf",
                    return_value={
                        "path": str(output),
                        "plate_count": 1,
                        "gcode_entries": [
                            "Metadata/plate_1.gcode"
                        ],
                    },
                ),
            ):
                result = backend.slice_with_bambu_cli(
                    source,
                    output,
                    studio_exe=studio,
                    machine_json=machine,
                    process_json=process,
                    filament_jsons=[filament],
                )

            self.assertEqual(len(calls), 1)

            cmd = calls[0]

            orient = cmd.index("--orient")
            self.assertEqual(cmd[orient + 1], "1")

            arrange = cmd.index("--arrange")
            self.assertEqual(cmd[arrange + 1], "1")

            self.assertIn("--ensure-on-bed", cmd)
            self.assertIn("--load-settings", cmd)
            self.assertIn("--load-filaments", cmd)

            slice_index = cmd.index("--slice")
            self.assertEqual(cmd[slice_index + 1], "0")

            export_index = cmd.index("--export-3mf")
            self.assertEqual(
                Path(cmd[export_index + 1]),
                output.resolve(),
            )

            self.assertEqual(
                Path(cmd[-1]),
                source.resolve(),
            )

            self.assertEqual(
                result["pipeline"],
                "bambu_single_stage_cli_v1",
            )

    def test_3mf_does_not_get_auto_orient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, machine, process, filament = self._files(root)

            source = root / "input.3mf"
            source.write_bytes(b"fake")
            output = root / "result.gcode.3mf"

            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(list(cmd))
                export_index = cmd.index("--export-3mf")
                Path(cmd[export_index + 1]).write_bytes(b"fake")

                return subprocess.CompletedProcess(
                    cmd, 0, stdout="ok", stderr=""
                )

            with (
                mock.patch.object(
                    backend,
                    "materialize_bambu_cli_profiles",
                    return_value=self._resolved(
                        machine, process, filament
                    ),
                ),
                mock.patch.object(
                    backend.subprocess,
                    "run",
                    side_effect=fake_run,
                ),
                mock.patch.object(
                    backend,
                    "inspect_gcode_3mf",
                    return_value={
                        "path": str(output),
                        "plate_count": 1,
                        "gcode_entries": [
                            "Metadata/plate_1.gcode"
                        ],
                    },
                ),
            ):
                backend.slice_with_bambu_cli(
                    source,
                    output,
                    studio_exe=studio,
                    machine_json=machine,
                    process_json=process,
                    filament_jsons=[filament],
                )

            self.assertEqual(len(calls), 1)

            cmd = calls[0]

            self.assertNotIn("--orient", cmd)
            self.assertNotIn("--arrange", cmd)
            self.assertNotIn("--ensure-on-bed", cmd)

    def test_windows_unsigned_returncode_is_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, machine, process, filament = self._files(root)

            source = root / "model.stl"
            source.write_bytes(b"x")
            output = root / "result.gcode.3mf"

            with (
                mock.patch.object(
                    backend,
                    "materialize_bambu_cli_profiles",
                    return_value=self._resolved(
                        machine, process, filament
                    ),
                ),
                mock.patch.object(
                    backend.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [],
                        4294967290,
                        stdout="",
                        stderr="failure",
                    ),
                ),
            ):
                with self.assertRaises(
                    backend.DeveloperBackendError
                ) as ctx:
                    backend.slice_with_bambu_cli(
                        source,
                        output,
                        studio_exe=studio,
                        machine_json=machine,
                        process_json=process,
                        filament_jsons=[filament],
                    )

            message = str(ctx.exception)

            self.assertIn(
                "returncode_raw=4294967290",
                message,
            )
            self.assertIn(
                "returncode_signed=-6",
                message,
            )

    def test_stale_neighbor_output_is_never_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, machine, process, filament = self._files(root)

            source = root / "model.stl"
            source.write_bytes(b"x")

            output = root / "wanted.gcode.3mf"

            # Existing unrelated artifact must never satisfy this run.
            (root / "old.gcode.3mf").write_bytes(b"old")

            with (
                mock.patch.object(
                    backend,
                    "materialize_bambu_cli_profiles",
                    return_value=self._resolved(
                        machine, process, filament
                    ),
                ),
                mock.patch.object(
                    backend.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [],
                        0,
                        stdout="",
                        stderr="",
                    ),
                ),
            ):
                with self.assertRaises(
                    backend.DeveloperBackendError
                ):
                    backend.slice_with_bambu_cli(
                        source,
                        output,
                        studio_exe=studio,
                        machine_json=machine,
                        process_json=process,
                        filament_jsons=[filament],
                    )


if __name__ == "__main__":
    unittest.main()
