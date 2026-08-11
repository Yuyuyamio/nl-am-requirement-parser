from __future__ import annotations

import unittest
from unittest.mock import patch

from am_print_executor.gate4_runtime_v403 import _gate3_integrity_lock_only


class Gate4V403Tests(unittest.TestCase):
    def test_gate4a_does_not_open_ftps(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("FTPS factory must never be called in Gate4A v4.0.3")

        result = _gate3_integrity_lock_only(
            ip_address="192.168.1.5",
            access_code="secret",
            remote_name="M2-1E4B2301FADD_abc.gcode.3mf",
            expected_sha256="a" * 64,
            expected_size=12345,
            ftp_factory=forbidden,
        )
        self.assertFalse(result["ftps_recheck_performed"])
        self.assertFalse(result["ftps_connection_attempted"])

    def test_locked_sha_is_preserved(self):
        sha = "b" * 64
        result = _gate3_integrity_lock_only(
            ip_address="192.168.1.5",
            access_code="secret",
            remote_name="a.gcode.3mf",
            expected_sha256=sha,
            expected_size=9,
        )
        self.assertEqual(result["remote_sha256_locked"], sha)
        self.assertEqual(result["remote_integrity_basis"], "gate3_v3.3.1_full_remote_sha256_verified")

    def test_invalid_sha_is_rejected(self):
        with self.assertRaises(Exception):
            _gate3_integrity_lock_only(
                ip_address="192.168.1.5",
                access_code="secret",
                remote_name="a.gcode.3mf",
                expected_sha256="bad",
                expected_size=9,
            )

    def test_non_print_artifact_name_is_rejected(self):
        with self.assertRaises(Exception):
            _gate3_integrity_lock_only(
                ip_address="192.168.1.5",
                access_code="secret",
                remote_name="a.txt",
                expected_sha256="a" * 64,
                expected_size=9,
            )


if __name__ == "__main__":
    unittest.main()
