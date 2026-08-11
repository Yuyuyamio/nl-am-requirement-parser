import unittest

from am_print_executor.gate7b_profile_patch_contract_v710 import (
    Gate7BV710Error,
    build_patch_bundle,
    build_patch_item,
    validate_bundle,
)


class Gate7BV710Tests(unittest.TestCase):
    def test_process_profile_mapping(self):
        item = build_patch_item(
            defect_type="warping",
            fused_decision="REVIEW",
            recommendation={
                "parameter_family": "first_layer_speed",
                "action": "decrease_within_profile_limits",
                "candidate_changes": ["reduce_first_layer_speed"],
                "requires_reslice": True,
            },
            patch_index=1,
        )
        self.assertEqual(item["target_layer"], "process_profile")

    def test_filament_profile_mapping(self):
        item = build_patch_item(
            defect_type="stringing",
            fused_decision="REVIEW",
            recommendation={
                "parameter_family": "retraction",
                "action": "retune_within_machine_and_material_profile",
                "candidate_changes": ["review_retraction_distance"],
                "requires_reslice": True,
            },
            patch_index=1,
        )
        self.assertEqual(item["target_layer"], "filament_profile")

    def test_physical_setup_mapping(self):
        item = build_patch_item(
            defect_type="layer_shift",
            fused_decision="WOULD_STOP",
            recommendation={
                "parameter_family": "mechanical_and_collision",
                "action": "inspect_before_any_profile_optimization",
                "candidate_changes": ["inspect_belt_and_motion_system"],
                "requires_reslice": False,
            },
            patch_index=1,
        )
        self.assertEqual(item["target_layer"], "physical_setup")

    def test_numeric_delta_is_null(self):
        item = build_patch_item(
            defect_type="warping",
            fused_decision="REVIEW",
            recommendation={
                "parameter_family": "bed_temperature",
                "action": "review_or_increase_within_material_and_plate_limits",
                "candidate_changes": ["adjust_bed_temperature"],
                "requires_reslice": True,
            },
            patch_index=1,
        )
        self.assertIsNone(item["numeric_delta"])

    def test_no_auto_apply(self):
        item = build_patch_item(
            defect_type="warping",
            fused_decision="REVIEW",
            recommendation={
                "parameter_family": "build_plate_adhesion",
                "action": "increase_adhesion_support",
                "candidate_changes": ["add_or_increase_brim"],
                "requires_reslice": True,
            },
            patch_index=1,
        )
        self.assertFalse(item["auto_apply_allowed"])

    def test_bundle_requires_review(self):
        bundle = build_patch_bundle({
            "defect_type": "warping",
            "fused_decision": "REVIEW",
            "result": {
                "recommendations": [{
                    "parameter_family": "build_plate_adhesion",
                    "action": "increase_adhesion_support",
                    "candidate_changes": ["add_or_increase_brim"],
                    "requires_reslice": True,
                }]
            }
        })
        self.assertTrue(bundle["requires_human_review"])

    def test_bundle_validation_passes(self):
        bundle = build_patch_bundle({
            "defect_type": "stringing",
            "fused_decision": "REVIEW",
            "result": {
                "recommendations": [{
                    "parameter_family": "material_condition",
                    "action": "inspect_before_parameter_change",
                    "candidate_changes": ["verify_filament_dryness"],
                    "requires_reslice": False,
                }]
            }
        })
        self.assertEqual(validate_bundle(bundle), [])

    def test_unknown_parameter_family_blocks(self):
        with self.assertRaises(Gate7BV710Error):
            build_patch_item(
                defect_type="x",
                fused_decision="REVIEW",
                recommendation={
                    "parameter_family": "not_real",
                    "action": "x",
                    "candidate_changes": ["x"],
                    "requires_reslice": False,
                },
                patch_index=1,
            )


if __name__ == "__main__":
    unittest.main()
