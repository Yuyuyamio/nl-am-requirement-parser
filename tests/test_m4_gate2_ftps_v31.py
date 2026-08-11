from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from am_print_executor.ftps_probe_v31 import (
    EXPECTED_DEVICE_ID,
    Gate2Error,
    ensure_ftps_pin,
    load_gate1_identity,
    run_canary_attempt,
)


class FakeFTP:
    files = {}

    def __init__(self, *args, **kwargs):
        self.cwd_path = "/"

    def connect(self, *args, **kwargs):
        return "ok"

    def login(self, *args, **kwargs):
        return "ok"

    def prot_p(self):
        return "ok"

    def cwd(self, path):
        self.cwd_path = path

    def storbinary(self, command, fp):
        name = command.split(" ", 1)[1]
        self.files[name] = fp.read()

    def nlst(self):
        return list(self.files)

    def retrbinary(self, command, callback):
        name = command.split(" ", 1)[1]
        callback(self.files[name])

    def delete(self, name):
        del self.files[name]

    def quit(self):
        return "ok"


class Gate2Tests(unittest.TestCase):
    def setUp(self):
        FakeFTP.files = {}

    def test_canary_round_trip(self):
        result = run_canary_attempt(
            "192.168.1.5",
            "secret",
            "M2-1E4B2301FADD",
            1,
            ftp_factory=FakeFTP,
        )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertTrue(result.remote_absent_after_delete)

    def test_canary_filename_is_txt(self):
        result = run_canary_attempt(
            "192.168.1.5",
            "secret",
            "M2-1E4B2301FADD",
            1,
            ftp_factory=FakeFTP,
        )
        self.assertTrue(result.remote_name.endswith(".txt"))

    def test_ftps_pin_first_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pin.json"
            pin = ensure_ftps_pin(
                path,
                ip_address="192.168.1.5",
                device_id=EXPECTED_DEVICE_ID,
                fingerprint="AA" * 32,
                confirmation_reader=lambda prompt: "AAAAAAAA",
            )
            self.assertFalse(pin["access_code_stored"])

    def test_changed_ftps_certificate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pin.json"
            ensure_ftps_pin(
                path,
                ip_address="192.168.1.5",
                device_id=EXPECTED_DEVICE_ID,
                fingerprint="AA" * 32,
                confirmation_reader=lambda prompt: "AAAAAAAA",
            )
            with self.assertRaises(Gate2Error):
                ensure_ftps_pin(
                    path,
                    ip_address="192.168.1.5",
                    device_id=EXPECTED_DEVICE_ID,
                    fingerprint="BB" * 32,
                )

    def test_gate1_identity_requires_10_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "outputs" / "m4" / "M2-1E4B2301FADD"
            task.mkdir(parents=True)
            (task / "m4_gate1_autodiscovered_device_v31.json").write_text(
                json.dumps({
                    "request_id": "M2-1E4B2301FADD",
                    "ip_address": "192.168.1.5",
                    "device_id": EXPECTED_DEVICE_ID,
                }),
                encoding="utf-8",
            )
            (task / "m4_gate1_autodiscovery_probe_v31.json").write_text(
                json.dumps({
                    "request_id": "M2-1E4B2301FADD",
                    "status": "readonly_probe_passed",
                    "all_attempts_passed": True,
                    "attempt_count": 1,
                    "device_id_autodiscovered": EXPECTED_DEVICE_ID,
                }),
                encoding="utf-8",
            )
            with self.assertRaises(Gate2Error):
                load_gate1_identity(root, "M2-1E4B2301FADD")


if __name__ == "__main__":
    unittest.main()
