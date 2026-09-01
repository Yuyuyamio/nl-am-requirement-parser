import unittest

from am_print_executor.gate6_vision_contract_v610 import (
    Gate6BV610Error,
    aggregate_frames,
    classify_frame,
    validate_detection,
)


class Gate6BV610Tests(unittest.TestCase):
    def test_valid_detection(self):
        d = validate_detection({
            "defect_type": "spaghetti",
            "confidence": 0.9,
            "bbox_norm": [0.1, 0.1, 0.8, 0.8],
        })
        self.assertEqual(d["defect_type"], "spaghetti")

    def test_bad_bbox_blocks(self):
        with self.assertRaises(Gate6BV610Error):
            validate_detection({
                "defect_type": "warping",
                "confidence": 0.7,
                "bbox_norm": [0.8, 0.1, 0.2, 0.5],
            })

    def test_clean_frame_normal(self):
        r = classify_frame({"frame_id": "x", "detections": []})
        self.assertEqual(r["risk_level"], "normal")

    def test_critical_frame(self):
        r = classify_frame({
            "frame_id": "x",
            "detections": [{
                "defect_type": "bed_detachment",
                "confidence": 0.91,
                "bbox_norm": [0.1, 0.1, 0.9, 0.9],
            }],
        })
        self.assertEqual(r["risk_level"], "critical")

    def test_two_critical_frames_make_critical_sequence(self):
        frames = [
            {"frame_id": "1", "detections": [{
                "defect_type": "spaghetti",
                "confidence": 0.9,
                "bbox_norm": [0.1, 0.1, 0.8, 0.8],
            }]},
            {"frame_id": "2", "detections": [{
                "defect_type": "spaghetti",
                "confidence": 0.92,
                "bbox_norm": [0.1, 0.1, 0.8, 0.8],
            }]},
        ]
        r = aggregate_frames(frames)
        self.assertEqual(r["sequence_risk"], "critical")

    def test_one_critical_frame_only_attention_sequence(self):
        frames = [
            {"frame_id": "1", "detections": []},
            {"frame_id": "2", "detections": [{
                "defect_type": "layer_shift",
                "confidence": 0.88,
                "bbox_norm": [0.1, 0.2, 0.8, 0.8],
            }]},
            {"frame_id": "3", "detections": []},
        ]
        r = aggregate_frames(frames)
        self.assertEqual(r["sequence_risk"], "attention")

    def test_unknown_defect_blocks(self):
        with self.assertRaises(Gate6BV610Error):
            validate_detection({
                "defect_type": "made_up_defect",
                "confidence": 0.9,
                "bbox_norm": None,
            })


if __name__ == "__main__":
    unittest.main()
