import unittest
from am_print_executor.gate4b_runtime_v411 import _project_file_payload, _strict_prepublish_check, _classify_outcome

class Gate4BV411Tests(unittest.TestCase):
    def _idle(self):
        return {
            "gcode_state": "IDLE", "print_error": 0, "nozzle_diameter": "0.4",
            "sdcard": True, "hms": [], "mc_percent": 0, "mc_remaining_time": 0
        }

    def test_strict_idle_passes(self):
        self.assertTrue(_strict_prepublish_check(self._idle())["passed"])

    def test_finish_blocks_for_gate4b(self):
        x = self._idle(); x["gcode_state"] = "FINISH"
        self.assertFalse(_strict_prepublish_check(x)["passed"])

    def test_any_hms_blocks(self):
        x = self._idle(); x["hms"] = [{"attr": 83887360, "code": 65543}]
        self.assertFalse(_strict_prepublish_check(x)["passed"])

    def test_payload_matches_local_project_file_shape(self):
        p = _project_file_payload("123", "/cache/a.gcode.3mf", "Metadata/plate_1.gcode")["print"]
        self.assertEqual(p["url"], "ftp:///cache/a.gcode.3mf")
        self.assertEqual(p["param"], "Metadata/plate_1.gcode")
        self.assertFalse(p["use_ams"])
        self.assertEqual(p["bed_type"], "auto")

    def test_rejection_classified(self):
        self.assertEqual(
            _classify_outcome(True, {"result": "failed"}, ["IDLE"]),
            "first_print_start_rejected"
        )

    def test_running_classified_started(self):
        self.assertEqual(
            _classify_outcome(True, {"result": "success"}, ["IDLE", "PREPARE"]),
            "first_print_started"
        )

if __name__ == "__main__":
    unittest.main()
