import unittest

from am_print_executor.gate8_offline_end_to_end_v800 import (
    Gate8V800Error,
    _fusion_decision,
    _expected_patch_layer,
)


class Gate8V800Tests(unittest.TestCase):
    def test_normal_path_continue(self):
        self.assertEqual(_fusion_decision("normal", "normal"), "CONTINUE")

    def test_vision_attention_review(self):
        self.assertEqual(_fusion_decision("normal", "attention"), "REVIEW")

    def test_vision_critical_pause(self):
        self.assertEqual(_fusion_decision("normal", "critical"), "WOULD_PAUSE")

    def test_attention_plus_critical_stop(self):
        self.assertEqual(_fusion_decision("attention", "critical"), "WOULD_STOP")

    def test_device_critical_stop(self):
        self.assertEqual(_fusion_decision("critical", "normal"), "WOULD_STOP")

    def test_unknown_review(self):
        self.assertEqual(_fusion_decision("unknown", "normal"), "REVIEW")

    def test_warping_patch_layer(self):
        self.assertEqual(_expected_patch_layer("warping"), "process_profile")

    def test_stringing_patch_layer(self):
        self.assertEqual(_expected_patch_layer("stringing"), "physical_setup")

    def test_unknown_defect_blocks(self):
        with self.assertRaises(Gate8V800Error):
            _expected_patch_layer("not_real")


if __name__ == "__main__":
    unittest.main()
