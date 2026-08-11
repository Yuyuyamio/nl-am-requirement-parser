import unittest
from am_print_executor.gate4_runtime_v406 import _runtime_preflight_v406


def base(state):
    return {
        "gcode_state": state,
        "print_error": 0,
        "nozzle_diameter": "0.4",
        "sdcard": True,
        "hms": [],
        "mc_percent": 100,
        "mc_remaining_time": 0,
    }


class Gate4V406Tests(unittest.TestCase):
    def test_idle_passes(self):
        r = _runtime_preflight_v406(base("IDLE"))
        self.assertTrue(r["passed"])
        self.assertTrue(r["checks"]["gcode_state_idle"])

    def test_finish_complete_passes(self):
        r = _runtime_preflight_v406(base("FINISH"))
        self.assertTrue(r["passed"])
        self.assertTrue(r["checks"]["gcode_state_finish_completed"])

    def test_finish_not_complete_blocks(self):
        x = base("FINISH")
        x["mc_percent"] = 99
        r = _runtime_preflight_v406(x)
        self.assertFalse(r["passed"])

    def test_running_blocks(self):
        r = _runtime_preflight_v406(base("RUNNING"))
        self.assertFalse(r["passed"])

    def test_pause_blocks(self):
        r = _runtime_preflight_v406(base("PAUSE"))
        self.assertFalse(r["passed"])

    def test_failed_blocks(self):
        r = _runtime_preflight_v406(base("FAILED"))
        self.assertFalse(r["passed"])

    def test_error_blocks(self):
        x = base("FINISH")
        x["print_error"] = 123
        r = _runtime_preflight_v406(x)
        self.assertFalse(r["passed"])

    def test_hms_blocks(self):
        x = base("FINISH")
        x["hms"] = [{"code": 1}]
        r = _runtime_preflight_v406(x)
        self.assertFalse(r["passed"])


if __name__ == "__main__":
    unittest.main()
