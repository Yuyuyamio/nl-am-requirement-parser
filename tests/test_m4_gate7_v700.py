import unittest

from am_print_executor.gate7_optimization_recommendation_v700 import (
    Gate7V700Error,
    recommend_for_defect,
)


class Gate7V700Tests(unittest.TestCase):
    def test_warping_recommendation(self):
        r = recommend_for_defect("warping", "REVIEW")
        self.assertGreaterEqual(len(r["recommendations"]), 1)

    def test_stringing_targets_profile(self):
        r = recommend_for_defect("stringing", "REVIEW")
        self.assertTrue(all(
            x["target_layer"] == "bambu_studio_profile_or_physical_setup"
            for x in r["recommendations"]
        ))

    def test_layer_shift_requires_inspection(self):
        r = recommend_for_defect("layer_shift", "WOULD_STOP")
        self.assertEqual(
            r["recommendations"][0]["action"],
            "inspect_before_any_profile_optimization",
        )

    def test_spaghetti_requires_root_cause(self):
        r = recommend_for_defect("spaghetti", "WOULD_STOP")
        self.assertEqual(
            r["recommendations"][0]["action"],
            "classify_root_cause_before_optimization",
        )

    def test_foreign_object_not_auto_applied(self):
        r = recommend_for_defect("foreign_object", "WOULD_STOP")
        self.assertFalse(r["automatic_parameter_application"])

    def test_no_direct_gcode_mutation(self):
        r = recommend_for_defect("warping", "REVIEW")
        self.assertFalse(r["direct_gcode_mutation"])
        self.assertTrue(all(
            x["direct_gcode_edit_allowed"] is False
            for x in r["recommendations"]
        ))

    def test_all_rules_require_human_review(self):
        for defect in [
            "warping", "bed_detachment", "stringing",
            "under_extrusion", "over_extrusion", "blob",
            "layer_shift", "spaghetti", "foreign_object",
        ]:
            r = recommend_for_defect(defect, "REVIEW")
            self.assertTrue(all(
                x["human_review_required"] is True
                for x in r["recommendations"]
            ))

    def test_unknown_defect_blocks(self):
        with self.assertRaises(Gate7V700Error):
            recommend_for_defect("not_a_defect", "REVIEW")

    def test_unknown_decision_blocks(self):
        with self.assertRaises(Gate7V700Error):
            recommend_for_defect("warping", "DO_SOMETHING")

    def test_stop_strategy_requires_resolution(self):
        r = recommend_for_defect("warping", "WOULD_STOP")
        self.assertEqual(
            r["optimization_strategy"],
            "resolve_failure_cause_before_next_slice",
        )


if __name__ == "__main__":
    unittest.main()
