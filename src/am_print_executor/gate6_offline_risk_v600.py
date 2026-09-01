from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
GATE5A_REPORT = "m4_gate5_post_print_acceptance_v500.json"
OUTPUT_REPORT = "m4_gate6a_offline_risk_validation_v600.json"


class Gate6V600Error(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate6V600Error(f"Required report missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate6V600Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise Gate6V600Error(f"Expected JSON object: {path}")
    return obj


def classify_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    state = str(snapshot.get("gcode_state") or "").strip().upper()
    print_error = snapshot.get("print_error")
    hms = snapshot.get("hms")

    reasons: list[str] = []

    if not state:
        return {
            "risk_level": "unknown",
            "decision": "hold_for_review",
            "reasons": ["missing_gcode_state"],
        }

    if print_error not in (0, "0", None):
        reasons.append("nonzero_print_error")

    if isinstance(hms, list) and len(hms) > 0:
        reasons.append("hms_present")
    elif hms is not None and not isinstance(hms, list):
        reasons.append("invalid_hms_shape")

    if state == "FAILED":
        reasons.append("printer_state_failed")

    if reasons:
        return {
            "risk_level": "critical",
            "decision": "would_require_human_intervention",
            "reasons": reasons,
        }

    if state in {"PAUSE", "PAUSED"}:
        return {
            "risk_level": "attention",
            "decision": "human_review",
            "reasons": ["printer_paused"],
        }

    if state in {"PREPARE", "RUNNING", "SLICING"}:
        return {
            "risk_level": "normal",
            "decision": "continue_monitoring",
            "reasons": [],
        }

    if state == "FINISH":
        percent = snapshot.get("mc_percent")
        remaining = snapshot.get("mc_remaining_time")
        if isinstance(percent, (int, float)) and float(percent) >= 100 and remaining in (0, 0.0, "0"):
            return {
                "risk_level": "normal",
                "decision": "accept_completed_terminal_state",
                "reasons": [],
            }
        return {
            "risk_level": "attention",
            "decision": "human_review",
            "reasons": ["finish_state_not_confirmed_complete"],
        }

    if state == "IDLE":
        return {
            "risk_level": "normal",
            "decision": "idle_ready",
            "reasons": [],
        }

    return {
        "risk_level": "unknown",
        "decision": "hold_for_review",
        "reasons": [f"unrecognized_state:{state}"],
    }


def _gate5a_terminal_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    terminal = report.get("terminal_state")
    if not isinstance(terminal, dict):
        raise Gate6V600Error("Gate5A terminal_state is missing.")
    observed = terminal.get("observed")
    if not isinstance(observed, dict):
        raise Gate6V600Error("Gate5A terminal_state.observed is missing.")
    return observed


def _scenarios() -> list[dict[str, Any]]:
    return [
        {
            "name": "normal_running",
            "snapshot": {
                "gcode_state": "RUNNING",
                "print_error": 0,
                "hms": [],
                "mc_percent": 42,
                "mc_remaining_time": 18,
            },
            "expected_risk": "normal",
            "expected_decision": "continue_monitoring",
        },
        {
            "name": "paused_for_review",
            "snapshot": {
                "gcode_state": "PAUSE",
                "print_error": 0,
                "hms": [],
            },
            "expected_risk": "attention",
            "expected_decision": "human_review",
        },
        {
            "name": "explicit_failed_state",
            "snapshot": {
                "gcode_state": "FAILED",
                "print_error": 0,
                "hms": [],
            },
            "expected_risk": "critical",
            "expected_decision": "would_require_human_intervention",
        },
        {
            "name": "nonzero_print_error",
            "snapshot": {
                "gcode_state": "RUNNING",
                "print_error": 12345,
                "hms": [],
            },
            "expected_risk": "critical",
            "expected_decision": "would_require_human_intervention",
        },
        {
            "name": "hms_present",
            "snapshot": {
                "gcode_state": "RUNNING",
                "print_error": 0,
                "hms": [{"attr": 1, "code": 2}],
            },
            "expected_risk": "critical",
            "expected_decision": "would_require_human_intervention",
        },
        {
            "name": "clean_finish",
            "snapshot": {
                "gcode_state": "FINISH",
                "print_error": 0,
                "hms": [],
                "mc_percent": 100,
                "mc_remaining_time": 0,
            },
            "expected_risk": "normal",
            "expected_decision": "accept_completed_terminal_state",
        },
        {
            "name": "unknown_state",
            "snapshot": {
                "gcode_state": "SOMETHING_NEW",
                "print_error": 0,
                "hms": [],
            },
            "expected_risk": "unknown",
            "expected_decision": "hold_for_review",
        },
    ]


def run(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    gate5a_path = project_root / "outputs" / "m4" / REQUEST_ID / GATE5A_REPORT
    gate5a = _load_json(gate5a_path)

    if gate5a.get("version") != "5.0.0":
        raise Gate6V600Error("Gate5A version lock is not 5.0.0.")
    if gate5a.get("status") != "first_real_print_accepted":
        raise Gate6V600Error("Gate5A did not record first_real_print_accepted.")
    if gate5a.get("request_id") != REQUEST_ID:
        raise Gate6V600Error("Gate5A request_id mismatch.")
    if gate5a.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate6V600Error("Gate5A device_id mismatch.")

    terminal_snapshot = _gate5a_terminal_snapshot(gate5a)
    terminal_classification = classify_snapshot(terminal_snapshot)
    if terminal_classification["risk_level"] != "normal":
        raise Gate6V600Error(
            f"Accepted Gate5A terminal state did not classify normal: {terminal_classification}"
        )

    results = []
    all_passed = True
    for item in _scenarios():
        got = classify_snapshot(item["snapshot"])
        passed = (
            got["risk_level"] == item["expected_risk"]
            and got["decision"] == item["expected_decision"]
        )
        all_passed = all_passed and passed
        results.append({
            "name": item["name"],
            "expected_risk": item["expected_risk"],
            "expected_decision": item["expected_decision"],
            "actual": got,
            "passed": passed,
        })

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "6A",
        "version": "6.0.0",
        "stage": "offline_telemetry_risk_engine_validation",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": "offline_risk_engine_validated" if all_passed else "offline_risk_engine_failed",
        "created_unix": time.time(),
        "source_gate5a": {
            "report_file": str(gate5a_path),
            "status": gate5a.get("status"),
            "terminal_classification": terminal_classification,
        },
        "scenario_results": results,
        "scenario_count": len(results),
        "scenario_pass_count": sum(1 for r in results if r["passed"]),
        "policy": {
            "offline_only": True,
            "printer_connection_attempted": False,
            "mqtt_publish_count": 0,
            "print_start_command_count": 0,
            "pause_command_count": 0,
            "stop_command_count": 0,
            "parameter_adjustment_count": 0,
            "device_actions_are_recommendations_only": True,
            "temperature_thresholds_not_invented": True,
        },
        "deferred_real_hardware_gate": {
            "gate5b_live_mqtt_monitor": "DEFERRED_TO_FINAL_LAB",
            "live_camera_validation": "DEFERRED_TO_FINAL_LAB",
        },
        "next_phase": "m4_gate6b_offline_vision_interface",
    }

    output = project_root / "outputs" / "m4" / REQUEST_ID / OUTPUT_REPORT
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise Gate6V600Error(f"Refusing to overwrite existing report: {output}")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report | {"report_file": str(output)}
