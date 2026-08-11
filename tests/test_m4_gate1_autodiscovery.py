from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from am_print_executor.readonly_probe_autodiscovery import (
    ProbeError,
    ProbeResult,
    _extract_device_id,
    _extract_summary,
    run_probe,
    validate_private_ipv4,
)


class Gate1AutodiscoveryTests(unittest.TestCase):
    def test_device_id_from_topic(self):
        self.assertEqual(_extract_device_id("device/ABC123/report"), "ABC123")
        self.assertIsNone(_extract_device_id("device/ABC123/request"))

    def test_status_summary(self):
        result = _extract_summary({"print": {"gcode_state": "IDLE", "nozzle_temper": 30.0}})
        self.assertEqual(result["gcode_state"], "IDLE")

    def test_public_ip_rejected(self):
        with self.assertRaises(ProbeError):
            validate_private_ipv4("8.8.8.8")

    def test_no_serial_required_and_secret_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fake = ProbeResult(True, True, True, "AUTO123", {"gcode_state": "IDLE"}, None, 0.1)
            with patch("am_print_executor.readonly_probe_autodiscovery.get_tls_fingerprint", return_value="AA" * 32), \
                 patch("am_print_executor.readonly_probe_autodiscovery.passive_discover", return_value=fake):
                result = run_probe(project, "M2-1E4B2301FADD", "172.16.61.6", "SECRET", attempts=1)
            self.assertEqual(result["device_id_autodiscovered"], "AUTO123")
            self.assertEqual(result["policy"]["mqtt_publish_count"], 0)
            report = Path(result["report_file"]).read_text(encoding="utf-8")
            self.assertNotIn("SECRET", report)

    def test_ten_attempts_advance_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fake = ProbeResult(True, True, True, "AUTO123", {"gcode_state": "IDLE"}, None, 0.1)
            with patch("am_print_executor.readonly_probe_autodiscovery.get_tls_fingerprint", return_value="AA" * 32), \
                 patch("am_print_executor.readonly_probe_autodiscovery.passive_discover", return_value=fake), \
                 patch("am_print_executor.readonly_probe_autodiscovery.time.sleep", return_value=None):
                result = run_probe(project, "M2-1E4B2301FADD", "172.16.61.6", "SECRET", attempts=10)
            self.assertEqual(result["success_count"], 10)
            self.assertEqual(result["next_phase"], "m4_gate2_ftps_upload_probe")

    def test_device_identity_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fake1 = ProbeResult(True, True, True, "AUTO123", {"gcode_state": "IDLE"}, None, 0.1)
            fake2 = ProbeResult(True, True, True, "AUTO456", {"gcode_state": "IDLE"}, None, 0.1)
            with patch("am_print_executor.readonly_probe_autodiscovery.get_tls_fingerprint", return_value="AA" * 32), \
                 patch("am_print_executor.readonly_probe_autodiscovery.passive_discover", return_value=fake1):
                run_probe(project, "M2-1E4B2301FADD", "172.16.61.6", "SECRET", attempts=1)
            with patch("am_print_executor.readonly_probe_autodiscovery.get_tls_fingerprint", return_value="AA" * 32), \
                 patch("am_print_executor.readonly_probe_autodiscovery.passive_discover", return_value=fake2):
                with self.assertRaises(ProbeError):
                    run_probe(project, "M2-1E4B2301FADD", "172.16.61.6", "SECRET", attempts=1)


if __name__ == "__main__":
    unittest.main()
