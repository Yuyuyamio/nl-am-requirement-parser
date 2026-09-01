from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"

GATE6B_REPORT = "m4_gate6b_offline_vision_contract_v610.json"
GATE6C_REPORT = "m4_gate6c_offline_fusion_decision_v620.json"
OUTPUT_REPORT = "m4_gate7_offline_optimization_recommendation_v700.json"


class Gate7V700Error(RuntimeError):
    pass


# Gate7 never edits G-code directly. Recommendations target a slicer/profile layer.
# Numeric deltas are intentionally NOT invented here because they depend on the
# actual material, nozzle, plate, and process profile loaded for the next slice.
RULES: dict[str, list[dict[str, Any]]] = {
    "warping": [
        {
            "priority": 1,
            "parameter_family": "build_plate_adhesion",
            "action": "increase_adhesion_support",
            "candidate_changes": ["add_or_increase_brim", "review_first_layer_adhesion"],
            "requires_reslice": True,
            "human_review_required": True,
        },
        {
            "priority": 2,
            "parameter_family": "first_layer_speed",
            "action": "decrease_within_profile_limits",
            "candidate_changes": ["reduce_first_layer_speed"],
            "requires_reslice": True,
            "human_review_required": True,
        },
        {
            "priority": 3,
            "parameter_family": "bed_temperature",
            "action": "review_or_increase_within_material_and_plate_limits",
            "candidate_changes": ["adjust_bed_temperature"],
            "requires_reslice": True,
            "human_review_required": True,
        },
    ],
    "bed_detachment": [
        {
            "priority": 1,
            "parameter_family": "build_plate_condition",
            "action": "inspect_before_parameter_change",
            "candidate_changes": ["clean_plate", "verify_plate_type", "verify_first_layer"],
            "requires_reslice": False,
            "human_review_required": True,
        },
        {
            "priority": 2,
            "parameter_family": "build_plate_adhesion",
            "action": "increase_adhesion_support",
            "candidate_changes": ["add_or_increase_brim"],
            "requires_reslice": True,
            "human_review_required": True,
        },
        {
            "priority": 3,
            "parameter_family": "bed_temperature",
            "action": "review_or_increase_within_material_and_plate_limits",
            "candidate_changes": ["adjust_bed_temperature"],
            "requires_reslice": True,
            "human_review_required": True,
        },
    ],
    "stringing": [
        {
            "priority": 1,
            "parameter_family": "material_condition",
            "action": "inspect_before_parameter_change",
            "candidate_changes": ["verify_filament_dryness"],
            "requires_reslice": False,
            "human_review_required": True,
        },
        {
            "priority": 2,
            "parameter_family": "retraction",
            "action": "retune_within_machine_and_material_profile",
            "candidate_changes": ["review_retraction_distance", "review_retraction_speed"],
            "requires_reslice": True,
            "human_review_required": True,
        },
        {
            "priority": 3,
            "parameter_family": "nozzle_temperature",
            "action": "review_or_decrease_within_material_profile",
            "candidate_changes": ["adjust_nozzle_temperature"],
            "requires_reslice": True,
            "human_review_required": True,
        },
    ],
    "under_extrusion": [
        {
            "priority": 1,
            "parameter_family": "extrusion_path",
            "action": "inspect_before_parameter_change",
            "candidate_changes": ["check_nozzle_for_partial_clog", "check_filament_feed"],
            "requires_reslice": False,
            "human_review_required": True,
        },
        {
            "priority": 2,
            "parameter_family": "volumetric_flow",
            "action": "verify_not_exceeding_material_profile",
            "candidate_changes": ["reduce_peak_volumetric_demand_if_needed"],
            "requires_reslice": True,
            "human_review_required": True,
        },
        {
            "priority": 3,
            "parameter_family": "flow_calibration",
            "action": "recalibrate_before_flow_ratio_change",
            "candidate_changes": ["run_flow_calibration"],
            "requires_reslice": False,
            "human_review_required": True,
        },
    ],
    "over_extrusion": [
        {
            "priority": 1,
            "parameter_family": "flow_calibration",
            "action": "recalibrate_before_profile_change",
            "candidate_changes": ["run_flow_calibration"],
            "requires_reslice": False,
            "human_review_required": True,
        },
        {
            "priority": 2,
            "parameter_family": "flow_ratio",
            "action": "review_or_decrease_after_calibration",
            "candidate_changes": ["adjust_flow_ratio"],
            "requires_reslice": True,
            "human_review_required": True,
        },
    ],
    "blob": [
        {
            "priority": 1,
            "parameter_family": "nozzle_and_extrusion",
            "action": "inspect_before_parameter_change",
            "candidate_changes": ["inspect_nozzle_buildup", "inspect_prime_and_purge_behavior"],
            "requires_reslice": False,
            "human_review_required": True,
        },
        {
            "priority": 2,
            "parameter_family": "retraction_and_temperature",
            "action": "retune_within_material_profile",
            "candidate_changes": ["review_retraction", "review_nozzle_temperature"],
            "requires_reslice": True,
            "human_review_required": True,
        },
    ],
    "layer_shift": [
        {
            "priority": 1,
            "parameter_family": "mechanical_and_collision",
            "action": "inspect_before_any_profile_optimization",
            "candidate_changes": [
                "inspect_belt_and_motion_system",
                "inspect_collision_or_part_curling",
                "inspect_toolhead_obstruction",
            ],
            "requires_reslice": False,
            "human_review_required": True,
        },
        {
            "priority": 2,
            "parameter_family": "speed_and_acceleration",
            "action": "review_only_after_mechanical_causes_are_excluded",
            "candidate_changes": ["review_speed", "review_acceleration"],
            "requires_reslice": True,
            "human_review_required": True,
        },
    ],
    "spaghetti": [
        {
            "priority": 1,
            "parameter_family": "failure_root_cause",
            "action": "classify_root_cause_before_optimization",
            "candidate_changes": [
                "inspect_bed_adhesion",
                "inspect_support_failure",
                "inspect_part_detachment",
                "inspect_geometry_or_collision",
            ],
            "requires_reslice": False,
            "human_review_required": True,
        },
    ],
    "foreign_object": [
        {
            "priority": 1,
            "parameter_family": "physical_workspace",
            "action": "remove_obstruction_and_reinspect",
            "candidate_changes": ["clear_build_area", "inspect_toolhead_and_bed"],
            "requires_reslice": False,
            "human_review_required": True,
        },
    ],
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate7V700Error(f"Required report missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate7V700Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise Gate7V700Error(f"Expected JSON object: {path}")
    return obj


def recommend_for_defect(defect_type: str, fused_decision: str) -> dict[str, Any]:
    defect = str(defect_type or "").strip()
    decision = str(fused_decision or "").strip().upper()

    if defect not in RULES:
        raise Gate7V700Error(f"Unsupported defect_type: {defect!r}")

    if decision not in {"CONTINUE", "REVIEW", "WOULD_PAUSE", "WOULD_STOP"}:
        raise Gate7V700Error(f"Unsupported fused decision: {decision!r}")

    recommendations = []
    for rule in RULES[defect]:
        item = dict(rule)
        item["auto_apply_allowed"] = False
        item["target_layer"] = "bambu_studio_profile_or_physical_setup"
        item["direct_gcode_edit_allowed"] = False
        recommendations.append(item)

    if decision == "WOULD_STOP":
        strategy = "resolve_failure_cause_before_next_slice"
    elif decision == "WOULD_PAUSE":
        strategy = "review_current_failure_then_prepare_next_slice_adjustments"
    elif decision == "REVIEW":
        strategy = "human_review_before_next_slice_adjustments"
    else:
        strategy = "record_observation_no_forced_change"

    return {
        "defect_type": defect,
        "fused_decision": decision,
        "optimization_strategy": strategy,
        "recommendations": recommendations,
        "automatic_parameter_application": False,
        "direct_gcode_mutation": False,
    }


def _validation_cases() -> list[dict[str, Any]]:
    return [
        {"defect": "warping", "decision": "REVIEW"},
        {"defect": "bed_detachment", "decision": "WOULD_PAUSE"},
        {"defect": "stringing", "decision": "REVIEW"},
        {"defect": "under_extrusion", "decision": "REVIEW"},
        {"defect": "over_extrusion", "decision": "REVIEW"},
        {"defect": "blob", "decision": "REVIEW"},
        {"defect": "layer_shift", "decision": "WOULD_STOP"},
        {"defect": "spaghetti", "decision": "WOULD_STOP"},
        {"defect": "foreign_object", "decision": "WOULD_STOP"},
    ]


def _validate_rule_safety(rec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if rec.get("automatic_parameter_application") is not False:
        errors.append("automatic_parameter_application_must_be_false")
    if rec.get("direct_gcode_mutation") is not False:
        errors.append("direct_gcode_mutation_must_be_false")
    for item in rec.get("recommendations", []):
        if item.get("auto_apply_allowed") is not False:
            errors.append("rule_auto_apply_allowed_must_be_false")
        if item.get("direct_gcode_edit_allowed") is not False:
            errors.append("rule_direct_gcode_edit_allowed_must_be_false")
        if item.get("human_review_required") is not True:
            errors.append("human_review_required_must_be_true")
    return errors


def run(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    task_dir = project_root / "outputs" / "m4" / REQUEST_ID

    gate6b_path = task_dir / GATE6B_REPORT
    gate6c_path = task_dir / GATE6C_REPORT

    gate6b = _load_json(gate6b_path)
    gate6c = _load_json(gate6c_path)

    if gate6b.get("version") != "6.1.0":
        raise Gate7V700Error("Gate6B version lock is not 6.1.0.")
    if gate6b.get("status") != "offline_vision_contract_validated":
        raise Gate7V700Error("Gate6B did not pass.")
    if gate6c.get("version") != "6.2.0":
        raise Gate7V700Error("Gate6C version lock is not 6.2.0.")
    if gate6c.get("status") != "offline_fusion_decision_validated":
        raise Gate7V700Error("Gate6C did not pass.")

    allowed_from_gate6b = set(
        gate6b.get("vision_contract", {}).get("allowed_defects", [])
    )
    missing = sorted(set(RULES) - allowed_from_gate6b)
    if missing:
        raise Gate7V700Error(
            f"Gate7 rules contain defects not allowed by Gate6B: {missing}"
        )

    case_results = []
    all_passed = True

    for case in _validation_cases():
        rec = recommend_for_defect(case["defect"], case["decision"])
        safety_errors = _validate_rule_safety(rec)
        passed = len(safety_errors) == 0 and len(rec["recommendations"]) >= 1
        all_passed = all_passed and passed
        case_results.append({
            "defect_type": case["defect"],
            "fused_decision": case["decision"],
            "passed": passed,
            "safety_errors": safety_errors,
            "result": rec,
        })

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 7,
        "version": "7.0.0",
        "stage": "offline_optimization_recommendation_validation",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": (
            "offline_optimization_recommendation_validated"
            if all_passed
            else "offline_optimization_recommendation_failed"
        ),
        "created_unix": time.time(),
        "source_gate6b": {
            "report_file": str(gate6b_path),
            "version": gate6b.get("version"),
            "status": gate6b.get("status"),
        },
        "source_gate6c": {
            "report_file": str(gate6c_path),
            "version": gate6c.get("version"),
            "status": gate6c.get("status"),
        },
        "recommendation_contract": {
            "defect_types": sorted(RULES),
            "target_layer": "Bambu Studio machine/material/process profile or physical setup",
            "direct_gcode_editing": False,
            "automatic_parameter_application": False,
            "numeric_delta_policy": (
                "No numeric parameter delta is invented without the actual "
                "machine/material/plate/process profile for the next slice."
            ),
            "every_recommendation_requires_human_review": True,
        },
        "validation_case_count": len(case_results),
        "validation_pass_count": sum(1 for x in case_results if x["passed"]),
        "validation_results": case_results,
        "policy": {
            "office_only": True,
            "printer_connection_attempted": False,
            "camera_connection_attempted": False,
            "mqtt_publish_count": 0,
            "ftps_connection_attempted": False,
            "gcode_file_modified": False,
            "bambu_profile_modified": False,
            "automatic_reprint_enabled": False,
            "device_control_count": 0,
        },
        "deferred_to_final_lab": {
            "gate5b_live_mqtt_monitor": True,
            "real_x1c_camera_stream": True,
            "real_ai_model_live_inference": True,
            "live_pause_stop_validation": True,
        },
        "next_phase": "m4_gate7b_profile_patch_contract_or_gate8_offline_end_to_end",
    }

    output = task_dir / OUTPUT_REPORT
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise Gate7V700Error(f"Refusing to overwrite existing report: {output}")

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report | {"report_file": str(output)}
