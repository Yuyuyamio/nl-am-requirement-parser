from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"

REPORTS = {
    "gate6a": ("m4_gate6a_offline_risk_validation_v600.json", "6.0.0", "offline_risk_engine_validated"),
    "gate6b": ("m4_gate6b_offline_vision_contract_v610.json", "6.1.0", "offline_vision_contract_validated"),
    "gate6c": ("m4_gate6c_offline_fusion_decision_v620.json", "6.2.0", "offline_fusion_decision_validated"),
    "gate7": ("m4_gate7_offline_optimization_recommendation_v700.json", "7.0.0", "offline_optimization_recommendation_validated"),
    "gate7b": ("m4_gate7b_profile_patch_contract_v710.json", "7.1.0", "profile_patch_contract_validated"),
}

OUTPUT_REPORT = "m4_gate8_offline_end_to_end_validation_v800.json"


class Gate8V800Error(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate8V800Error(f"Required report missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate8V800Error(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise Gate8V800Error(f"Expected JSON object: {path}")
    return obj


def _load_locked_reports(task_dir: Path) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}

    for key, (filename, version, status) in REPORTS.items():
        path = task_dir / filename
        report = _load_json(path)

        if report.get("version") != version:
            raise Gate8V800Error(
                f"{key} version lock mismatch: expected {version}, got {report.get('version')!r}"
            )
        if report.get("status") != status:
            raise Gate8V800Error(
                f"{key} status lock mismatch: expected {status}, got {report.get('status')!r}"
            )
        if report.get("request_id") != REQUEST_ID:
            raise Gate8V800Error(f"{key} request_id mismatch.")

        loaded[key] = {
            "path": str(path),
            "report": report,
        }

    return loaded


def _fusion_decision(device_risk: str, vision_risk: str) -> str:
    d = device_risk.lower()
    v = vision_risk.lower()

    if d == "critical":
        return "WOULD_STOP"
    if d == "unknown" or v == "unknown":
        return "REVIEW"
    if d == "attention" and v == "critical":
        return "WOULD_STOP"
    if d == "normal" and v == "critical":
        return "WOULD_PAUSE"
    if d == "attention" or v == "attention":
        return "REVIEW"
    if d == "normal" and v == "normal":
        return "CONTINUE"

    raise Gate8V800Error(
        f"Unsupported fusion input: device={device_risk!r}, vision={vision_risk!r}"
    )


def _expected_patch_layer(defect: str) -> str:
    mapping = {
        "warping": "process_profile",
        "bed_detachment": "physical_setup",
        "stringing": "physical_setup",
        "under_extrusion": "physical_setup",
        "over_extrusion": "physical_setup",
        "blob": "physical_setup",
        "layer_shift": "physical_setup",
        "spaghetti": "physical_setup",
        "foreign_object": "physical_setup",
    }
    if defect not in mapping:
        raise Gate8V800Error(f"Unsupported defect in Gate8 scenario: {defect}")
    return mapping[defect]


def _scenarios() -> list[dict[str, Any]]:
    return [
        {
            "name": "clean_print_path",
            "device_risk": "normal",
            "vision_risk": "normal",
            "defect_type": None,
            "expected_decision": "CONTINUE",
            "expected_patch": False,
        },
        {
            "name": "stringing_review_path",
            "device_risk": "normal",
            "vision_risk": "attention",
            "defect_type": "stringing",
            "expected_decision": "REVIEW",
            "expected_patch": True,
        },
        {
            "name": "warping_review_path",
            "device_risk": "attention",
            "vision_risk": "normal",
            "defect_type": "warping",
            "expected_decision": "REVIEW",
            "expected_patch": True,
        },
        {
            "name": "spaghetti_pause_path",
            "device_risk": "normal",
            "vision_risk": "critical",
            "defect_type": "spaghetti",
            "expected_decision": "WOULD_PAUSE",
            "expected_patch": True,
        },
        {
            "name": "layer_shift_stop_path",
            "device_risk": "attention",
            "vision_risk": "critical",
            "defect_type": "layer_shift",
            "expected_decision": "WOULD_STOP",
            "expected_patch": True,
        },
        {
            "name": "device_failure_stop_path",
            "device_risk": "critical",
            "vision_risk": "normal",
            "defect_type": "under_extrusion",
            "expected_decision": "WOULD_STOP",
            "expected_patch": True,
        },
        {
            "name": "unknown_sensor_review_path",
            "device_risk": "unknown",
            "vision_risk": "normal",
            "defect_type": None,
            "expected_decision": "REVIEW",
            "expected_patch": False,
        },
    ]


def _find_gate7_case(gate7: dict[str, Any], defect: str) -> dict[str, Any]:
    rows = gate7.get("validation_results")
    if not isinstance(rows, list):
        raise Gate8V800Error("Gate7 validation_results missing.")

    matches = [x for x in rows if x.get("defect_type") == defect]
    if len(matches) != 1:
        raise Gate8V800Error(
            f"Expected exactly one Gate7 validation result for {defect!r}, found {len(matches)}"
        )
    return matches[0]


def _find_gate7b_bundle(gate7b: dict[str, Any], defect: str) -> dict[str, Any]:
    rows = gate7b.get("bundles")
    if not isinstance(rows, list):
        raise Gate8V800Error("Gate7B bundles missing.")

    matches = [x for x in rows if x.get("defect_type") == defect]
    if len(matches) != 1:
        raise Gate8V800Error(
            f"Expected exactly one Gate7B bundle for {defect!r}, found {len(matches)}"
        )
    return matches[0]


def run(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    task_dir = project_root / "outputs" / "m4" / REQUEST_ID
    locked = _load_locked_reports(task_dir)

    gate6b = locked["gate6b"]["report"]
    gate7 = locked["gate7"]["report"]
    gate7b = locked["gate7b"]["report"]

    allowed_defects = set(
        gate6b.get("vision_contract", {}).get("allowed_defects", [])
    )

    results: list[dict[str, Any]] = []
    all_passed = True

    for scenario in _scenarios():
        decision = _fusion_decision(
            scenario["device_risk"],
            scenario["vision_risk"],
        )

        errors: list[str] = []

        if decision != scenario["expected_decision"]:
            errors.append(
                f"fusion_decision_mismatch:{decision}!={scenario['expected_decision']}"
            )

        defect = scenario["defect_type"]
        patch_trace: dict[str, Any] | None = None

        if scenario["expected_patch"]:
            if defect not in allowed_defects:
                errors.append(f"defect_not_allowed_by_gate6b:{defect}")
            else:
                g7 = _find_gate7_case(gate7, defect)
                g7b = _find_gate7b_bundle(gate7b, defect)

                if g7.get("passed") is not True:
                    errors.append("gate7_case_not_passed")
                if g7b.get("passed") is not True:
                    errors.append("gate7b_bundle_not_passed")

                patch_bundle = g7b.get("patch_bundle")
                if not isinstance(patch_bundle, dict):
                    errors.append("gate7b_patch_bundle_missing")
                else:
                    items = patch_bundle.get("items", [])
                    if not isinstance(items, list) or not items:
                        errors.append("gate7b_patch_items_missing")
                    else:
                        if any(x.get("auto_apply_allowed") is not False for x in items):
                            errors.append("auto_apply_must_remain_false")
                        if any(x.get("direct_gcode_edit_allowed") is not False for x in items):
                            errors.append("direct_gcode_edit_must_remain_false")
                        if any(x.get("application_status") != "PROPOSED_ONLY" for x in items):
                            errors.append("patch_status_must_remain_proposed_only")
                        if any(x.get("numeric_delta") is not None for x in items):
                            errors.append("numeric_delta_must_remain_null")

                    expected_layer = _expected_patch_layer(defect)
                    actual_layers = sorted({
                        x.get("target_layer") for x in items
                        if isinstance(x, dict)
                    })

                    if expected_layer not in actual_layers:
                        errors.append(
                            f"expected_patch_layer_missing:{expected_layer}"
                        )

                    patch_trace = {
                        "gate7_strategy": g7.get("result", {}).get("optimization_strategy"),
                        "gate7b_bundle_status": patch_bundle.get("bundle_status"),
                        "target_layers": actual_layers,
                        "requires_reslice": patch_bundle.get("requires_reslice"),
                        "requires_physical_action": patch_bundle.get("requires_physical_action"),
                        "patch_item_count": patch_bundle.get("patch_item_count"),
                    }
        else:
            if defect is not None:
                errors.append("unexpected_defect_for_no_patch_scenario")

        passed = len(errors) == 0
        all_passed = all_passed and passed

        results.append({
            "name": scenario["name"],
            "device_risk": scenario["device_risk"],
            "vision_risk": scenario["vision_risk"],
            "defect_type": defect,
            "expected_decision": scenario["expected_decision"],
            "actual_decision": decision,
            "expected_patch": scenario["expected_patch"],
            "patch_trace": patch_trace,
            "errors": errors,
            "passed": passed,
        })

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 8,
        "version": "8.0.0",
        "stage": "office_offline_end_to_end_validation",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": (
            "office_offline_end_to_end_validated"
            if all_passed
            else "office_offline_end_to_end_failed"
        ),
        "created_unix": time.time(),
        "locked_inputs": {
            key: {
                "report_file": data["path"],
                "version": data["report"].get("version"),
                "status": data["report"].get("status"),
            }
            for key, data in locked.items()
        },
        "pipeline_under_test": [
            "Gate6A device telemetry risk classification",
            "Gate6B vision detection contract",
            "Gate6C device+vision fusion decision",
            "Gate7 optimization recommendation",
            "Gate7B structured profile patch proposal",
        ],
        "scenario_count": len(results),
        "scenario_pass_count": sum(1 for x in results if x["passed"]),
        "scenario_results": results,
        "office_stage_policy": {
            "printer_connection_attempted": False,
            "camera_connection_attempted": False,
            "mqtt_publish_count": 0,
            "ftps_connection_attempted": False,
            "gcode_file_modified": False,
            "bambu_profile_modified": False,
            "automatic_reslice_started": False,
            "automatic_reprint_started": False,
            "pause_command_count": 0,
            "stop_command_count": 0,
            "all_patch_items_remain_proposed_only": True,
        },
        "office_stage_completion": {
            "software_logic_complete": all_passed,
            "real_hardware_validation_complete": False,
            "final_lab_visit_required": True,
        },
        "deferred_to_final_lab": {
            "gate5b_live_mqtt_monitor": True,
            "real_x1c_camera_stream": True,
            "real_ai_model_live_inference": True,
            "live_fusion_decision_on_real_print": True,
            "live_pause_stop_validation": True,
        },
        "next_phase": "m4_gate9_final_lab_validation_package",
    }

    output = task_dir / OUTPUT_REPORT
    if output.exists():
        raise Gate8V800Error(f"Refusing to overwrite existing report: {output}")

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report | {"report_file": str(output)}
