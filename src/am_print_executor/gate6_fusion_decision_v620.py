from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"

GATE6A_REPORT = "m4_gate6a_offline_risk_validation_v600.json"
GATE6B_REPORT = "m4_gate6b_offline_vision_contract_v610.json"
OUTPUT_REPORT = "m4_gate6c_offline_fusion_decision_v620.json"


class Gate6CV620Error(RuntimeError):
    pass


DEVICE_RISKS = {"normal", "attention", "critical", "unknown"}
VISION_RISKS = {"normal", "attention", "critical", "unknown"}

# Office-stage policy only. No live printer command is emitted.
DECISION_RANK = {
    "CONTINUE": 0,
    "REVIEW": 1,
    "WOULD_PAUSE": 2,
    "WOULD_STOP": 3,
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate6CV620Error(f"Required report missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate6CV620Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise Gate6CV620Error(f"Expected JSON object: {path}")
    return obj


def _normalize_risk(name: str, value: Any, allowed: set[str]) -> str:
    risk = str(value or "").strip().lower()
    if risk not in allowed:
        raise Gate6CV620Error(f"Unsupported {name} risk: {risk!r}")
    return risk


def fuse_risks(
    device_risk: str,
    vision_risk: str,
    *,
    device_reasons: list[str] | None = None,
    vision_reasons: list[str] | None = None,
) -> dict[str, Any]:
    d = _normalize_risk("device", device_risk, DEVICE_RISKS)
    v = _normalize_risk("vision", vision_risk, VISION_RISKS)

    device_reasons = list(device_reasons or [])
    vision_reasons = list(vision_reasons or [])

    # Highest priority: explicit device critical state.
    # Examples: FAILED, non-zero print_error, HMS present.
    if d == "critical":
        decision = "WOULD_STOP"
        fused_risk = "critical"
        rationale = ["device_critical_has_priority"]
    elif d == "unknown":
        decision = "REVIEW"
        fused_risk = "unknown"
        rationale = ["device_state_unknown"]
    elif v == "unknown":
        decision = "REVIEW"
        fused_risk = "unknown"
        rationale = ["vision_state_unknown"]
    elif d == "attention" and v == "critical":
        decision = "WOULD_STOP"
        fused_risk = "critical"
        rationale = ["device_attention_plus_vision_critical"]
    elif d == "normal" and v == "critical":
        decision = "WOULD_PAUSE"
        fused_risk = "critical"
        rationale = ["vision_critical_requires_confirmation_before_stop"]
    elif d == "attention":
        decision = "REVIEW"
        fused_risk = "attention"
        rationale = ["device_attention_requires_review"]
    elif v == "attention":
        decision = "REVIEW"
        fused_risk = "attention"
        rationale = ["vision_attention_requires_review"]
    else:
        decision = "CONTINUE"
        fused_risk = "normal"
        rationale = ["device_and_vision_normal"]

    return {
        "device_risk": d,
        "vision_risk": v,
        "fused_risk": fused_risk,
        "decision": decision,
        "decision_rank": DECISION_RANK[decision],
        "device_reasons": device_reasons,
        "vision_reasons": vision_reasons,
        "rationale": rationale,
        "live_command_emitted": False,
    }


def _scenarios() -> list[dict[str, Any]]:
    return [
        {
            "name": "all_normal",
            "device_risk": "normal",
            "vision_risk": "normal",
            "expected_decision": "CONTINUE",
        },
        {
            "name": "device_attention_only",
            "device_risk": "attention",
            "vision_risk": "normal",
            "expected_decision": "REVIEW",
        },
        {
            "name": "vision_attention_only",
            "device_risk": "normal",
            "vision_risk": "attention",
            "expected_decision": "REVIEW",
        },
        {
            "name": "vision_critical_device_normal",
            "device_risk": "normal",
            "vision_risk": "critical",
            "expected_decision": "WOULD_PAUSE",
        },
        {
            "name": "device_attention_vision_critical",
            "device_risk": "attention",
            "vision_risk": "critical",
            "expected_decision": "WOULD_STOP",
        },
        {
            "name": "device_critical_vision_normal",
            "device_risk": "critical",
            "vision_risk": "normal",
            "expected_decision": "WOULD_STOP",
        },
        {
            "name": "device_critical_vision_critical",
            "device_risk": "critical",
            "vision_risk": "critical",
            "expected_decision": "WOULD_STOP",
        },
        {
            "name": "unknown_device",
            "device_risk": "unknown",
            "vision_risk": "normal",
            "expected_decision": "REVIEW",
        },
        {
            "name": "unknown_vision",
            "device_risk": "normal",
            "vision_risk": "unknown",
            "expected_decision": "REVIEW",
        },
    ]


def run(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    task_dir = project_root / "outputs" / "m4" / REQUEST_ID

    gate6a_path = task_dir / GATE6A_REPORT
    gate6b_path = task_dir / GATE6B_REPORT

    gate6a = _load_json(gate6a_path)
    gate6b = _load_json(gate6b_path)

    if gate6a.get("version") != "6.0.0":
        raise Gate6CV620Error("Gate6A version lock is not 6.0.0.")
    if gate6a.get("status") != "offline_risk_engine_validated":
        raise Gate6CV620Error("Gate6A did not pass.")
    if gate6a.get("request_id") != REQUEST_ID:
        raise Gate6CV620Error("Gate6A request_id mismatch.")

    if gate6b.get("version") != "6.1.0":
        raise Gate6CV620Error("Gate6B version lock is not 6.1.0.")
    if gate6b.get("status") != "offline_vision_contract_validated":
        raise Gate6CV620Error("Gate6B did not pass.")
    if gate6b.get("request_id") != REQUEST_ID:
        raise Gate6CV620Error("Gate6B request_id mismatch.")

    results: list[dict[str, Any]] = []
    all_passed = True

    for case in _scenarios():
        actual = fuse_risks(
            case["device_risk"],
            case["vision_risk"],
        )
        passed = actual["decision"] == case["expected_decision"]
        all_passed = all_passed and passed
        results.append({
            "name": case["name"],
            "device_risk": case["device_risk"],
            "vision_risk": case["vision_risk"],
            "expected_decision": case["expected_decision"],
            "actual": actual,
            "passed": passed,
        })

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "6C",
        "version": "6.2.0",
        "stage": "offline_multimodal_fusion_decision_validation",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": (
            "offline_fusion_decision_validated"
            if all_passed
            else "offline_fusion_decision_failed"
        ),
        "created_unix": time.time(),
        "source_gate6a": {
            "report_file": str(gate6a_path),
            "status": gate6a.get("status"),
            "version": gate6a.get("version"),
        },
        "source_gate6b": {
            "report_file": str(gate6b_path),
            "status": gate6b.get("status"),
            "version": gate6b.get("version"),
        },
        "decision_contract": {
            "outputs": [
                "CONTINUE",
                "REVIEW",
                "WOULD_PAUSE",
                "WOULD_STOP",
            ],
            "live_command_semantics": {
                "CONTINUE": "No intervention recommended.",
                "REVIEW": "Human review recommended; no automatic printer command.",
                "WOULD_PAUSE": "Offline policy says a live controller could request pause after final lab validation.",
                "WOULD_STOP": "Offline policy says a live controller could request stop after final lab validation.",
            },
            "priority_rules": [
                "device critical -> WOULD_STOP",
                "device unknown or vision unknown -> REVIEW",
                "device attention + vision critical -> WOULD_STOP",
                "device normal + vision critical -> WOULD_PAUSE",
                "any attention without critical -> REVIEW",
                "both normal -> CONTINUE",
            ],
        },
        "scenario_count": len(results),
        "scenario_pass_count": sum(1 for x in results if x["passed"]),
        "scenario_results": results,
        "policy": {
            "office_only": True,
            "printer_connection_attempted": False,
            "camera_connection_attempted": False,
            "mqtt_publish_count": 0,
            "ftps_connection_attempted": False,
            "pause_command_count": 0,
            "stop_command_count": 0,
            "print_start_command_count": 0,
            "parameter_adjustment_count": 0,
            "automatic_control_enabled": False,
            "would_pause_stop_are_recommendations_only": True,
        },
        "deferred_to_final_lab": {
            "gate5b_live_mqtt_monitor": True,
            "real_x1c_camera_stream": True,
            "real_ai_model_live_inference": True,
            "live_pause_command_validation": True,
            "live_stop_command_validation": True,
        },
        "next_phase": "m4_gate7_offline_optimization_recommendation",
    }

    output = task_dir / OUTPUT_REPORT
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise Gate6CV620Error(f"Refusing to overwrite existing report: {output}")

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report | {"report_file": str(output)}
