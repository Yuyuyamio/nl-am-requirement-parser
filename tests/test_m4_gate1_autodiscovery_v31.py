from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from am_print_executor.readonly_probe_autodiscovery_v31 import (
    ProbeResult,
    _extract_device_id,
    _extract_summary,
    run_probe,
    validate_private_ipv4,
)


class FakeReasonCode:
    def __init__(self, is_failure: bool) -> None:
        self.is_failure = is_failure

    def __str__(self) -> str:
        return "failure" if self.is_failure else "success"


class Gate1V31Tests(unittest.TestCase):
    def test_private_ip(self) -> None:
        self.assertEqual(validate_private_ipv4("172.16.61.6"), "172.16.61.6")

    def test_extract_device_id(self) -> None:
        self.assertEqual(_extract_device_id("device/ABC123/report"), "ABC123")
        self.assertIsNone(_extract_device_id("device/ABC123/request"))

    def test_extract_summary(self) -> None:
        summary = _extract_summary({"print": {"gcode_state": "IDLE", "nozzle_temper": 30.0}})
        self.assertEqual(summary["gcode_state"], "IDLE")

    def test_reason_code_contract_matches_paho_v2(self) -> None:
        self.assertFalse(FakeReasonCode(False).is_failure)
        self.assertTrue(FakeReasonCode(True).is_failure)

    def test_passive_discover_reuses_public_connection_module(self) -> None:
        status = type(
            "Status",
            (),
            {
                "connected": True,
                "subscribed": True,
                "printer_state_observed": True,
                "device_id": "DEVICE123",
                "status_summary": {"gcode_state": "IDLE"},
                "elapsed_seconds": 0.1,
            },
        )()
        with patch(
            "am_print_executor.readonly_probe_autodiscovery_v31.connect_printer",
            return_value=status,
        ) as connector:
            from am_print_executor.readonly_probe_autodiscovery_v31 import (
                passive_discover,
            )

            result = passive_discover("172.16.61.6", "SUPERSECRET")
        self.assertTrue(result.message_received)
        self.assertEqual(result.device_id, "DEVICE123")
        self.assertIsNone(result.error)
        self.assertEqual(connector.call_count, 1)

    def test_success_writes_v31_report_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            success = ProbeResult(True, True, True, "DEVICE123", {"gcode_state": "IDLE"}, None, 0.1)
            with patch("am_print_executor.readonly_probe_autodiscovery_v31.get_tls_fingerprint", return_value="AA"*32), patch(
                "am_print_executor.readonly_probe_autodiscovery_v31.passive_discover", return_value=success
            ):
                result = run_probe(project, "M2-1E4B2301FADD", "172.16.61.6", "SUPERSECRET", attempts=1)
            self.assertEqual(result["status"], "readonly_probe_passed")
            self.assertIn("v31", result["report_file"])
            text = Path(result["report_file"]).read_text(encoding="utf-8")
            self.assertNotIn("SUPERSECRET", text)
            self.assertEqual(result["policy"]["mqtt_publish_count"], 0)

    def test_ten_successes_advance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            success = ProbeResult(True, True, True, "DEVICE123", {"gcode_state": "IDLE"}, None, 0.1)
            with patch("am_print_executor.readonly_probe_autodiscovery_v31.get_tls_fingerprint", return_value="AA"*32), patch(
                "am_print_executor.readonly_probe_autodiscovery_v31.passive_discover", return_value=success
            ), patch("am_print_executor.readonly_probe_autodiscovery_v31.time.sleep", return_value=None):
                result = run_probe(project, "M2-1E4B2301FADD", "172.16.61.6", "SUPERSECRET", attempts=10)
            self.assertEqual(result["success_count"], 10)
            self.assertEqual(result["next_phase"], "m4_gate2_ftps_upload_probe")


if __name__ == "__main__":
    unittest.main()
