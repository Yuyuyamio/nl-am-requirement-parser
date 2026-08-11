from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from am_print_executor.audited_artifact_upload_v1 import (
    EXPECTED_DEVICE_ID,
    Gate3Error,
    UploadResult,
    resolve_audited_artifact,
    run_gate3,
    validate_gate2_v32,
)

REQUEST_ID = "M2-1E4B2301FADD"


class Gate3Tests(unittest.TestCase):
    def make_project(self, root: Path) -> Path:
        m4 = root / "outputs" / "m4" / REQUEST_ID
        m3 = root / "outputs" / "m3" / REQUEST_ID
        m4.mkdir(parents=True)
        m3.mkdir(parents=True)

        artifact = m3 / "controlled_slice_smoke.gcode.3mf"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "G1 X1 Y1\n")
        import hashlib
        sha = hashlib.sha256(artifact.read_bytes()).hexdigest()

        (m4 / "m4_gate2_ftps_probe_v32.json").write_text(json.dumps({
            "module": "M4", "phase": 2, "stage": "developer_mode_ftps_canary_probe_v32",
            "request_id": REQUEST_ID, "status": "ftps_probe_passed", "printer_ip": "172.16.61.6",
            "device_id": EXPECTED_DEVICE_ID, "ftps_port": 990, "remote_directory": "/cache",
            "transport_fix": {"implicit_ftps": True, "tls_session_reuse_on_data_channel": True, "remote_presence_check": "SIZE"},
            "attempt_count": 3, "success_count": 3, "all_attempts_passed": True,
            "policy": {"canary_only": True, "printable": False, "print_artifact_uploaded": False,
                       "mqtt_publish_count": 0, "control_command_count": 0, "print_start_command_count": 0,
                       "access_code_stored": False, "cleanup_required": True},
            "attempts": [
                {"success": True, "uploaded": True, "remote_size_verified": True, "downloaded": True,
                 "sha256_verified": True, "deleted": True, "remote_absent_after_delete": True, "error": None}
                for _ in range(3)
            ],
            "next_phase": "m4_gate3_audited_artifact_upload"
        }), encoding="utf-8")

        (m3 / "m3_gcode_audit.json").write_text(json.dumps({
            "module": "M3", "phase": 5, "stage": "slice_and_gcode_audit", "request_id": REQUEST_ID,
            "status": "audit_passed", "hard_constraints_passed": True, "hard_failure_count": 0,
            "artifact": {"path": str(artifact), "sha256": sha, "size_bytes": artifact.stat().st_size,
                         "gcode_entries": ["Metadata/plate_1.gcode"]}
        }), encoding="utf-8")
        (m3 / "m3_final_acceptance.json").write_text(json.dumps({
            "module": "M3", "phase": 7, "stage": "final_acceptance", "request_id": REQUEST_ID,
            "status": "m3_accepted", "m3_complete": True, "m4_activation_authorized": False,
            "printer_connection_attempted": False, "artifact_uploaded": False, "print_started": False,
            "next_module": "M4"
        }), encoding="utf-8")
        return artifact

    def test_gate2_v32_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.make_project(root)
            report = validate_gate2_v32(root, REQUEST_ID)
            self.assertEqual(report["device_id"], EXPECTED_DEVICE_ID)

    def test_gate2_less_than_three_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.make_project(root)
            path = root / "outputs" / "m4" / REQUEST_ID / "m4_gate2_ftps_probe_v32.json"
            data = json.loads(path.read_text()); data["attempt_count"] = 2
            path.write_text(json.dumps(data))
            with self.assertRaises(Gate3Error): validate_gate2_v32(root, REQUEST_ID)

    def test_only_audit_passed_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.make_project(root)
            path = root / "outputs" / "m3" / REQUEST_ID / "m3_gcode_audit.json"
            data = json.loads(path.read_text()); data["status"] = "audit_failed"
            path.write_text(json.dumps(data))
            with patch("am_print_executor.audited_artifact_upload_v1.upload_and_verify"):
                with self.assertRaises(Gate3Error): run_gate3(root, REQUEST_ID, "SECRET")

    def test_artifact_hash_change_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); artifact = self.make_project(root)
            artifact.write_bytes(artifact.read_bytes() + b"changed")
            audit_path = root / "outputs" / "m3" / REQUEST_ID / "m3_gcode_audit.json"
            audit = json.loads(audit_path.read_text())
            with self.assertRaises(Gate3Error): resolve_audited_artifact(audit_path, audit)

    def test_non_3mf_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.make_project(root)
            audit_path = root / "outputs" / "m3" / REQUEST_ID / "m3_gcode_audit.json"
            audit = json.loads(audit_path.read_text())
            p = root / "x.txt"; p.write_text("x")
            import hashlib
            audit["artifact"] = {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size_bytes": 1, "gcode_entries": ["x"]}
            with self.assertRaises(Gate3Error): resolve_audited_artifact(audit_path, audit)

    def test_success_records_no_print_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); artifact = self.make_project(root)
            import hashlib
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            fake = UploadResult(True, False, True, True, True, True, f"/cache/m4_{REQUEST_ID}_{sha[:12]}.gcode.3mf", artifact.stat().st_size, artifact.stat().st_size, sha, sha)
            with patch("am_print_executor.audited_artifact_upload_v1.upload_and_verify", return_value=fake):
                result = run_gate3(root, REQUEST_ID, "SECRET")
            self.assertEqual(result["status"], "audited_artifact_uploaded")
            self.assertEqual(result["policy"]["mqtt_publish_count"], 0)
            self.assertEqual(result["policy"]["print_start_command_count"], 0)
            self.assertFalse(result["policy"]["access_code_stored"])
            self.assertFalse(result["policy"]["print_started"])

    def test_existing_remote_reuse_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); artifact = self.make_project(root)
            import hashlib
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            fake = UploadResult(False, True, True, True, True, True, f"/cache/m4_{REQUEST_ID}_{sha[:12]}.gcode.3mf", artifact.stat().st_size, artifact.stat().st_size, sha, sha)
            with patch("am_print_executor.audited_artifact_upload_v1.upload_and_verify", return_value=fake):
                result = run_gate3(root, REQUEST_ID, "SECRET")
            self.assertTrue(result["artifact"]["reused_existing_remote"])
            self.assertTrue(result["artifact"]["remote_retained"])

    def test_wrong_device_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.make_project(root)
            path = root / "outputs" / "m4" / REQUEST_ID / "m4_gate2_ftps_probe_v32.json"
            data = json.loads(path.read_text()); data["device_id"] = "OTHER"
            path.write_text(json.dumps(data))
            with self.assertRaises(Gate3Error): run_gate3(root, REQUEST_ID, "SECRET")


if __name__ == "__main__":
    unittest.main()
