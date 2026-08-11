from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from am_print_executor.gate4_runtime_v40 import (
    Gate4V40Error,
    _build_project_file_payload,
    _find_gate3_v331,
    _preflight_checks,
    expected_start_phrase,
    run_gate4b,
)

RID = "M2-1E4B2301FADD"
DID = "00M09A3A1700722"


class Gate4V40Tests(unittest.TestCase):
    def test_start_phrase_is_bound_to_request_and_device(self):
        self.assertEqual(expected_start_phrase(), f"START_PRINT_{RID}_{DID}")

    def test_preflight_requires_idle(self):
        result = _preflight_checks({
            "gcode_state": "RUNNING", "print_error": 0, "nozzle_diameter": "0.4",
            "sdcard": True, "hms": []
        })
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["gcode_state_idle"])

    def test_preflight_accepts_clean_idle(self):
        result = _preflight_checks({
            "gcode_state": "IDLE", "print_error": 0, "nozzle_diameter": "0.4",
            "sdcard": True, "hms": []
        })
        self.assertTrue(result["passed"])

    def test_gate3_old_v33_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            (task / "m4_gate3_audited_artifact_upload_v33.json").write_text(json.dumps({
                "module": "M4", "phase": 3, "request_id": RID, "device_id": DID,
                "stage": "audited_artifact_upload_v33", "status": "audited_artifact_uploaded",
                "artifact": {"remote_sha256_verified": True, "remote_retained": True}
            }), encoding="utf-8")
            with self.assertRaises(Gate4V40Error):
                _find_gate3_v331(task)

    def test_gate3_v331_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            path = task / "m4_gate3_audited_artifact_upload_v331.json"
            path.write_text(json.dumps({
                "module": "M4", "phase": 3, "request_id": RID, "device_id": DID,
                "stage": "audited_artifact_upload_v331", "status": "audited_artifact_uploaded",
                "artifact": {"remote_sha256_verified": True, "remote_retained": True}
            }), encoding="utf-8")
            found, _ = _find_gate3_v331(task)
            self.assertEqual(found, path)

    def test_project_payload_uses_external_spool_and_uploaded_cache_file(self):
        preflight = {
            "remote_artifact": {"remote_name": "abc.gcode.3mf"},
            "source_m3": {"gcode_entry": "Metadata/plate_1.gcode"}
        }
        payload = _build_project_file_payload(preflight, "123")
        command = payload["print"]
        self.assertEqual(command["command"], "project_file")
        self.assertEqual(command["url"], "ftp:///cache/abc.gcode.3mf")
        self.assertFalse(command["use_ams"])
        self.assertEqual(command["ams_mapping"], "")

    def test_stale_preflight_blocks_start_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "outputs" / "m4" / RID
            task.mkdir(parents=True)
            (task / "m4_gate4a_runtime_preflight_v40.json").write_text(json.dumps({
                "status": "runtime_preflight_passed", "device_id": DID,
                "created_unix": time.time() - 999,
                "remote_artifact": {"remote_name": "a.gcode.3mf"},
                "source_m3": {"gcode_entry": "Metadata/plate_1.gcode"}
            }), encoding="utf-8")
            with self.assertRaises(Gate4V40Error):
                run_gate4b(root, "secret", expected_start_phrase())

    def test_wrong_phrase_blocks_start_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Gate4V40Error):
                run_gate4b(Path(tmp), "secret", "WRONG")

    def test_successful_start_never_allows_automatic_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "outputs" / "m4" / RID
            task.mkdir(parents=True)
            preflight = {
                "status": "runtime_preflight_passed", "device_id": DID,
                "created_unix": time.time(),
                "remote_artifact": {"remote_name": "a.gcode.3mf"},
                "source_m3": {"gcode_entry": "Metadata/plate_1.gcode"}
            }
            (task / "m4_gate4a_runtime_preflight_v40.json").write_text(json.dumps(preflight), encoding="utf-8")
            with (
                patch("am_print_executor.gate4_runtime_v40.load_gate1_identity", return_value={"device_id": DID, "ip_address": "192.168.1.5"}),
                patch("am_print_executor.gate4_runtime_v40.publish_first_print_once", return_value={
                    "mqtt_connected": True, "mqtt_publish_count": 1,
                    "project_file_publish_attempted": True, "command_ack_received": True,
                    "command_ack_result": "success", "command_ack": {"result": "success"},
                    "non_idle_state_observed": True, "latest_gcode_state": "PREPARE",
                    "outcome": "print_start_confirmed", "automatic_retry_allowed": False
                })
            ):
                result = run_gate4b(root, "secret", expected_start_phrase())
            self.assertEqual(result["status"], "first_print_started")
            self.assertFalse(result["policy"]["automatic_retry_allowed"])
            self.assertEqual(result["policy"]["print_start_command_count"], 1)


if __name__ == "__main__":
    unittest.main()
