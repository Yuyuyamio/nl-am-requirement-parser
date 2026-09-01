from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"

GATE7_REPORT = "m4_gate7_offline_optimization_recommendation_v700.json"
OUTPUT_REPORT = "m4_gate7b_profile_patch_contract_v710.json"


class Gate7BV710Error(RuntimeError):
    pass


ALLOWED_TARGETS = {
    "process_profile",
    "filament_profile",
    "machine_profile",
    "physical_setup",
}

# Maps Gate7 parameter families to the layer that is allowed to change.
TARGET_MAP = {
    "build_plate_adhesion": "process_profile",
    "first_layer_speed": "process_profile",
    "bed_temperature": "filament_profile",
    "build_plate_condition": "physical_setup",
    "material_condition": "physical_setup",
    "retraction": "filament_profile",
    "nozzle_temperature": "filament_profile",
    "extrusion_path": "physical_setup",
    "volumetric_flow": "filament_profile",
    "flow_calibration": "physical_setup",
    "flow_ratio": "filament_profile",
    "nozzle_and_extrusion": "physical_setup",
    "retraction_and_temperature": "filament_profile",
    "mechanical_and_collision": "physical_setup",
    "speed_and_acceleration": "process_profile",
    "failure_root_cause": "physical_setup",
    "physical_workspace": "physical_setup",
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate7BV710Error(f"Required report missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate7BV710Error(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise Gate7BV710Error(f"Expected JSON object: {path}")
    return obj


def build_patch_item(
    *,
    defect_type: str,
    fused_decision: str,
    recommendation: dict[str, Any],
    patch_index: int,
) -> dict[str, Any]:
    family = str(recommendation.get("parameter_family") or "").strip()
    if family not in TARGET_MAP:
        raise Gate7BV710Error(f"Unsupported parameter_family: {family!r}")

    target = TARGET_MAP[family]
    if target not in ALLOWED_TARGETS:
        raise Gate7BV710Error(f"Unsupported patch target: {target!r}")

    candidates = recommendation.get("candidate_changes")
    if not isinstance(candidates, list) or not candidates:
        raise Gate7BV710Error("candidate_changes must be a non-empty list.")

    return {
        "patch_id": f"{defect_type}-{patch_index:02d}",
        "defect_type": defect_type,
        "fused_decision": fused_decision,
        "parameter_family": family,
        "target_layer": target,
        "action": recommendation.get("action"),
        "candidate_changes": list(candidates),
        "requires_reslice": recommendation.get("requires_reslice") is True,
        "human_review_required": True,
        "auto_apply_allowed": False,
        "direct_gcode_edit_allowed": False,
        "numeric_delta": None,
        "numeric_delta_source": None,
        "application_status": "PROPOSED_ONLY",
    }


def build_patch_bundle(case: dict[str, Any]) -> dict[str, Any]:
    defect = str(case.get("defect_type") or "").strip()
    fused_decision = str(case.get("fused_decision") or "").strip().upper()
    result = case.get("result")

    if not defect:
        raise Gate7BV710Error("defect_type is missing.")
    if fused_decision not in {"CONTINUE", "REVIEW", "WOULD_PAUSE", "WOULD_STOP"}:
        raise Gate7BV710Error(f"Unsupported fused_decision: {fused_decision!r}")
    if not isinstance(result, dict):
        raise Gate7BV710Error("Gate7 result object missing.")

    recs = result.get("recommendations")
    if not isinstance(recs, list) or not recs:
        raise Gate7BV710Error("Gate7 recommendations missing.")

    items = [
        build_patch_item(
            defect_type=defect,
            fused_decision=fused_decision,
            recommendation=rec,
            patch_index=i + 1,
        )
        for i, rec in enumerate(recs)
    ]

    return {
        "defect_type": defect,
        "fused_decision": fused_decision,
        "bundle_status": "PROPOSED_ONLY",
        "patch_item_count": len(items),
        "requires_reslice": any(x["requires_reslice"] for x in items),
        "requires_physical_action": any(x["target_layer"] == "physical_setup" for x in items),
        "requires_human_review": True,
        "items": items,
    }


def validate_bundle(bundle: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if bundle.get("bundle_status") != "PROPOSED_ONLY":
        errors.append("bundle_status_must_be_PROPOSED_ONLY")
    if bundle.get("requires_human_review") is not True:
        errors.append("requires_human_review_must_be_true")

    items = bundle.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items_missing")
        return errors

    for item in items:
        if item.get("application_status") != "PROPOSED_ONLY":
            errors.append("item_application_status_must_be_PROPOSED_ONLY")
        if item.get("auto_apply_allowed") is not False:
            errors.append("auto_apply_allowed_must_be_false")
        if item.get("direct_gcode_edit_allowed") is not False:
            errors.append("direct_gcode_edit_allowed_must_be_false")
        if item.get("human_review_required") is not True:
            errors.append("human_review_required_must_be_true")
        if item.get("numeric_delta") is not None:
            errors.append("numeric_delta_must_remain_null_without_real_profile")
        if item.get("target_layer") not in ALLOWED_TARGETS:
            errors.append("invalid_target_layer")

    return errors


def run(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    task_dir = project_root / "outputs" / "m4" / REQUEST_ID
    gate7_path = task_dir / GATE7_REPORT
    gate7 = _load_json(gate7_path)

    if gate7.get("version") != "7.0.0":
        raise Gate7BV710Error("Gate7 version lock is not 7.0.0.")
    if gate7.get("status") != "offline_optimization_recommendation_validated":
        raise Gate7BV710Error("Gate7 did not pass.")
    if gate7.get("request_id") != REQUEST_ID:
        raise Gate7BV710Error("Gate7 request_id mismatch.")

    validation_results = gate7.get("validation_results")
    if not isinstance(validation_results, list) or not validation_results:
        raise Gate7BV710Error("Gate7 validation_results missing.")

    bundles = []
    all_passed = True

    for case in validation_results:
        bundle = build_patch_bundle(case)
        safety_errors = validate_bundle(bundle)
        passed = len(safety_errors) == 0
        all_passed = all_passed and passed
        bundles.append({
            "defect_type": bundle["defect_type"],
            "passed": passed,
            "safety_errors": safety_errors,
            "patch_bundle": bundle,
        })

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "7B",
        "version": "7.1.0",
        "stage": "profile_patch_contract_validation",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": (
            "profile_patch_contract_validated"
            if all_passed
            else "profile_patch_contract_failed"
        ),
        "created_unix": time.time(),
        "source_gate7": {
            "report_file": str(gate7_path),
            "version": gate7.get("version"),
            "status": gate7.get("status"),
        },
        "contract": {
            "allowed_target_layers": sorted(ALLOWED_TARGETS),
            "patch_status": "PROPOSED_ONLY",
            "human_review_required": True,
            "auto_apply_allowed": False,
            "direct_gcode_edit_allowed": False,
            "numeric_delta_policy": (
                "numeric_delta remains null until a real Bambu Studio "
                "machine/material/process profile is loaded and validated"
            ),
            "reslice_required_when_profile_changes": True,
        },
        "bundle_count": len(bundles),
        "bundle_pass_count": sum(1 for x in bundles if x["passed"]),
        "bundles": bundles,
        "policy": {
            "office_only": True,
            "printer_connection_attempted": False,
            "camera_connection_attempted": False,
            "mqtt_publish_count": 0,
            "ftps_connection_attempted": False,
            "profile_file_modified": False,
            "gcode_file_modified": False,
            "automatic_reslice_started": False,
            "automatic_reprint_started": False,
        },
        "deferred_to_final_lab": {
            "gate5b_live_mqtt_monitor": True,
            "real_x1c_camera_stream": True,
            "real_ai_model_live_inference": True,
            "live_pause_stop_validation": True,
        },
        "next_phase": "m4_gate8_offline_end_to_end_simulation",
    }

    output = task_dir / OUTPUT_REPORT
    if output.exists():
        raise Gate7BV710Error(f"Refusing to overwrite existing report: {output}")

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report | {"report_file": str(output)}
