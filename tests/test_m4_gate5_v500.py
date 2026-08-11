import unittest

from am_print_executor.gate5_runtime_v500 import _terminal_evaluation


class Gate5V500Tests(unittest.TestCase):
    def test_idle_clean_passes(self):
        r = _terminal_evaluation({
            "gcode_state": "IDLE",
            "print_error": 0,
            "hms": [],
            "mc_percent": 0,
            "mc_remaining_time": 0,
        })
        self.assertTrue(r["passed"])
        self.assertTrue(r["idle_complete"])

    def test_finish_100_passes(self):
        r = _terminal_evaluation({
            "gcode_state": "FINISH",
            "print_error": 0,
            "hms": [],
            "mc_percent": 100,
            "mc_remaining_time": 0,
        })
        self.assertTrue(r["passed"])
        self.assertTrue(r["finish_complete"])

    def test_finish_99_blocks(self):
        r = _terminal_evaluation({
            "gcode_state": "FINISH",
            "print_error": 0,
            "hms": [],
            "mc_percent": 99,
            "mc_remaining_time": 0,
        })
        self.assertFalse(r["passed"])

    def test_hms_blocks(self):
        r = _terminal_evaluation({
            "gcode_state": "IDLE",
            "print_error": 0,
            "hms": [{"attr": 1, "code": 2}],
            "mc_percent": 0,
            "mc_remaining_time": 0,
        })
        self.assertFalse(r["passed"])

    def test_print_error_blocks(self):
        r = _terminal_evaluation({
            "gcode_state": "IDLE",
            "print_error": 123,
            "hms": [],
            "mc_percent": 0,
            "mc_remaining_time": 0,
        })
        self.assertFalse(r["passed"])


if __name__ == "__main__":
    unittest.main()
