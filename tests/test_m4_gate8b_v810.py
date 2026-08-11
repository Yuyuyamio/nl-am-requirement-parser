import unittest

from am_print_executor.gate8b_printguard_adapter_v810 import (
    Gate8BV810Error,
    validate_printguard_response,
)


class Gate8BV810Tests(unittest.TestCase):
    def test_success_maps_normal(self):
        r = validate_printguard_response({
            "prediction": "success",
            "distances": {"success": 0.4, "failure": 1.2},
            "margin": 0.8,
            "defect_score": 0.25,
        })
        self.assertEqual(r["vision_risk"], "normal")
        self.assertIsNone(r["defect_type"])

    def test_failure_maps_single_frame_attention(self):
        r = validate_printguard_response({
            "prediction": "failure",
            "distances": {"success": 1.3, "failure": 0.5},
            "margin": 0.8,
            "defect_score": 0.82,
        })
        self.assertEqual(r["vision_risk"], "attention")
        self.assertIsNone(r["defect_type"])

    def test_unknown_maps_unknown(self):
        r = validate_printguard_response({
            "prediction": "unknown",
            "distances": {"success": 1.0, "failure": 1.0},
            "margin": 0.0,
            "defect_score": 0.5,
        })
        self.assertEqual(r["vision_risk"], "unknown")

    def test_bad_prediction_blocks(self):
        with self.assertRaises(Gate8BV810Error):
            validate_printguard_response({
                "prediction": "spaghetti",
                "distances": {"success": 0.4, "failure": 1.2},
                "margin": 0.8,
                "defect_score": 0.2,
            })

    def test_missing_distances_blocks(self):
        with self.assertRaises(Gate8BV810Error):
            validate_printguard_response({
                "prediction": "success",
                "margin": 0.8,
            })

    def test_bad_score_blocks(self):
        with self.assertRaises(Gate8BV810Error):
            validate_printguard_response({
                "prediction": "failure",
                "distances": {"success": 1.3, "failure": 0.5},
                "margin": 0.8,
                "defect_score": 1.5,
            })

    def test_score_optional(self):
        r = validate_printguard_response({
            "prediction": "success",
            "distances": {"success": 0.3, "failure": 1.0},
            "margin": 0.7,
        })
        self.assertIsNone(r["defect_score"])


if __name__ == "__main__":
    unittest.main()
