from __future__ import annotations

import unittest

from am_print_executor.gate4_runtime_v401 import _fast_remote_check


class FakeGate4Error(RuntimeError):
    pass


class FakeV40:
    Gate4V40Error = FakeGate4Error
    FTPS_PORT = 990
    FTPS_USERNAME = "bblp"


class FakeFTP:
    def __init__(self, context=None, timeout=None): self.timeout = timeout
    def connect(self, host, port, timeout=None): return None
    def login(self, user, password): return None
    def prot_p(self): return None
    def cwd(self, path): return None
    def size(self, name): return 4
    def retrbinary(self, command, callback): callback(b"test")
    def quit(self): return None
    def close(self): return None


class TimeoutFTP(FakeFTP):
    def retrbinary(self, command, callback):
        raise TimeoutError("read operation timed out")


class Gate4V401Tests(unittest.TestCase):
    def test_full_sha_check_passes(self):
        import hashlib
        result = _fast_remote_check(
            FakeV40, ip_address="192.168.1.5", access_code="secret",
            remote_name="a.gcode.3mf", expected_sha256=hashlib.sha256(b"test").hexdigest(),
            expected_size=4, ftp_factory=FakeFTP,
        )
        self.assertTrue(result["remote_sha256_verified"])
        self.assertTrue(result["gate4a_full_remote_redownload"])
        self.assertEqual(result["ftps_read_timeout_seconds"], 180.0)

    def test_size_mismatch_blocks(self):
        with self.assertRaises(FakeGate4Error):
            _fast_remote_check(
                FakeV40, ip_address="192.168.1.5", access_code="secret",
                remote_name="a.gcode.3mf", expected_sha256="a" * 64,
                expected_size=999, ftp_factory=FakeFTP,
            )

    def test_ftps_timeout_names_full_download_stage(self):
        with self.assertRaisesRegex(FakeGate4Error, "FTPS timeout at stage=full_sha256_download"):
            _fast_remote_check(
                FakeV40, ip_address="192.168.1.5", access_code="secret",
                remote_name="a.gcode.3mf", expected_sha256="a" * 64,
                expected_size=4, ftp_factory=TimeoutFTP,
            )


if __name__ == "__main__":
    unittest.main()
