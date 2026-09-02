from __future__ import annotations

import json
import hashlib
import tempfile
import time
import unittest
import zipfile

from http import HTTPStatus
from pathlib import Path

from am_print_executor.x1c_connection import MQTT_PROTOCOL, PrinterConnectionStatus
from am_print_frontend.delivery import verified_files
from am_print_frontend.print_monitor import summarize_print_status
from am_print_frontend.server import ApiError, JobManager, PrinterConnectionManager


class PrintMonitorSummaryTests(unittest.TestCase):
    def test_normal_layer_updates_are_reduced_to_percentage(self) -> None:
        summary = summarize_print_status(
            {
                "gcode_state": "RUNNING",
                "mc_percent": 42,
                "mc_remaining_time": 81,
                "layer_num": 418,
                "total_layer_num": 999,
                "print_error": 0,
                "hms": [],
            },
            active_seen=True,
        )

        self.assertEqual(summary["status"], "printing")
        self.assertEqual(summary["percent"], 42.0)
        self.assertNotIn("layer_num", summary)
        self.assertNotIn("total_layer_num", summary)
        self.assertNotIn("alert", summary)

    def test_error_includes_the_layer_context(self) -> None:
        summary = summarize_print_status(
            {
                "gcode_state": "FAILED",
                "mc_percent": 42,
                "layer_num": 418,
                "total_layer_num": 999,
                "print_error": 123,
                "hms": [{"attr": 1, "code": 2}],
            },
            active_seen=True,
        )

        self.assertEqual(summary["status"], "error")
        self.assertTrue(summary["has_alert"])
        self.assertEqual(summary["alert"]["layer_num"], 418)
        self.assertEqual(summary["alert"]["total_layer_num"], 999)

    def test_reprint_only_becomes_ready_after_safe_terminal_state(self) -> None:
        completed = summarize_print_status(
            {
                "gcode_state": "FINISH",
                "mc_percent": 100,
                "print_error": 0,
                "sdcard": True,
                "hms": [],
                "nozzle_target_temper": 0,
                "bed_target_temper": 0,
            },
            active_seen=True,
        )
        paused = summarize_print_status(
            {
                "gcode_state": "PAUSE",
                "print_error": 0,
                "sdcard": True,
                "hms": [],
                "nozzle_target_temper": 0,
                "bed_target_temper": 0,
            },
            active_seen=True,
        )

        self.assertTrue(completed["restart_ready"])
        self.assertFalse(paused["restart_ready"])

    def test_job_snapshot_exposes_live_monitor_as_a_separate_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "job-monitor-test"
            job = root / job_id
            job.mkdir()
            (job / "workflow_state.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": "print_started",
                        "current_stage": None,
                        "request_text": "打印一个测试件",
                        "created_unix": 1,
                        "updated_unix": 2,
                        "start_print_requested": True,
                        "stages": {},
                        "last_error": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = JobManager(output_root=root)
            manager.set_printer_connection_provider(
                lambda: {
                    "monitor": {
                        "status": "printing",
                        "percent": 63.0,
                        "remaining_minutes": 24.0,
                        "has_alert": False,
                    }
                }
            )

            snapshot = manager.snapshot(job_id)

        self.assertEqual(snapshot["print_monitor"]["status"], "printing")
        self.assertEqual(snapshot["print_monitor"]["percent"], 63.0)

    def test_verified_connection_starts_the_read_only_live_monitor(self) -> None:
        captured: dict[str, object] = {}

        def connector(**kwargs: object) -> PrinterConnectionStatus:
            return PrinterConnectionStatus(
                connected=True,
                printer_ip=str(kwargs["ip"]),
                device_id=str(kwargs["device_id"]),
                transport=MQTT_PROTOCOL,
                authenticated=True,
                printer_state_observed=True,
                subscribed=True,
                elapsed_seconds=0.1,
                connection_attempts=1,
                status_summary={"gcode_state": "IDLE", "mc_percent": 0},
                tls_certificate_sha256="AA" * 32,
            )

        class FakeMonitor:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            def start(self) -> None:
                callback = captured["on_update"]
                callback(
                    {
                        "status": "printing",
                        "percent": 27.0,
                        "remaining_minutes": 33.0,
                        "has_alert": False,
                    }
                )

            def stop(self) -> None:
                captured["stopped"] = True

        manager = PrinterConnectionManager(
            connector=connector,
            live_monitor_factory=FakeMonitor,
        )
        result = manager.connect(
            ip="172.16.61.6",
            access_code="SECRET-NOT-PUBLIC",
            device_id="00M09A3A1700722",
        )
        snapshot = manager.snapshot()
        manager.close()

        self.assertEqual(snapshot["monitor"]["percent"], 27.0)
        self.assertEqual(captured["ip"], "172.16.61.6")
        self.assertNotIn("SECRET-NOT-PUBLIC", json.dumps(result))
        self.assertNotIn("SECRET-NOT-PUBLIC", json.dumps(snapshot))
        self.assertTrue(captured["stopped"])

    def test_generation_phase_does_not_reuse_another_print_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "job-still-generating"
            job = root / job_id
            job.mkdir()
            (job / "workflow_state.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": "running",
                        "current_stage": "m2_wait",
                        "request_text": "打印一个测试件",
                        "created_unix": 1,
                        "updated_unix": 2,
                        "start_print_requested": True,
                        "stages": {},
                        "last_error": None,
                    }
                ),
                encoding="utf-8",
            )
            manager = JobManager(output_root=root)
            manager.set_printer_connection_provider(
                lambda: {"monitor": {"status": "printing", "percent": 88.0}}
            )

            snapshot = manager.snapshot(job_id)

        self.assertEqual(snapshot["print_monitor"]["status"], "waiting")
        self.assertEqual(snapshot["print_monitor"]["percent"], 0.0)


