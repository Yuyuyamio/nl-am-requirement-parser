from __future__ import annotations

import unittest

from am_print_executor.r10_final_preflight_v2 import (
    final_safe_state,
)


class R10FinalPreflightV2Tests(unittest.TestCase):
    def test_idle_safe(self):
        result = final_safe_state(
            {
                "gcode_state": "IDLE",
                "print_error": 0,
                "hms": [],
                "sdcard": True,
                "nozzle_target_temper": 0.0,
                "bed_target_temper": 0.0,
            }
        )
        self.assertTrue(result["passed"])

    def test_running_is_not_safe(self):
        result = final_safe_state(
            {
                "gcode_state": "RUNNING",
                "print_error": 0,
                "hms": [],
                "sdcard": True,
                "nozzle_target_temper": 220.0,
                "bed_target_temper": 55.0,
            }
        )
        self.assertFalse(result["passed"])

    def test_failed_terminal_can_be_safe_when_cold_and_clean(self):
        result = final_safe_state(
            {
                "gcode_state": "FAILED",
                "print_error": 0,
                "hms": [],
                "sdcard": True,
                "nozzle_target_temper": 0.0,
                "bed_target_temper": 0.0,
            }
        )
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
