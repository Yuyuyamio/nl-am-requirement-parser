from __future__ import annotations

import json
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import am_print_executor.ftps_probe_v32 as ftps_module
from am_print_executor.ftps_probe_v32 import (
    EXPECTED_DEVICE_ID,
    Gate2V32Error,
    SessionReuseImplicitFTP_TLS,
    load_gate1_identity,
    validate_or_create_ftps_pin,
)


class Gate2V32Tests(unittest.TestCase):
    def test_pin_atomic_write_retries_transient_windows_file_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "pin.json"
            replace_calls = 0
            real_replace = ftps_module.os.replace

            def transient_replace(source, target):
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls < 3:
                    raise PermissionError(5, "transient Windows file lock")
                real_replace(source, target)

            with (
                patch.object(
                    ftps_module.os,
                    "replace",
                    side_effect=transient_replace,
                ),
                patch.object(ftps_module.time, "sleep") as sleep,
            ):
                ftps_module._write_json_atomic(destination, {"ok": True})

            self.assertEqual(replace_calls, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(
                json.loads(destination.read_text(encoding="utf-8")),
                {"ok": True},
            )

    def test_session_reuse_is_passed_to_data_wrap(self):
        ftp = SessionReuseImplicitFTP_TLS()
        ftp.host = "192.168.1.5"
        ftp._prot_p = True

        control = MagicMock(spec=ssl.SSLSocket)
        control.session = object()
        ftp.sock = control

        context = MagicMock()
        wrapped = MagicMock()
        context.wrap_socket.return_value = wrapped
        ftp.context = context

        raw_data = MagicMock()
        with patch("ftplib.FTP.ntransfercmd", return_value=(raw_data, 123)):
            conn, size = ftp.ntransfercmd("STOR x.txt")

        self.assertIs(conn, wrapped)
        self.assertEqual(size, 123)
        context.wrap_socket.assert_called_once_with(
            raw_data,
            server_hostname="192.168.1.5",
            session=control.session,
        )

    def test_v31_pin_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            (task / "m4_gate2_ftps_tls_pin_v31.json").write_text(
                json.dumps({
                    "ip_address": "192.168.1.5",
                    "device_id": EXPECTED_DEVICE_ID,
                    "certificate_sha256": "AA" * 32,
                }),
                encoding="utf-8",
            )
            pin = validate_or_create_ftps_pin(
                task,
                ip_address="192.168.1.5",
                device_id=EXPECTED_DEVICE_ID,
                fingerprint="AA" * 32,
            )
            self.assertEqual(pin["device_id"], EXPECTED_DEVICE_ID)

    def test_v31_pin_changed_certificate_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            (task / "m4_gate2_ftps_tls_pin_v31.json").write_text(
                json.dumps({
                    "ip_address": "192.168.1.5",
                    "device_id": EXPECTED_DEVICE_ID,
                    "certificate_sha256": "AA" * 32,
                }),
                encoding="utf-8",
            )
            with self.assertRaises(Gate2V32Error):
                validate_or_create_ftps_pin(
                    task,
                    ip_address="192.168.1.5",
                    device_id=EXPECTED_DEVICE_ID,
                    fingerprint="BB" * 32,
                )

    def test_authenticated_mqtt_fingerprint_can_create_ftps_pin_without_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            prompt = MagicMock(side_effect=AssertionError("must not prompt"))
            fingerprint = "CC" * 32

            pin = validate_or_create_ftps_pin(
                task,
                ip_address="192.168.1.5",
                device_id=EXPECTED_DEVICE_ID,
                fingerprint=fingerprint,
                confirmation_reader=prompt,
                trusted_fingerprint=fingerprint,
            )

            prompt.assert_not_called()
            self.assertEqual(pin["certificate_sha256"], fingerprint)
            self.assertTrue(
                (task / "m4_gate2_ftps_tls_pin_v32.json").is_file()
            )

    def test_mismatched_authenticated_mqtt_fingerprint_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Gate2V32Error):
                validate_or_create_ftps_pin(
                    Path(tmp),
                    ip_address="192.168.1.5",
                    device_id=EXPECTED_DEVICE_ID,
                    fingerprint="CC" * 32,
                    trusted_fingerprint="DD" * 32,
                )

    def test_gate1_requires_expected_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "outputs" / "m4" / "M2-1E4B2301FADD"
            task.mkdir(parents=True)
            (task / "m4_gate1_autodiscovered_device_v31.json").write_text(
                json.dumps({
                    "request_id": "M2-1E4B2301FADD",
                    "ip_address": "192.168.1.5",
                    "device_id": "OTHERDEVICE",
                }),
                encoding="utf-8",
            )
            (task / "m4_gate1_autodiscovery_probe_v31.json").write_text(
                json.dumps({
                    "request_id": "M2-1E4B2301FADD",
                    "status": "readonly_probe_passed",
                    "all_attempts_passed": True,
                    "attempt_count": 10,
                    "device_id_autodiscovered": "OTHERDEVICE",
                }),
                encoding="utf-8",
            )
            with self.assertRaises(Gate2V32Error):
                load_gate1_identity(root, "M2-1E4B2301FADD")


if __name__ == "__main__":
    unittest.main()