class ReprintStepOneTests(unittest.TestCase):
    @staticmethod
    def _write_source_job(root: Path, job_id: str = "job-original-print") -> Path:
        job = root / job_id
        files = job / "delivery"
        files.mkdir(parents=True)
        paths = {
            "gcode": files / "ready.gcode.3mf",
            "project": files / "editable.3mf",
            "stl": files / "body.stl",
        }
        settings = {
            "curr_bed_type": "Textured PEI Plate",
            "filament_type": ["PLA"],
            "textured_plate_temp": ["55"],
            "textured_plate_temp_initial_layer": ["55"],
        }
        with zipfile.ZipFile(paths["project"], "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("3D/3dmodel.model", "<model/>")
            archive.writestr("Metadata/model_settings.config", "<config/>")
            archive.writestr("Metadata/slice_info.config", "<config/>")
            archive.writestr("Metadata/project_settings.config", json.dumps(settings))
        gcode = b"""; curr_bed_type = Textured PEI Plate
M140 S55
M190 S55
G29.1 Z-0.04 ; for Textured PEI Plate
G1 X1 Y1 E1
"""
        with zipfile.ZipFile(paths["gcode"], "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("3D/3dmodel.model", "<model/>")
            archive.writestr("Metadata/model_settings.config", "<config/>")
            archive.writestr("Metadata/slice_info.config", "<config/>")
            archive.writestr("Metadata/project_settings.config", json.dumps(settings))
            archive.writestr("Metadata/plate_1.json", json.dumps({"bed_type": "textured_plate"}))
            archive.writestr("Metadata/plate_1.gcode", gcode)
            archive.writestr(
                "Metadata/plate_1.gcode.md5",
                hashlib.md5(gcode).hexdigest().upper(),
            )
        paths["stl"].write_bytes(b"verified-stl")
        digests: dict[str, str] = {}
        for kind, path in paths.items():
            digests[kind] = hashlib.sha256(path.read_bytes()).hexdigest()
        state = {
            "schema_version": "automatic-print-workflow-v1",
            "job_id": job_id,
            "status": "print_started",
            "current_stage": None,
            "request_text": "打印一个测试件",
            "configuration": {},
            "created_unix": 1,
            "updated_unix": 2,
            "start_print_requested": True,
            "stages": {
                "bambu_slice": {
                    "status": "completed",
                    "attempts": 1,
                    "result": {
                        "status": "slice_complete",
                        "pipeline": "bambu_native_direct_print_v3",
                        "acceptance_basis": "bambu_cli_slice_and_artifact_validation",
                        "post_slice_validation_performed": True,
                        "post_slice_validation": {
                            "status": "bambu_gcode_3mf_finalized",
                            "validation": {"status": "bambu_gcode_3mf_validated"},
                        },
                        "project_container_validation_performed": True,
                        "project_container_validation": {
                            "status": "bambu_project_3mf_validated"
                        },
                        "delivery_artifact_validation_performed": True,
                        "delivery_artifact_validation": {
                            "status": "bambu_gcode_3mf_validated"
                        },
                        "build_plate": {
                            "curr_bed_type": "Textured PEI Plate",
                            "bed_type": "textured_plate",
                            "pla_bed_temperature_c": 55,
                            "z_compensation_mm": -0.04,
                        },
                        "artifact": {
                            "path": str(paths["gcode"].resolve()),
                            "sha256": digests["gcode"],
                        },
                        "project": {
                            "path": str(paths["project"].resolve()),
                            "sha256": digests["project"],
                        },
                        "geometry_path": str(paths["stl"].resolve()),
                        "geometry_sha256": digests["stl"],
                    },
                },
                "printer_upload": {
                    "status": "completed",
                    "result": {"status": "upload_verified"},
                },
                "print_start": {
                    "status": "completed",
                    "result": {
                        "status": "direct_print_started",
                        "payload": {"print": {"subtask_name": "original-print"}},
                    },
                },
            },
            "last_error": None,
        }
        (job / "workflow_state.json").write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        return job

    def test_reprint_copies_verified_files_into_a_new_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_job_id = "job-original-print"
            source_job = self._write_source_job(root, source_job_id)
            captured: dict[str, object] = {}

            class FakeServices:
                def upload_gcode(self, path, access_code, *, printer_connection=None):
                    captured["gcode_path"] = Path(path)
                    captured["access_code"] = access_code
                    captured["connection"] = printer_connection
                    return {"status": "upload_verified", "remote_path": "/reprint.gcode.3mf"}

                def start_print(self, upload, access_code):
                    captured["start_upload"] = upload
                    return {
                        "status": "direct_print_started",
                        "payload": {"print": {"subtask_name": "reprint-task"}},
                    }

            connection = {
                "connected": True,
                "monitor": {
                    "status": "completed",
                    "percent": 100.0,
                    "restart_ready": True,
                    "has_alert": False,
                },
            }
            manager = JobManager(
                output_root=root,
                access_code_provider=lambda: "SECRET-NOT-STORED",
                printer_connection_provider=lambda: connection,
                reprint_services_factory=lambda config: FakeServices(),
            )

            snapshot = manager.reprint_job(source_job_id)
            new_job_id = snapshot["job_id"]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                snapshot = manager.snapshot(new_job_id)
                if not snapshot["control"]["worker_alive"]:
                    break
                time.sleep(0.01)

            new_state_path = root / new_job_id / "workflow_state.json"
            new_state = json.loads(new_state_path.read_text(encoding="utf-8"))
            copied_files = verified_files(new_state, root / new_job_id)
            source_state = json.loads((source_job / "workflow_state.json").read_text(encoding="utf-8"))
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (new_state_path, root / new_job_id / "workflow_events.jsonl")
            )

            self.assertNotEqual(new_job_id, source_job_id)
            self.assertEqual(snapshot["status"], "print_started")
            self.assertEqual(snapshot["source_job_id"], source_job_id)
            self.assertEqual(new_state["reprint_mode"], "verified_step1_files")
            self.assertEqual(new_state["stages"]["print_start"]["status"], "completed")
            self.assertEqual(source_state["stages"]["print_start"]["result"]["payload"]["print"]["subtask_name"], "original-print")
            self.assertTrue(Path(captured["gcode_path"]).is_relative_to(root / new_job_id))
            self.assertNotEqual(copied_files["gcode"][0], source_job / "delivery" / "ready.gcode.3mf")
            self.assertNotIn("SECRET-NOT-STORED", persisted)

    def test_paused_print_cannot_create_a_reprint_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_job_id = "job-original-print"
            self._write_source_job(root, source_job_id)
            manager = JobManager(
                output_root=root,
                access_code_provider=lambda: "SECRET-NOT-STORED",
                printer_connection_provider=lambda: {
                    "connected": True,
                    "monitor": {
                        "status": "paused",
                        "restart_ready": False,
                        "has_alert": False,
                    },
                },
            )

            with self.assertRaises(ApiError) as caught:
                manager.reprint_job(source_job_id)

            self.assertEqual(caught.exception.status, HTTPStatus.CONFLICT)
            self.assertEqual(
                [path.name for path in root.iterdir() if path.is_dir()],
                [source_job_id],
            )


if __name__ == "__main__":
    unittest.main()
