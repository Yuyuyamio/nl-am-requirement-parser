from __future__ import annotations

import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import am_print_executor.developer_mode_backend_v1120 as backend


def _cli_result(
    command,
    *,
    signed_exit=0,
    output_exists=True,
):
    raw_exit = (
        signed_exit + 2**32
        if signed_exit < 0
        else signed_exit
    )

    return SimpleNamespace(
        command=tuple(command),
        raw_exit=raw_exit,
        signed_exit=signed_exit,
        stdout="",
        stderr="failure" if signed_exit else "",
        outputs_exist=output_exists,
        success=(signed_exit == 0 and output_exists),
        lock_wait_seconds=0.0,
        process_settle_seconds=0.0,
    )


class BambuSlicePipelineTests(unittest.TestCase):
    def test_live_printer_identity_replaces_legacy_gate_file_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            connection = {
                "connected": True,
                "authenticated": True,
                "printer_state_observed": True,
                "credential_available": True,
                "printer_ip": "172.16.61.6",
                "device_id": backend.EXPECTED_DEVICE_ID,
                "tls_certificate_sha256": "AB" * 32,
            }

            identity = backend._live_identity_from_connection(
                connection,
                project_root=root,
                expected_device_id=backend.EXPECTED_DEVICE_ID,
            )

            self.assertEqual(identity["ip_address"], "172.16.61.6")
            self.assertEqual(
                identity["identity_source"],
                "authenticated_printer_connection",
            )
            self.assertEqual(
                identity["task_dir"],
                root
                / "outputs"
                / "printer_connections"
                / backend.EXPECTED_DEVICE_ID,
            )

    def test_live_printer_identity_requires_current_authenticated_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = {
                "connected": True,
                "authenticated": False,
                "printer_state_observed": True,
                "credential_available": True,
                "printer_ip": "172.16.61.6",
                "device_id": backend.EXPECTED_DEVICE_ID,
                "tls_certificate_sha256": "AB" * 32,
            }
            with self.assertRaises(backend.DeveloperBackendError):
                backend._live_identity_from_connection(
                    connection,
                    project_root=Path(tmp),
                    expected_device_id=backend.EXPECTED_DEVICE_ID,
                )

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

    @staticmethod
    def _auto_orient(**kwargs):
        output = Path(kwargs["output_path"])
        output.write_bytes(b"project")

        return {
            "status": "auto_orient_complete",
            "attempt_count": 1,
            "output": {"path": str(output)},
        }

    @staticmethod
    def _inspection(output):
        return {
            "path": str(output),
            "plate_count": 1,
            "gcode_entries": [
                "Metadata/plate_1.gcode"
            ],
        }

    def test_stl_uses_separate_auto_orient_and_slice_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, machine, process, filament = self._files(root)
            source = root / "model.stl"
            source.write_bytes(b"solid x\nendsolid x\n")
            output = root / "result.gcode.3mf"
            orient_calls = []
            slice_calls = []

            def fake_auto(**kwargs):
                orient_calls.append(kwargs)
                return self._auto_orient(**kwargs)

            def fake_run(command, **kwargs):
                slice_calls.append(list(command))
                output.write_bytes(b"fake")
                return _cli_result(command)

            with (
                mock.patch.object(
                    backend,
                    "materialize_bambu_cli_profiles",
                    return_value=self._resolved(
                        machine, process, filament
                    ),
                ),
                mock.patch.object(
                    backend,
                    "auto_orient_with_bambu_cli",
                    side_effect=fake_auto,
                ),
                mock.patch.object(
                    backend,
                    "run_bambu_cli",
                    side_effect=fake_run,
                ),
                mock.patch.object(
                    backend,
                    "repair_bambu_model_settings_xml",
                    return_value={"repaired": True},
                ),
                mock.patch.object(
                    backend,
                    "inspect_gcode_3mf",
                    return_value=self._inspection(output),
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

            self.assertEqual(len(orient_calls), 1)
            self.assertEqual(len(slice_calls), 1)
            self.assertEqual(result["cli_invocation_count"], 2)
            self.assertEqual(
                result["pipeline"],
                "bambu_auto_orient_then_slice_v2",
            )

            final_command = slice_calls[0]
            self.assertNotIn("--orient", final_command)
            self.assertNotIn("--arrange", final_command)
            self.assertNotIn("--ensure-on-bed", final_command)
            self.assertEqual(
                Path(final_command[-1]),
                Path(result["slice_input"]),
            )
            self.assertTrue(
                str(final_command[-1]).endswith(
                    ".auto_orient.project.3mf"
                )
            )

    def test_3mf_skips_auto_orient_and_slices_exact_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, machine, process, filament = self._files(root)
            source = root / "input.3mf"
            source.write_bytes(b"project")
            output = root / "result.gcode.3mf"

            def fake_run(command, **kwargs):
                output.write_bytes(b"fake")
                return _cli_result(command)

            with (
                mock.patch.object(
                    backend,
                    "materialize_bambu_cli_profiles",
                    return_value=self._resolved(
                        machine, process, filament
                    ),
                ),
                mock.patch.object(
                    backend,
                    "auto_orient_with_bambu_cli",
                ) as auto_mock,
                mock.patch.object(
                    backend,
                    "run_bambu_cli",
                    side_effect=fake_run,
                ),
                mock.patch.object(
                    backend,
                    "repair_bambu_model_settings_xml",
                    return_value={"repaired": False},
                ),
                mock.patch.object(
                    backend,
                    "inspect_gcode_3mf",
                    return_value=self._inspection(output),
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

            auto_mock.assert_not_called()
            self.assertFalse(result["auto_orient_applied"])
            self.assertEqual(
                Path(result["slice_input"]),
                source.resolve(),
            )
            self.assertEqual(result["cli_invocation_count"], 1)

    def test_windows_unsigned_failure_is_retained_after_retries(self):
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
                    backend,
                    "auto_orient_with_bambu_cli",
                    side_effect=self._auto_orient,
                ),
                mock.patch.object(
                    backend,
                    "run_bambu_cli",
                    side_effect=lambda command, **kwargs: (
                        _cli_result(
                            command,
                            signed_exit=-6,
                            output_exists=False,
                        )
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
            self.assertIn("4294967290", message)
            self.assertIn("-6", message)

    def test_slice_retries_a_transient_native_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, machine, process, filament = self._files(root)
            source = root / "model.stl"
            source.write_bytes(b"x")
            output = root / "result.gcode.3mf"
            calls = []

            def fake_run(command, **kwargs):
                calls.append(list(command))

                if len(calls) == 1:
                    return _cli_result(
                        command,
                        signed_exit=-6,
                        output_exists=False,
                    )

                output.write_bytes(b"fake")
                return _cli_result(command)

            with (
                mock.patch.object(
                    backend,
                    "materialize_bambu_cli_profiles",
                    return_value=self._resolved(
                        machine, process, filament
                    ),
                ),
                mock.patch.object(
                    backend,
                    "auto_orient_with_bambu_cli",
                    side_effect=self._auto_orient,
                ),
                mock.patch.object(
                    backend,
                    "run_bambu_cli",
                    side_effect=fake_run,
                ),
                mock.patch.object(
                    backend,
                    "repair_bambu_model_settings_xml",
                    return_value={"repaired": True},
                ),
                mock.patch.object(
                    backend,
                    "inspect_gcode_3mf",
                    return_value=self._inspection(output),
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

            self.assertEqual(len(calls), 2)
            self.assertEqual(len(result["slice_attempts"]), 2)
            self.assertEqual(result["cli_invocation_count"], 3)

    def test_stale_neighbor_output_is_never_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, machine, process, filament = self._files(root)
            source = root / "model.stl"
            source.write_bytes(b"x")
            output = root / "wanted.gcode.3mf"
            old = root / "old.gcode.3mf"
            old.write_bytes(b"old")

            with (
                mock.patch.object(
                    backend,
                    "materialize_bambu_cli_profiles",
                    return_value=self._resolved(
                        machine, process, filament
                    ),
                ),
                mock.patch.object(
                    backend,
                    "auto_orient_with_bambu_cli",
                    side_effect=self._auto_orient,
                ),
                mock.patch.object(
                    backend,
                    "run_bambu_cli",
                    side_effect=lambda command, **kwargs: (
                        _cli_result(
                            command,
                            output_exists=False,
                        )
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

            self.assertEqual(old.read_bytes(), b"old")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
