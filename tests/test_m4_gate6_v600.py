import unittest

from am_print_executor.gate6_offline_risk_v600 import classify_snapshot


class Gate6V600Tests(unittest.TestCase):
    def test_running_is_normal(self):
        r = classify_snapshot({"gcode_state": "RUNNING", "print_error": 0, "hms": []})
        self.assertEqual(r["risk_level"], "normal")

    def test_pause_needs_review(self):
        r = classify_snapshot({"gcode_state": "PAUSE", "print_error": 0, "hms": []})
        self.assertEqual(r["risk_level"], "attention")

    def test_failed_is_critical(self):
        r = classify_snapshot({"gcode_state": "FAILED", "print_error": 0, "hms": []})
        self.assertEqual(r["risk_level"], "critical")

    def test_hms_is_critical(self):
        r = classify_snapshot({
            "gcode_state": "RUNNING",
            "print_error": 0,
            "hms": [{"attr": 1, "code": 2}],
        })
        self.assertEqual(r["risk_level"], "critical")

    def test_print_error_is_critical(self):
        r = classify_snapshot({"gcode_state": "RUNNING", "print_error": 5, "hms": []})
        self.assertEqual(r["risk_level"], "critical")

    def test_clean_finish_is_normal(self):
        r = classify_snapshot({
            "gcode_state": "FINISH",
            "print_error": 0,
            "hms": [],
            "mc_percent": 100,
            "mc_remaining_time": 0,
        })
        self.assertEqual(r["decision"], "accept_completed_terminal_state")

    def test_unknown_state_is_held(self):
        r = classify_snapshot({"gcode_state": "NEW_STATE", "print_error": 0, "hms": []})
        self.assertEqual(r["risk_level"], "unknown")
        self.assertEqual(r["decision"], "hold_for_review")


if __name__ == "__main__":
    unittest.main()
