from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest

from pathlib import Path
from typing import Any
from unittest.mock import patch

import am_print_automation.workflow as workflow_module
from am_print_automation.workflow import (
    AutomationConfig,
    AutomationWorkflowError,
    WorkflowControl,
    WorkflowStopRequested,
    _atomic_write_json,
    create_job_id,
    run_text_to_print,
)


class FakeServices:
    def __init__(
        self,
        root: Path,
        *,
        clarification: bool = False,
        raw_mesh_passes: bool = True,
        fail_slice_once: bool = False,
        interrupt_start: bool = False,
    ) -> None:
        self.root = root
        self.clarification = clarification
        self.raw_mesh_passes = raw_mesh_passes
        self.fail_slice_once = fail_slice_once
        self.interrupt_start = interrupt_start
        self.calls: list[str] = []
        self.validate_count = 0
        self.start_count = 0
        self.printer_connection: dict[str, Any] | None = None

    def _call(self, name: str) -> None:
        self.calls.append(name)

    def run_m1(self, text: str, output_dir: Path) -> dict[str, Any]:
        self._call("m1")
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = output_dir / "m1_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        if self.clarification:
            return {
                "status": "needs_clarification",
                "task_type": "creative_asset",
                "next_module": None,
                "manifest_file": str(manifest),
                "payload": {
                    "clarification_question": "成品需要多高？",
                },
            }
        return {
            "status": "ready",
            "task_type": "creative_asset",
            "next_module": "M2",
            "manifest_file": str(manifest),
            "payload": {},
        }

    def plan_m2(self, manifest: Path, output_root: Path) -> dict[str, Any]:
        self._call("m2_plan")
        task = output_root / "M2-FAKE"
        task.mkdir(parents=True, exist_ok=True)
        return {"status": "planned", "output_directory": str(task)}

    def submit_m2(self, task_dir: Path) -> dict[str, Any]:
        self._call("m2_submit")
        return {"status": "submitted"}

    def poll_m2(self, task_dir: Path) -> dict[str, Any]:
        self._call("m2_poll")
        return {"status": "generated", "provider_status": "completed"}

    def acquire_m2(self, task_dir: Path) -> dict[str, Any]:
        self._call("m2_artifact")
        return {"status": "acquired"}

    def validate_m2(self, task_dir: Path) -> dict[str, Any]:
        self._call("m2_validate")
        self.validate_count += 1
        passed = self.raw_mesh_passes or self.validate_count > 1
        return {"status": "validated", "hard_constraints_passed": passed}

    def repair_m2(self, task_dir: Path) -> dict[str, Any]:
        self._call("m2_repair")
        return {"status": "repaired"}

    def normalize_m2(self, task_dir: Path) -> dict[str, Any]:
        self._call("m2_normalize")
        return {"status": "normalized"}

    def validate_normalized_m2(self, task_dir: Path) -> dict[str, Any]:
        self._call("m2_normalized_validate")
        return {"status": "validated", "hard_constraints_passed": True}

    def create_stl(self, task_dir: Path) -> dict[str, Any]:
        self._call("m2_stl")
        path = task_dir / "m2_output.stl"
        path.write_bytes(b"fake-stl")
        return {"status": "stl_handoff_ready", "m3_model_file": str(path)}

    def slice_stl(self, stl_path: Path, output_path: Path) -> dict[str, Any]:
        self._call("bambu_slice")
        if self.fail_slice_once:
            self.fail_slice_once = False
            raise RuntimeError("transient slicer failure")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-gcode")
        return {
            "status": "slice_complete",
            "artifact": {"path": str(output_path), "sha256": "a" * 64},
        }

    def upload_gcode(
        self,
        gcode_path: Path,
        access_code: str,
        *,
        printer_connection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._call("printer_upload")
        self.assert_secret(access_code)
        self.printer_connection = printer_connection
        return {
            "status": "upload_verified",
            "remote_path": "/fake.gcode.3mf",
            "printer_ip": "192.168.1.2",
            "device_id": "FAKE",
            "gcode_entries": ["Metadata/plate_1.gcode"],
        }

    def start_print(self, upload: dict[str, Any], access_code: str) -> dict[str, Any]:
        self._call("print_start")
        self.start_count += 1
        self.assert_secret(access_code)
        if self.interrupt_start:
            raise KeyboardInterrupt("simulated process death after dispatch")
        return {"status": "direct_print_started", "mqtt_publish_count": 1}

    @staticmethod
    def assert_secret(access_code: str) -> None:
        if access_code != "TOP-SECRET":
            raise AssertionError("unexpected secret")


class TestAutomaticPrintWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_atomic_state_write_retries_transient_windows_file_lock(self) -> None:
        destination = self.root / "state.json"
        replace_calls = 0
        real_replace = workflow_module.os.replace

        def transient_replace(source: object, target: object) -> None:
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls < 3:
                raise PermissionError(5, "transient Windows file lock")
            real_replace(source, target)

        with patch(
            "am_print_automation.workflow.os.replace",
            side_effect=transient_replace,
        ), patch("am_print_automation.workflow.time.sleep") as sleep:
            _atomic_write_json(destination, {"status": "running"})

        self.assertEqual(replace_calls, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(
            json.loads(destination.read_text(encoding="utf-8")),
            {"status": "running"},
        )
        self.assertEqual(list(self.root.glob(".state.json.*.tmp")), [])

    def config(self, *, start_print: bool = False) -> AutomationConfig:
        return AutomationConfig(
            output_root=self.root / "jobs",
            start_print=start_print,
            provider_poll_interval_seconds=0.001,
            provider_timeout_seconds=1.0,
        )

    def test_prepare_pipeline_is_one_call_and_does_not_request_secret(self) -> None:
        services = FakeServices(self.root)
        secret_calls = 0

        def secret() -> str:
            nonlocal secret_calls
            secret_calls += 1
            return "TOP-SECRET"

        result = run_text_to_print(
            "打印一只十厘米高的卡通小狗",
            config=self.config(),
            services=services,
            access_code_provider=secret,
        )

        self.assertEqual(result.status, "ready_to_print")
        self.assertEqual(secret_calls, 0)
        self.assertNotIn("printer_upload", services.calls)
        self.assertNotIn("print_start", services.calls)
        self.assertNotIn("m3_printability", services.calls)
        self.assertTrue(Path(result.gcode_path or "").is_file())

    def test_slice_geometry_is_used_for_delivery_without_validation(self) -> None:
        class RepairedServices(FakeServices):
            def _placed(self, result, output):
                self.expected_geometry = output.with_suffix(".stl")
                self.expected_geometry.write_bytes(b"repaired-placed-geometry")
                result["geometry_path"] = str(self.expected_geometry)
                return result

            def slice_stl(self, stl_path, output_path):
                return self._placed(super().slice_stl(stl_path, output_path), output_path)

        services = RepairedServices(self.root)
        result = run_text_to_print("print a figurine", config=self.config(), services=services)
        self.assertEqual(result.status, "ready_to_print")
        self.assertEqual(result.stl_path, str(services.expected_geometry))
        self.assertNotIn("m3_printability", services.calls)
        self.assertNotIn("printer_upload", services.calls)

    def test_obsolete_preparation_replays_only_local_stages_and_preserves_history(self) -> None:
        from am_print_automation.preparation_recovery import can_recover_preparation, PREPARATION_STAGES
        from am_print_executor.preparation_version import PREPARATION_REVISION
        failed = FakeServices(self.root)
        result = run_text_to_print('print a figurine', config=self.config(), services=failed)
        state_file = Path(result.state_file)
        old = json.loads(state_file.read_text())
        old['status'] = 'needs_geometry_regeneration'
        old['stages']['bambu_slice']['result'].update(
            status='blocked', pipeline='verified_support_orient_reslice_v3'
        )
        _atomic_write_json(state_file,old)
        self.assertTrue(can_recover_preparation(old))
        recovered = FakeServices(self.root)
        final = run_text_to_print('print a figurine',config=self.config(),services=recovered,resume_job_id=result.job_id)
        self.assertEqual(final.status,'ready_to_print')
        self.assertEqual(recovered.calls,['bambu_slice'])
        current = json.loads(state_file.read_text())
        self.assertEqual(current['preparation_revision'],PREPARATION_REVISION)
        self.assertNotIn('geometry_regeneration_attempts',current)
        self.assertNotIn('bambu_regeneration_slice',current['stages'])
        archives=list(Path(result.job_directory).glob('attempt_history/*/workflow_state.json'))
        self.assertEqual(len(archives),1)
        archived=json.loads(archives[0].read_text())
        self.assertEqual(archived['stages']['bambu_slice'],old['stages']['bambu_slice'])
        self.assertIn(current['preparation_attempt'],final.gcode_path)
        self.assertTrue(Path(old['stages']['bambu_slice']['result']['artifact']['path']).is_file())
        # Same-revision failure is not a new budget, nor a request to generate again.
        current['status']='needs_geometry_regeneration'
        current['stages']['bambu_slice']['result'].update(status='blocked',pipeline='bambu_native_direct_print_v2')
        self.assertFalse(can_recover_preparation(current))

    def test_preparation_recovery_never_replays_possible_printer_side_effects(self) -> None:
        import copy
        from am_print_automation.preparation_recovery import can_recover_preparation
        state={'status':'needs_geometry_regeneration','stages':{
            'm2_stl_handoff':{'status':'completed'},
            'bambu_slice':{'status':'completed','result':{'status':'blocked','pipeline':'verified_support_orient_reslice_v3'}}}}
        self.assertTrue(can_recover_preparation(state))
        for name in ('printer_upload','print_start'):
            for status in ('running','outcome_unknown','completed','failed'):
                modified=copy.deepcopy(state)
                modified['stages'][name]={'status':status,'attempts':1}
                self.assertFalse(can_recover_preparation(modified))

    def test_old_blocked_preparation_can_reslice_and_print_without_validation(self) -> None:
        prepared = run_text_to_print(
            "打印一个树状支撑测试件",
            config=self.config(),
            services=FakeServices(self.root),
        )
        state_file = Path(prepared.state_file)
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["status"] = "printability_blocked"
        state["preparation_revision"] = "20260901_bambu_native_tree_support_v1"
        state["stages"]["bambu_slice"]["result"].update(
            status="blocked",
            pipeline="bambu_native_tree_support_v1",
        )
        _atomic_write_json(state_file, state)

        services = FakeServices(self.root)
        result = run_text_to_print(
            "打印一个树状支撑测试件",
            config=self.config(start_print=True),
            resume_job_id=prepared.job_id,
            services=services,
            access_code_provider=lambda: "TOP-SECRET",
        )

        self.assertEqual(result.status, "print_started")
        self.assertEqual(services.calls, ["bambu_slice", "printer_upload", "print_start"])
        current = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertNotIn("m3_printability", current["stages"])

    def test_direct_print_consumes_secret_once_and_never_persists_it(self) -> None:
        services = FakeServices(self.root)
        secret_calls = 0

        def secret() -> str:
            nonlocal secret_calls
            secret_calls += 1
            return "TOP-SECRET"

        result = run_text_to_print(
            "打印一只十厘米高的卡通小狗",
            config=self.config(start_print=True),
            services=services,
            access_code_provider=secret,
        )

        self.assertEqual(result.status, "print_started")
        self.assertEqual(result.print_status, "direct_print_started")
        self.assertEqual(secret_calls, 1)
        state_text = Path(result.state_file).read_text(encoding="utf-8")
        events_text = (
            Path(result.job_directory) / "workflow_events.jsonl"
        ).read_text(encoding="utf-8")
        self.assertNotIn("TOP-SECRET", state_text)
        self.assertNotIn("TOP-SECRET", events_text)
        self.assertIn('"credentials_stored": false', state_text)

    def test_direct_print_receives_verified_connection_without_persisting_it(self) -> None:
        services = FakeServices(self.root)
        connection = {
            "connected": True,
            "authenticated": True,
            "printer_state_observed": True,
            "credential_available": True,
            "printer_ip": "192.168.1.2",
            "device_id": "FAKE-DEVICE",
            "tls_certificate_sha256": "AB" * 32,
        }

        result = run_text_to_print(
            "打印一个连接来源测试件",
            config=self.config(start_print=True),
            services=services,
            access_code_provider=lambda: "TOP-SECRET",
            printer_connection_provider=lambda: connection,
        )

        self.assertEqual(result.status, "print_started")
        self.assertEqual(services.printer_connection, connection)
        state_text = Path(result.state_file).read_text(encoding="utf-8")
        self.assertNotIn("tls_certificate_sha256", state_text)

    def test_prepared_job_can_resume_directly_into_upload_and_start(self) -> None:
        prepared_services = FakeServices(self.root)
        prepared = run_text_to_print(
            "打印一只二十厘米高的海豚",
            config=self.config(),
            services=prepared_services,
        )

        dispatch_services = FakeServices(self.root)
        started = run_text_to_print(
            "打印一只二十厘米高的海豚",
            config=self.config(start_print=True),
            resume_job_id=prepared.job_id,
            services=dispatch_services,
            access_code_provider=lambda: "TOP-SECRET",
        )

        self.assertEqual(started.status, "print_started")
        self.assertEqual(
            dispatch_services.calls,
            ["printer_upload", "print_start"],
        )

    def test_failed_safe_stage_resumes_without_repeating_completed_work(self) -> None:
        first_services = FakeServices(self.root, fail_slice_once=True)
        with self.assertRaises(AutomationWorkflowError) as context:
            run_text_to_print(
                "打印一个可爱的企鹅",
                config=self.config(),
                services=first_services,
            )

        job_id = context.exception.job_id
        self.assertIsNotNone(job_id)
        second_services = FakeServices(self.root)
        result = run_text_to_print(
            "打印一个可爱的企鹅",
            config=self.config(),
            resume_job_id=job_id,
            services=second_services,
        )

        self.assertEqual(result.status, "ready_to_print")
        self.assertEqual(
            second_services.calls,
            ["bambu_slice"],
        )

    def test_bambu_slice_success_goes_directly_to_upload_and_print(self) -> None:
        services = FakeServices(self.root)
        secret_calls = 0

        def secret() -> str:
            nonlocal secret_calls
            secret_calls += 1
            return "TOP-SECRET"

        result = run_text_to_print(
            "打印一个悬垂较多的树屋模型",
            config=self.config(start_print=True),
            services=services,
            access_code_provider=secret,
        )

        self.assertEqual(result.status, "print_started")
        self.assertNotIn("m3_printability", services.calls)
        self.assertNotIn("bambu_support_reslice", services.calls)
        self.assertEqual(services.calls.count("m2_plan"), 1)
        self.assertEqual(services.calls.count("m2_submit"), 1)
        self.assertIn("printer_upload", services.calls)
        self.assertIn("print_start", services.calls)
        self.assertEqual(secret_calls, 1)
        state = json.loads(Path(result.state_file).read_text(encoding="utf-8"))
        self.assertNotIn("geometry_regeneration_attempts", state)
        self.assertNotIn("printability_resolution", state)
        self.assertNotIn("m3_printability", state["stages"])
        self.assertNotIn("bambu_support_reslice", state["stages"])
        self.assertNotIn("m3_support_printability", state["stages"])
    def test_print_dispatch_with_unknown_outcome_is_never_replayed(self) -> None:
        services = FakeServices(self.root, interrupt_start=True)
        with self.assertRaises(AutomationWorkflowError) as context:
            run_text_to_print(
                "打印一个小花盆",
                config=self.config(start_print=True),
                services=services,
                access_code_provider=lambda: "TOP-SECRET",
            )

        job_id = context.exception.job_id
        self.assertEqual(services.start_count, 1)
        resumed = FakeServices(self.root)
        with self.assertRaises(AutomationWorkflowError) as resumed_context:
            run_text_to_print(
                "打印一个小花盆",
                config=self.config(start_print=True),
                resume_job_id=job_id,
                services=resumed,
                access_code_provider=lambda: "TOP-SECRET",
            )

        self.assertIn("Automatic replay is blocked", str(resumed_context.exception))
        self.assertEqual(resumed.start_count, 0)

    def test_failed_raw_mesh_enters_repair_branch(self) -> None:
        services = FakeServices(self.root, raw_mesh_passes=False)
        result = run_text_to_print(
            "打印一个圆润的猫摆件",
            config=self.config(),
            services=services,
        )

        self.assertEqual(result.status, "ready_to_print")
        self.assertIn("m2_repair", services.calls)
        self.assertEqual(services.validate_count, 2)

    def test_clarification_stops_before_generation(self) -> None:
        services = FakeServices(self.root, clarification=True)
        result = run_text_to_print(
            "给我打印一个东西",
            config=self.config(),
            services=services,
        )

        self.assertEqual(result.status, "awaiting_clarification")
        self.assertEqual(services.calls, ["m1"])
        state = json.loads(Path(result.state_file).read_text(encoding="utf-8"))
        self.assertEqual(state["clarification_question"], "成品需要多高？")

    def test_pause_blocks_before_first_stage_and_resume_continues(self) -> None:
        services = FakeServices(self.root)
        control = WorkflowControl()
        job_id = create_job_id()
        control.pause()
        outcome: dict[str, object] = {}

        def run() -> None:
            outcome["result"] = run_text_to_print(
                "打印一个圆角收纳盒",
                config=self.config(),
                new_job_id=job_id,
                services=services,
                control=control,
            )

        worker = threading.Thread(target=run)
        worker.start()
        state_file = self.root / "jobs" / job_id / "workflow_state.json"
        deadline = time.monotonic() + 2.0
        state: dict[str, object] = {}
        while time.monotonic() < deadline:
            if state_file.is_file():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if state.get("status") == "paused":
                    break
            time.sleep(0.01)

        self.assertEqual(state.get("status"), "paused")
        self.assertEqual(services.calls, [])
        control.resume()
        worker.join(timeout=3.0)
        self.assertFalse(worker.is_alive())
        result = outcome["result"]
        self.assertEqual(result.status, "ready_to_print")
        events = (
            self.root / "jobs" / job_id / "workflow_events.jsonl"
        ).read_text(encoding="utf-8")
        self.assertIn('"event": "job_paused"', events)
        self.assertIn('"event": "job_resumed"', events)

    def test_stop_before_first_stage_is_terminal_and_runs_nothing(self) -> None:
        services = FakeServices(self.root)
        control = WorkflowControl()
        job_id = create_job_id()
        control.stop()

        with self.assertRaises(WorkflowStopRequested):
            run_text_to_print(
                "打印一个小灯罩",
                config=self.config(),
                new_job_id=job_id,
                services=services,
                control=control,
            )

        self.assertEqual(services.calls, [])
        state = json.loads(
            (self.root / "jobs" / job_id / "workflow_state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["status"], "stopped")

    def test_irreversible_dispatch_rejects_late_stop(self) -> None:
        control = WorkflowControl()
        self.assertTrue(control.begin_irreversible())
        self.assertEqual(control.status, "dispatching")
        self.assertFalse(control.pause())
        self.assertFalse(control.stop())


if __name__ == "__main__":
    unittest.main()
