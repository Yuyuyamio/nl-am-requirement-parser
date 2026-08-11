from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from am_print_executor.audited_artifact_upload_v33 import UploadResult, expected_authorization_phrase
from am_print_executor.audited_artifact_upload_v331 import (
    Gate3V331Error,
    _validate_formal_gcode_audit,
    run_gate3_v331,
)

REQUEST_ID = "M2-1E4B2301FADD"
DEVICE_ID = "00M09A3A1700722"


class Gate3V331Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.m4 = self.root / "outputs" / "m4" / REQUEST_ID
        self.m3 = self.root / "outputs" / "m3" / REQUEST_ID
        self.m4.mkdir(parents=True)
        self.m3.mkdir(parents=True)

        (self.m4 / "m4_gate1_autodiscovered_device_v31.json").write_text(json.dumps({
            "request_id": REQUEST_ID, "device_id": DEVICE_ID, "ip_address": "192.168.1.5"
        }), encoding="utf-8")
        (self.m4 / "m4_gate1_autodiscovery_probe_v31.json").write_text(json.dumps({
            "request_id": REQUEST_ID, "status": "readonly_probe_passed",
            "all_attempts_passed": True, "attempt_count": 10,
            "device_id_autodiscovered": DEVICE_ID
        }), encoding="utf-8")
        (self.m4 / "m4_gate2_ftps_probe_v32.json").write_text(json.dumps({
            "request_id": REQUEST_ID,
            "stage": "developer_mode_ftps_canary_probe_v32",
            "status": "ftps_probe_passed",
            "device_id": DEVICE_ID,
            "all_attempts_passed": True,
            "attempt_count": 3,
            "success_count": 3,
            "transport_fix": {
                "implicit_ftps": True,
                "tls_session_reuse_on_data_channel": True,
                "remote_presence_check": "SIZE"
            }
        }), encoding="utf-8")
        (self.m4 / "m4_gate2_ftps_tls_pin_v32.json").write_text(json.dumps({
            "ip_address": "192.168.1.5", "device_id": DEVICE_ID,
            "certificate_sha256": "AA" * 32
        }), encoding="utf-8")

        self.artifact = self.m3 / "controlled_slice_smoke.gcode.3mf"
        with zipfile.ZipFile(self.artifact, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Metadata/plate_1.gcode", "; Bambu Studio\nG1 X1 Y1\n")
            archive.writestr("3D/3dmodel.model", "model")
        self.artifact_sha = hashlib.sha256(self.artifact.read_bytes()).hexdigest()

        self.audit = self.m3 / "m3_gcode_audit.json"
        self.audit.write_text(json.dumps({
            "schema_version": "0.1.0", "module": "M3", "phase": 5,
            "stage": "slice_and_gcode_audit", "request_id": REQUEST_ID,
            "status": "audit_passed", "hard_constraints_passed": True,
            "hard_failure_count": 0,
            "artifact": {
                "path": str(self.artifact), "sha256": self.artifact_sha,
                "size_bytes": self.artifact.stat().st_size,
                "gcode_entries": ["Metadata/plate_1.gcode"]
            }
        }), encoding="utf-8")
        audit_sha = hashlib.sha256(self.audit.read_bytes()).hexdigest()
        self.acceptance = self.m3 / "m3_final_acceptance.json"
        self.acceptance.write_text(json.dumps({
            "schema_version": "0.1.0", "module": "M3", "phase": 7,
            "stage": "final_acceptance", "request_id": REQUEST_ID,
            "status": "m3_accepted", "m3_complete": True,
            "m4_activation_authorized": False, "hard_constraints_passed": True,
            "next_module": "M4", "source_gcode_audit": str(self.audit),
            "source_gcode_audit_sha256": audit_sha
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fake_upload(self, **kwargs):
        return UploadResult(
            remote_name=f"{REQUEST_ID}_{self.artifact_sha[:12]}.gcode.3mf",
            remote_directory="/cache", uploaded_new=True,
            reused_existing_remote=False, remote_size_verified=True,
            remote_sha256_verified=True, remote_sha256=self.artifact_sha,
            remote_size_bytes=self.artifact.stat().st_size, remote_retained=True,
        )

    def test_duplicate_backup_audit_does_not_block_formal_path(self):
        backup = self.root / "outputs" / "m3" / "backup" / "m3_gcode_audit.json"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(self.audit.read_bytes())
        path, report, artifact, sha = _validate_formal_gcode_audit(self.root, REQUEST_ID)
        self.assertEqual(path, self.audit.resolve())
        self.assertEqual(artifact, self.artifact.resolve())
        self.assertEqual(sha, self.artifact_sha)

    def test_formal_audit_must_pass(self):
        data = json.loads(self.audit.read_text(encoding="utf-8"))
        data["status"] = "audit_failed"
        self.audit.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(Gate3V331Error):
            _validate_formal_gcode_audit(self.root, REQUEST_ID)

    def test_artifact_hash_change_blocks(self):
        self.artifact.write_bytes(self.artifact.read_bytes() + b"changed")
        with self.assertRaises(Gate3V331Error):
            _validate_formal_gcode_audit(self.root, REQUEST_ID)

    def test_pipeline_upload_keeps_print_start_locked(self):
        with (
            patch("am_print_executor.audited_artifact_upload_v331.get_server_fingerprint", return_value="AA" * 32),
            patch("am_print_executor.audited_artifact_upload_v331.upload_audited_artifact", side_effect=self.fake_upload),
        ):
            result = run_gate3_v331(
                self.root, REQUEST_ID, "SECRET", expected_authorization_phrase(REQUEST_ID)
            )
        self.assertEqual(result["status"], "audited_artifact_uploaded")
        self.assertEqual(result["source_m3"]["selection_policy"], "deterministic_formal_task_path")
        self.assertTrue(result["policy"]["recursive_audit_discovery_disabled"])
        self.assertEqual(result["policy"]["mqtt_publish_count"], 0)
        self.assertEqual(result["policy"]["print_start_command_count"], 0)
        self.assertFalse(result["policy"]["print_started"])

    def test_wrong_authorization_blocks(self):
        with self.assertRaises(Gate3V331Error):
            run_gate3_v331(self.root, REQUEST_ID, "SECRET", "NO")

    def test_final_acceptance_audit_hash_lock_is_checked(self):
        data = json.loads(self.acceptance.read_text(encoding="utf-8"))
        data["source_gcode_audit_sha256"] = "0" * 64
        self.acceptance.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(Gate3V331Error):
            run_gate3_v331(
                self.root, REQUEST_ID, "SECRET", expected_authorization_phrase(REQUEST_ID)
            )


if __name__ == "__main__":
    unittest.main()
