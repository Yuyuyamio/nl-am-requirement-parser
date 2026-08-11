import unittest

from am_print_executor.gate8c_model_screening_v820 import (
    Gate8CV820Error,
    aggregate_predictions,
    evaluate_labeled_results,
)


class Gate8CV820Tests(unittest.TestCase):
    def test_labeled_metrics_perfect(self):
        s = [{"prediction": "success"} for _ in range(3)]
        f = [{"prediction": "failure"} for _ in range(3)]
        r = evaluate_labeled_results(s, f)
        self.assertEqual(r["success_recall"], 1.0)
        self.assertEqual(r["failure_recall"], 1.0)

    def test_failure_predicted_success_lowers_recall(self):
        s = [{"prediction": "success"}]
        f = [{"prediction": "success"}, {"prediction": "failure"}]
        r = evaluate_labeled_results(s, f)
        self.assertEqual(r["failure_recall"], 0.5)

    def test_unknown_counts_wrong_for_accuracy(self):
        s = [{"prediction": "unknown"}]
        f = [{"prediction": "failure"}]
        r = evaluate_labeled_results(s, f)
        self.assertEqual(r["overall_accuracy_unknown_counted_wrong"], 0.5)

    def test_two_failure_frames_escalate(self):
        r = aggregate_predictions(["failure", "failure", "success"])
        self.assertEqual(r["vision_risk"], "critical")
        self.assertEqual(r["fusion_compatible_decision"], "WOULD_PAUSE")

    def test_one_failure_frame_review(self):
        r = aggregate_predictions(["success", "failure", "success"])
        self.assertEqual(r["vision_risk"], "attention")
        self.assertEqual(r["fusion_compatible_decision"], "REVIEW")

    def test_all_success_continue(self):
        r = aggregate_predictions(["success", "success", "success"])
        self.assertEqual(r["vision_risk"], "normal")
        self.assertEqual(r["fusion_compatible_decision"], "CONTINUE")

    def test_unknown_frame_review(self):
        r = aggregate_predictions(["success", "unknown", "success"])
        self.assertEqual(r["vision_risk"], "attention")

    def test_empty_sequence_blocks(self):
        with self.assertRaises(Gate8CV820Error):
            aggregate_predictions([])


if __name__ == "__main__":
    unittest.main()
