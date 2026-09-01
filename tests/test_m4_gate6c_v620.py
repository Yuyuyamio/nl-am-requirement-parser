import unittest

from am_print_executor.gate6_fusion_decision_v620 import (
    Gate6CV620Error,
    fuse_risks,
)


class Gate6CV620Tests(unittest.TestCase):
    def test_normal_normal_continue(self):
        self.assertEqual(
            fuse_risks("normal", "normal")["decision"],
            "CONTINUE",
        )

    def test_attention_review(self):
        self.assertEqual(
            fuse_risks("attention", "normal")["decision"],
            "REVIEW",
        )

    def test_vision_attention_review(self):
        self.assertEqual(
            fuse_risks("normal", "attention")["decision"],
            "REVIEW",
        )

    def test_vision_critical_pauses_when_device_normal(self):
        self.assertEqual(
            fuse_risks("normal", "critical")["decision"],
            "WOULD_PAUSE",
        )

    def test_device_attention_plus_vision_critical_stops(self):
        self.assertEqual(
            fuse_risks("attention", "critical")["decision"],
            "WOULD_STOP",
        )

    def test_device_critical_stops(self):
        self.assertEqual(
            fuse_risks("critical", "normal")["decision"],
            "WOULD_STOP",
        )

    def test_unknown_device_reviews(self):
        self.assertEqual(
            fuse_risks("unknown", "normal")["decision"],
            "REVIEW",
        )

    def test_unknown_vision_reviews(self):
        self.assertEqual(
            fuse_risks("normal", "unknown")["decision"],
            "REVIEW",
        )

    def test_invalid_risk_blocks(self):
        with self.assertRaises(Gate6CV620Error):
            fuse_risks("banana", "normal")


if __name__ == "__main__":
    unittest.main()
