import unittest
from am_print_executor.gate4_runtime_v407 import _runtime_preflight_v407


def base(state="FINISH"):
    return {
        "gcode_state": state,
        "print_error": 0,
        "nozzle_diameter": "0.4",
        "sdcard": True,
        "hms": [],
        "mc_percent": 100,
        "mc_remaining_time": 0,
    }


class Gate4V407Tests(unittest.TestCase):
    def test_empty_hms_passes(self):
        self.assertTrue(_runtime_preflight_v407(base())["passed"])

    def test_known_historical_auth_hms_passes(self):
        x = base()
        x["hms"] = [{"attr": 83887360, "code": 65543}]
        r = _runtime_preflight_v407(x)
        self.assertTrue(r["passed"])
        self.assertTrue(r["checks"]["only_known_historical_auth_hms"])

    def test_unknown_hms_blocks(self):
        x = base()
        x["hms"] = [{"attr": 1, "code": 2}]
        self.assertFalse(_runtime_preflight_v407(x)["passed"])

    def test_running_blocks(self):
        self.assertFalse(_runtime_preflight_v407(base("RUNNING"))["passed"])

    def test_print_error_blocks(self):
        x = base()
        x["print_error"] = 1
        self.assertFalse(_runtime_preflight_v407(x)["passed"])


if __name__ == "__main__":
    unittest.main()
