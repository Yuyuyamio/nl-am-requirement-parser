from __future__ import annotations

import unittest

from am_print_executor.gate4_runtime_v402 import _reason_code_failed


class FakeReasonCode:
    def __init__(self, is_failure: bool):
        self.is_failure = is_failure

    def __int__(self):
        raise TypeError("int conversion intentionally unsupported")


class Gate4V402Tests(unittest.TestCase):
    def test_reason_code_object_success_without_int_cast(self):
        self.assertFalse(_reason_code_failed(FakeReasonCode(False)))

    def test_reason_code_object_failure_without_int_cast(self):
        self.assertTrue(_reason_code_failed(FakeReasonCode(True)))

    def test_plain_zero_fallback(self):
        self.assertFalse(_reason_code_failed(0))

    def test_plain_nonzero_fallback(self):
        self.assertTrue(_reason_code_failed(5))


if __name__ == "__main__":
    unittest.main()
