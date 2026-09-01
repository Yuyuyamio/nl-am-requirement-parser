from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from am_print_executor.readonly_probe import (
    DeviceIdentity,
    ProbeAttempt,
    ReadonlyProbeError,
    ensure_tls_pin,
    extract_status_summary,
    format_fingerprint,
    run_readonly_probe,
    validate_private_ipv4,
    validate_serial,
)


class ReadonlyProbeTests(unittest.TestCase):
    def test_private_ipv4_accepts_lan_address(self) -> None:
        self.assertEqual(validate_private_ipv4("192.168.1.20"), "192.168.1.20")

    def test_public_ip_is_rejected(self) -> None:
        with self.assertRaises(ReadonlyProbeError):
            validate_private_ipv4("8.8.8.8")

    def test_serial_validation(self) -> None:
        self.assertEqual(validate_serial("01P00A123456789"), "01P00A123456789")
        with self.assertRaises(ReadonlyProbeError):
            validate_serial("bad serial!")

    def test_fingerprint_formatting(self) -> None:
        self.assertEqual(format_fingerprint("AABBCCDD"), "AA:BB:CC:DD")

    def test_first_use_pin_requires_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pin.json"
            pin = ensure_tls_pin(
                path,
                DeviceIdentity("192.168.1.5", "SERIAL123"),
                "AA" * 32,
                confirmation_reader=lambda prompt: "AAAAAAAA",
            )
            self.assertTrue(path.exists())
            self.assertFalse(pin["access_code_stored"])

    def test_existing_pin_rejects_changed_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pin.json"
            ensure_tls_pin(
                path,
                DeviceIdentity("192.168.1.5", "SERIAL123"),
                "AA" * 32,
                confirmation_reader=lambda prompt: "AAAAAAAA",
            )
            with self.assertRaises(ReadonlyProbeError):
                ensure_tls_pin(
                    path,
                    DeviceIdentity("192.168.1.5", "SERIAL123"),
                    "BB" * 32,
                )

    def test_extract_status_summary(self) -> None:
        summary = extract_status_summary(
            {
                "print": {
                    "gcode_state": "IDLE",
                    "nozzle_temper": 31.2,
                    "bed_temper": 29.5,
                }
            }
        )
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["gcode_state"], "IDLE")
        self.assertTrue(summary["report_has_print_object"])

    def test_run_probe_writes_no_secret_and_advances_after_ten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            acceptance = project / "outputs" / "m3" / "task" / "m3_final_acceptance.json"
            acceptance.parent.mkdir(parents=True)
            acceptance.write_text(
                json.dumps(
                    {
                        "request_id": "M2-1E4B2301FADD",
                        "status": "m3_accepted",
                        "m3_complete": True,
                        "m4_activation_authorized": False,
                    }
                ),
                encoding="utf-8",
            )
            successful = ProbeAttempt(
                attempt=0,
                success=True,
                connected=True,
                subscribed=True,
                message_received=True,
                elapsed_seconds=0.1,
                error=None,
                status_summary={"gcode_state": "IDLE"},
            )
            with (
                patch(
                    "am_print_executor.readonly_probe.get_tls_certificate_fingerprint",
                    return_value="AA" * 32,
                ),
                patch(
                    "am_print_executor.readonly_probe.run_single_passive_attempt",
                    return_value=successful,
                ),
                patch("am_print_executor.readonly_probe.time.sleep", return_value=None),
            ):
                result = run_readonly_probe(
                    project,
                    "M2-1E4B2301FADD",
                    DeviceIdentity("192.168.1.5", "SERIAL123"),
                    "SECRET-CODE",
                    attempts=10,
                    confirmation_reader=lambda prompt: "AAAAAAAA",
                )
            self.assertEqual(result["status"], "readonly_probe_passed")
            self.assertEqual(result["next_phase"], "m4_gate2_ftps_upload_probe")
            report = Path(result["report_file"]).read_text(encoding="utf-8")
            self.assertNotIn("SECRET-CODE", report)
            self.assertEqual(result["probe_policy"]["mqtt_publish_count"], 0)


if __name__ == "__main__":
    unittest.main()
