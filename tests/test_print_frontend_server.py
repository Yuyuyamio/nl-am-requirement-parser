from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from am_print_automation import AutomationResult
from am_print_executor.x1c_connection import (
    MQTT_PROTOCOL,
    PrinterConnectionError,
    PrinterConnectionStatus,
)
from am_print_frontend.server import (
    JobManager,
    PrinterConnectionManager,
    create_server,
)


class FakeTranscriber:
    def __init__(self) -> None:
        self.last_audio: bytes | None = None

    def capabilities(self) -> dict[str, object]:
        return {
            "available": True,
            "engine": "faster-whisper",
            "model": "base",
            "runs_locally": True,
            "editable_result": True,
        }

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        language: str | None,
    ) -> dict[str, object]:
        self.last_audio = audio
        return {
            "text": "打印一个圆角花瓶",
            "language": language,
            "engine": "faster-whisper",
            "model": "base",
        }


class TestPrintFrontendServer(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workflow_calls: list[dict[str, object]] = []

        def runner(transcript: str, **kwargs: object) -> AutomationResult:
            provider = kwargs.get("access_code_provider")
            connection_provider = kwargs.get("printer_connection_provider")
            connection = (
                connection_provider()
                if callable(connection_provider)
                else {}
            )
            self.workflow_calls.append(
                {
                    "credential_available": bool(
                        callable(provider) and provider()
                    ),
                    "printer_connected": bool(connection.get("connected")),
                    "printer_ip": connection.get("printer_ip"),
                }
            )
            config = kwargs["config"]
            job_id = str(kwargs.get("new_job_id") or kwargs.get("resume_job_id"))
            job_dir = Path(config.output_root) / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            now = time.time()
            state = {
                "job_id": job_id,
                "status": "ready_to_print",
                "current_stage": None,
                "request_text": transcript,
                "created_unix": now,
                "updated_unix": now,
                "start_print_requested": False,
                "stages": {"m1": {"status": "completed"}},
                "last_error": None,
            }
            state_file = job_dir / "workflow_state.json"
            state_file.write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )
            (job_dir / "workflow_events.jsonl").write_text(
                json.dumps(
                    {
                        "event": "job_finished",
                        "job_id": job_id,
                        "stage": None,
                        "created_unix": now,
                        "details": {"status": "ready_to_print"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            return AutomationResult(
                job_id=job_id,
                status="ready_to_print",
                current_stage=None,
                job_directory=str(job_dir),
                state_file=str(state_file),
                m2_task_directory=None,
                stl_path=None,
                gcode_path=None,
                print_status=None,
            )

        manager = JobManager(
            output_root=self.root / "jobs",
            workflow_runner=runner,
        )
        self.printer_calls: list[dict[str, object]] = []

        def printer_connector(**kwargs: object) -> PrinterConnectionStatus:
            self.printer_calls.append(
                {
                    "ip": kwargs["ip"],
                    "device_id": kwargs["device_id"],
                    "attempts": kwargs["attempts"],
                }
            )
            if kwargs["access_code"] == "INTENTIONALLY-WRONG":
                raise PrinterConnectionError(
                    "authentication_failed",
                    printer_ip=str(kwargs["ip"]),
                    device_id=str(kwargs["device_id"]),
                    elapsed_seconds=0.2,
                )
            return PrinterConnectionStatus(
                connected=True,
                printer_ip=str(kwargs["ip"]),
                device_id=str(kwargs["device_id"]),
                transport=MQTT_PROTOCOL,
                authenticated=True,
                printer_state_observed=True,
                subscribed=True,
                elapsed_seconds=0.4,
                connection_attempts=1,
                status_summary={"gcode_state": "IDLE"},
                tls_certificate_sha256="AA" * 32,
            )

        printer_manager = PrinterConnectionManager(connector=printer_connector)
        self.printer_manager = printer_manager
        self.transcriber = FakeTranscriber()
        self.server = create_server(
            port=0,
            manager=manager,
            transcriber=self.transcriber,
            printer_manager=printer_manager,
        )
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        self.temporary.cleanup()

    def get_json(self, path: str) -> dict[str, object]:
        with urlopen(self.base + path, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_static_application_and_job_api(self) -> None:
        with urlopen(self.base + "/", timeout=2.0) as response:
            page = response.read().decode("utf-8")
        self.assertIn("造物台", page)
        self.assertIn("/app.js", page)
        self.assertIn('id="printerSuccessDialog"', page)
        self.assertIn('type="password"', page)

        request = Request(
            self.base + "/api/jobs",
            data=json.dumps(
                {"transcript": "打印一个小花瓶", "start_print": False}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2.0) as response:
            created = json.loads(response.read().decode("utf-8"))
        job_id = str(created["job_id"])

        deadline = time.monotonic() + 2.0
        snapshot: dict[str, object] = {}
        while time.monotonic() < deadline:
            snapshot = self.get_json(f"/api/jobs/{job_id}")
            if snapshot.get("terminal"):
                break
            time.sleep(0.01)
        self.assertEqual(snapshot["status"], "ready_to_print")
        self.assertTrue(snapshot["terminal"])
        self.assertEqual(len(snapshot["events"]), 1)

        jobs = self.get_json("/api/jobs")["jobs"]
        self.assertEqual(jobs[0]["job_id"], job_id)

    def test_local_speech_transcription_api(self) -> None:
        recording = b"webm-audio" * 40
        request = Request(
            self.base + "/api/speech/transcribe?language=zh",
            data=recording,
            headers={"Content-Type": "audio/webm;codecs=opus"},
            method="POST",
        )
        with urlopen(request, timeout=2.0) as response:
            result = json.loads(response.read().decode("utf-8"))

        self.assertEqual(result["text"], "打印一个圆角花瓶")
        self.assertEqual(self.transcriber.last_audio, recording)
        health = self.get_json("/api/health")
        self.assertTrue(health["speech"]["available"])

    def test_printer_connection_api_is_read_only_and_secret_free(self) -> None:
        secret = "REAL-SECRET-MUST-NOT-BE-RETURNED"
        request = Request(
            self.base + "/api/printer/connect",
            data=json.dumps(
                {
                    "ip": "172.16.61.6",
                    "device_id": "00M09A3A1700722",
                    "access_code": secret,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2.0) as response:
            connected = json.loads(response.read().decode("utf-8"))

        self.assertTrue(connected["connected"])
        self.assertTrue(connected["authenticated"])
        self.assertTrue(connected["printer_state_observed"])
        self.assertTrue(connected["credential_available"])
        self.assertNotIn(secret, json.dumps(connected))
        self.assertEqual(self.printer_calls[-1]["attempts"], 1)
        self.assertNotIn("access_code", self.printer_calls[-1])

        snapshot = self.get_json("/api/printer")
        self.assertTrue(snapshot["connected"])
        self.assertTrue(snapshot["credential_available"])
        self.assertNotIn(secret, json.dumps(snapshot))

        job_request = Request(
            self.base + "/api/jobs",
            data=json.dumps(
                {"transcript": "打印一个凭证传递测试件", "start_print": False}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(job_request, timeout=2.0) as response:
            job = json.loads(response.read().decode("utf-8"))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            job = self.get_json(f"/api/jobs/{job['job_id']}")
            if job.get("terminal"):
                break
            time.sleep(0.01)
        self.assertTrue(self.workflow_calls[-1]["credential_available"])
        self.assertTrue(self.workflow_calls[-1]["printer_connected"])
        self.assertEqual(
            self.workflow_calls[-1]["printer_ip"],
            "172.16.61.6",
        )
        self.assertNotIn(secret, json.dumps(job))
        self.assertFalse(any(secret in path.read_text(encoding="utf-8")
                             for path in self.root.rglob("*.json")))

    def test_printer_connection_api_returns_classified_safe_failure(self) -> None:
        secret = "INTENTIONALLY-WRONG"
        request = Request(
            self.base + "/api/printer/connect",
            data=json.dumps(
                {
                    "ip": "172.16.61.6",
                    "device_id": "00M09A3A1700722",
                    "access_code": secret,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2.0)
        failure = json.loads(context.exception.read().decode("utf-8"))

        self.assertEqual(context.exception.code, 401)
        self.assertEqual(failure["error_code"], "authentication_failed")
        self.assertFalse(failure["credential_available"])
        self.assertEqual(self.printer_manager.access_code(), "")
        self.assertNotIn(secret, json.dumps(failure))

    def test_dead_worker_with_stale_running_state_is_reported_failed(self) -> None:
        def crashing_runner(transcript: str, **kwargs: object) -> object:
            job_id = str(kwargs["new_job_id"])
            job_directory = self.root / "crash-jobs" / job_id
            job_directory.mkdir(parents=True, exist_ok=True)
            now = time.time()
            (job_directory / "workflow_state.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": "running",
                        "current_stage": "m2_normalized_validation",
                        "request_text": transcript,
                        "created_unix": now,
                        "updated_unix": now,
                        "start_print_requested": False,
                        "stages": {},
                        "last_error": None,
                    }
                ),
                encoding="utf-8",
            )
            raise PermissionError(5, "transient Windows file lock")

        manager = JobManager(
            output_root=self.root / "crash-jobs",
            workflow_runner=crashing_runner,
        )
        started = manager.start_job("打印一个测试件", start_print=False)
        deadline = time.monotonic() + 2.0
        snapshot = started
        while time.monotonic() < deadline:
            snapshot = manager.snapshot(str(started["job_id"]))
            if not snapshot["control"]["worker_alive"]:
                break
            time.sleep(0.01)

        self.assertEqual(snapshot["status"], "failed")
        self.assertTrue(snapshot["terminal"])
        self.assertEqual(snapshot["last_error"]["type"], "PermissionError")

    def test_orphaned_running_state_after_restart_is_retryable_failure(self) -> None:
        output_root = self.root / "orphan-jobs"
        job_id = "AUTO-ORPHANED-STATE"
        job_directory = output_root / job_id
        job_directory.mkdir(parents=True)
        now = time.time()
        (job_directory / "workflow_state.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "status": "running",
                    "current_stage": "m2_normalized_validation",
                    "request_text": "打印一个测试件",
                    "created_unix": now,
                    "updated_unix": now,
                    "start_print_requested": False,
                    "stages": {},
                    "last_error": None,
                }
            ),
            encoding="utf-8",
        )
        manager = JobManager(output_root=output_root)

        snapshot = manager.snapshot(job_id)

        self.assertEqual(snapshot["status"], "failed")
        self.assertTrue(snapshot["terminal"])
        self.assertEqual(
            snapshot["last_error"]["type"],
            "WorkflowInterrupted",
        )

    def test_geometry_regeneration_state_is_a_clean_terminal_result(self) -> None:
        output_root = self.root / "geometry-regeneration-jobs"
        job_id = "AUTO-NEEDS-GEOMETRY"
        job_directory = output_root / job_id
        job_directory.mkdir(parents=True)
        now = time.time()
        (job_directory / "workflow_state.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "status": "needs_geometry_regeneration",
                    "current_stage": None,
                    "request_text": "打印一个测试件",
                    "created_unix": now,
                    "updated_unix": now,
                    "start_print_requested": True,
                    "stages": {
                        "m3_support_printability": {
                            "status": "completed",
                            "result": {"status": "blocked"},
                        },
                        "printer_upload": {"status": "skipped"},
                        "print_start": {"status": "skipped"},
                    },
                    "last_error": None,
                }
            ),
            encoding="utf-8",
        )
        manager = JobManager(output_root=output_root)

        snapshot = manager.snapshot(job_id)

        self.assertEqual(snapshot["status"], "needs_geometry_regeneration")
        self.assertTrue(snapshot["terminal"])
        self.assertIsNone(snapshot["last_error"])


if __name__ == "__main__":
    unittest.main()
