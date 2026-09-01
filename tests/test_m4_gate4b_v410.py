import unittest

from am_print_executor.gate4b_runtime_v410 import (
    _classify_outcome,
    _project_file_payload,
)


class Gate4BV410Tests(unittest.TestCase):
    def test_project_payload_uses_cache_and_no_ams(self):
        p = _project_file_payload(
            "123",
            "/cache/example.gcode.3mf",
            "Metadata/plate_1.gcode",
        )
        x = p["print"]
        self.assertEqual(x["command"], "project_file")
        self.assertEqual(x["url"], "ftp:///cache/example.gcode.3mf")
        self.assertEqual(x["param"], "Metadata/plate_1.gcode")
        self.assertFalse(x["use_ams"])
        self.assertEqual(x["ams_mapping"], "")

    def test_not_sent(self):
        self.assertEqual(
            _classify_outcome(published=False, ack=None, observed_states=[]),
            "first_print_not_sent",
        )

    def test_rejected(self):
        self.assertEqual(
            _classify_outcome(
                published=True,
                ack={"result": "fail"},
                observed_states=[],
            ),
            "first_print_start_rejected",
        )

    def test_running_means_started(self):
        self.assertEqual(
            _classify_outcome(
                published=True,
                ack=None,
                observed_states=["FINISH", "PREPARE"],
            ),
            "first_print_started",
        )

    def test_success_ack_without_transition_is_unknown(self):
        self.assertEqual(
            _classify_outcome(
                published=True,
                ack={"result": "success"},
                observed_states=["FINISH"],
            ),
            "first_print_start_outcome_unknown",
        )


if __name__ == "__main__":
    unittest.main()
