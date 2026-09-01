from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from am_print_executor.device_gate import DeviceInput, create_device_readiness
from am_print_executor.errors import M4Error

REQUEST_ID = "M2-1E4B2301FADD"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class M4Phase0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task = self.root / "outputs" / "m3" / REQUEST_ID
        self.task.mkdir(parents=True)
        self.package = self.task / f"{REQUEST_ID}_m3_to_m4_handoff.zip"
        self.package.write_bytes(b"PK\x03\x04test-package")
        self.acceptance = self.task / "m3_final_acceptance.json"
        self.acceptance.write_text(json.dumps({
            "schema_version": "0.1.0",
            "module": "M3",
            "phase": 7,
            "stage": "final_acceptance",
            "request_id": REQUEST_ID,
            "status": "m3_accepted",
            "handoff_package": str(self.package),
            "package_sha256": sha256(self.package),
            "m3_complete": True,
            "m4_activation_authorized": False,
            "printer_connection_attempted": False,
            "artifact_uploaded": False,
            "print_started": False,
            "next_module": "M4"
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def valid_input(self, **overrides) -> DeviceInput:
        values = dict(
            printer_ip="192.168.1.50",
            serial_number="01XABC123456789",
            firmware_version="01.11.02.00",
            material_source="ams",
            ams_present=True,
            network_mode="cloud",
            physical_device_observed=True,
            printer_idle=True,
            build_plate_installed=True,
            build_plate_clean=True,
            chamber_clear=True,
            filament_loaded=True,
            nozzle_matches_profile=True,
            material_matches_profile=True,
            authorize_readonly_probe=True,
        )
        values.update(overrides)
        return DeviceInput(**values)

    def test_ready_gate(self) -> None:
        result = create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input())
        self.assertEqual(result["status"], "device_gate_ready")
        self.assertEqual(result["next_phase"], "m4_phase1_readonly_connection_probe")
        report = json.loads(Path(result["device_readiness_file"]).read_text(encoding="utf-8"))
        self.assertFalse(report["security"]["access_code_stored"])
        self.assertFalse(report["security"]["printer_connection_attempted"])

    def test_authorization_can_be_withheld(self) -> None:
        result = create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input(authorize_readonly_probe=False))
        self.assertEqual(result["status"], "readonly_authorization_required")
        self.assertIsNone(result["next_phase"])

    def test_public_ip_rejected(self) -> None:
        with self.assertRaisesRegex(M4Error, "private LAN"):
            create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input(printer_ip="8.8.8.8"))

    def test_failed_operator_check_rejected(self) -> None:
        with self.assertRaisesRegex(M4Error, "build_plate_clean"):
            create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input(build_plate_clean=False))

    def test_ams_contradiction_rejected(self) -> None:
        with self.assertRaisesRegex(M4Error, "AMS material source"):
            create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input(ams_present=False))

    def test_handoff_hash_change_rejected(self) -> None:
        self.package.write_bytes(b"changed")
        with self.assertRaisesRegex(M4Error, "changed after final acceptance"):
            create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input())

    def test_unaccepted_m3_rejected(self) -> None:
        data = json.loads(self.acceptance.read_text(encoding="utf-8"))
        data["status"] = "failed"
        self.acceptance.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(M4Error, "not eligible"):
            create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input())

    def test_repeat_is_idempotent(self) -> None:
        first = create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input())
        second = create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input())
        self.assertEqual(first, second)

    def test_different_repeat_refuses_overwrite(self) -> None:
        create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input())
        with self.assertRaisesRegex(M4Error, "Refusing to overwrite"):
            create_device_readiness(project_root=self.root, final_acceptance_path=self.acceptance, device_input=self.valid_input(firmware_version="01.11.03.00"))


if __name__ == "__main__":
    unittest.main()
