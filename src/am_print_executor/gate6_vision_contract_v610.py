from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
GATE6A_REPORT = "m4_gate6a_offline_risk_validation_v600.json"
OUTPUT_REPORT = "m4_gate6b_offline_vision_contract_v610.json"

ALLOWED_DEFECTS = {
    "spaghetti",
    "warping",
    "bed_detachment",
    "layer_shift",
    "stringing",
    "under_extrusion",
    "over_extrusion",
    "blob",
    "foreign_object",
}

# Interface-validation defaults only. These are NOT production safety thresholds.
ATTENTION_CONFIDENCE = 0.55
CRITICAL_CONFIDENCE = 0.80
CRITICAL_DEFECTS = {"spaghetti", "bed_detachment", "layer_shift", "foreign_object"}


class Gate6BV610Error(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate6BV610Error(f"Required report missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate6BV610Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise Gate6BV610Error(f"Expected JSON object: {path}")
    return obj


def validate_detection(det: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(det, dict):
        raise Gate6BV610Error("Detection must be a JSON object.")

    defect = str(det.get("defect_type") or "").strip()
    if defect not in ALLOWED_DEFECTS:
        raise Gate6BV610Error(f"Unsupported defect_type: {defect!r}")

    confidence = det.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise Gate6BV610Error("confidence must be numeric.")
    confidence = float(confidence)
    if not (0.0 <= confidence <= 1.0):
        raise Gate6BV610Error("confidence must be within [0, 1].")

    bbox = det.get("bbox_norm")
    if bbox is not None:
        if not (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in bbox)
        ):
            raise Gate6BV610Error("bbox_norm must be [x1,y1,x2,y2] or null.")
        x1, y1, x2, y2 = map(float, bbox)
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise Gate6BV610Error("bbox_norm coordinates must be normalized and ordered.")
        bbox = [x1, y1, x2, y2]

    return {
        "defect_type": defect,
        "confidence": confidence,
        "bbox_norm": bbox,
        "source": str(det.get("source") or "offline_replay"),
    }


def classify_frame(frame: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(frame, dict):
        raise Gate6BV610Error("Frame record must be a JSON object.")

    frame_id = str(frame.get("frame_id") or "").strip()
    if not frame_id:
        raise Gate6BV610Error("frame_id is required.")

    detections_raw = frame.get("detections", [])
    if not isinstance(detections_raw, list):
        raise Gate6BV610Error("detections must be a list.")

    detections = [validate_detection(x) for x in detections_raw]

    critical = [
        d for d in detections
        if d["defect_type"] in CRITICAL_DEFECTS and d["confidence"] >= CRITICAL_CONFIDENCE
    ]
    attention = [
        d for d in detections
        if d["confidence"] >= ATTENTION_CONFIDENCE
    ]

    if critical:
        risk = "critical"
        decision = "would_require_human_intervention"
    elif attention:
        risk = "attention"
        decision = "continue_monitoring_and_review"
    else:
        risk = "normal"
        decision = "continue_monitoring"

    return {
        "frame_id": frame_id,
        "timestamp_ms": frame.get("timestamp_ms"),
        "risk_level": risk,
        "decision": decision,
        "detections": detections,
        "critical_detection_count": len(critical),
        "attention_detection_count": len(attention),
    }


def aggregate_frames(frames: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(frames, list) or not frames:
        raise Gate6BV610Error("At least one frame is required.")

    classified = [classify_frame(f) for f in frames]

    critical_frames = [f for f in classified if f["risk_level"] == "critical"]
    attention_frames = [f for f in classified if f["risk_level"] == "attention"]

    # Conservative temporal rule for office replay validation:
    # critical persists across >=2 frames => critical sequence.
    # one isolated critical frame => attention/review.
    if len(critical_frames) >= 2:
        sequence_risk = "critical"
        sequence_decision = "would_require_human_intervention"
    elif len(critical_frames) == 1 or len(attention_frames) >= 2:
        sequence_risk = "attention"
        sequence_decision = "human_review"
    else:
        sequence_risk = "normal"
        sequence_decision = "continue_monitoring"

    return {
        "sequence_risk": sequence_risk,
        "sequence_decision": sequence_decision,
        "frame_count": len(classified),
        "critical_frame_count": len(critical_frames),
        "attention_frame_count": len(attention_frames),
        "frames": classified,
    }


def _demo_sequences() -> list[dict[str, Any]]:
    return [
        {
            "name": "clean_print",
            "frames": [
                {"frame_id": "clean_001", "timestamp_ms": 0, "detections": []},
                {"frame_id": "clean_002", "timestamp_ms": 1000, "detections": []},
                {"frame_id": "clean_003", "timestamp_ms": 2000, "detections": []},
            ],
            "expected_risk": "normal",
            "expected_decision": "continue_monitoring",
        },
        {
            "name": "isolated_low_confidence_stringing",
            "frames": [
                {"frame_id": "s_001", "timestamp_ms": 0, "detections": []},
                {"frame_id": "s_002", "timestamp_ms": 1000, "detections": [
                    {
                        "defect_type": "stringing",
                        "confidence": 0.42,
                        "bbox_norm": [0.2, 0.2, 0.4, 0.5],
                    }
                ]},
            ],
            "expected_risk": "normal",
            "expected_decision": "continue_monitoring",
        },
        {
            "name": "persistent_attention_stringing",
            "frames": [
                {"frame_id": "a_001", "timestamp_ms": 0, "detections": [
                    {
                        "defect_type": "stringing",
                        "confidence": 0.68,
                        "bbox_norm": [0.2, 0.2, 0.5, 0.6],
                    }
                ]},
                {"frame_id": "a_002", "timestamp_ms": 1000, "detections": [
                    {
                        "defect_type": "stringing",
                        "confidence": 0.71,
                        "bbox_norm": [0.22, 0.2, 0.52, 0.61],
                    }
                ]},
            ],
            "expected_risk": "attention",
            "expected_decision": "human_review",
        },
        {
            "name": "persistent_spaghetti",
            "frames": [
                {"frame_id": "c_001", "timestamp_ms": 0, "detections": [
                    {
                        "defect_type": "spaghetti",
                        "confidence": 0.91,
                        "bbox_norm": [0.1, 0.1, 0.8, 0.8],
                    }
                ]},
                {"frame_id": "c_002", "timestamp_ms": 1000, "detections": [
                    {
                        "defect_type": "spaghetti",
                        "confidence": 0.94,
                        "bbox_norm": [0.08, 0.09, 0.82, 0.81],
                    }
                ]},
            ],
            "expected_risk": "critical",
            "expected_decision": "would_require_human_intervention",
        },
        {
            "name": "single_critical_frame_is_review_only",
            "frames": [
                {"frame_id": "i_001", "timestamp_ms": 0, "detections": []},
                {"frame_id": "i_002", "timestamp_ms": 1000, "detections": [
                    {
                        "defect_type": "layer_shift",
                        "confidence": 0.88,
                        "bbox_norm": [0.1, 0.2, 0.7, 0.8],
                    }
                ]},
                {"frame_id": "i_003", "timestamp_ms": 2000, "detections": []},
            ],
            "expected_risk": "attention",
            "expected_decision": "human_review",
        },
    ]


def run(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    gate6a_path = project_root / "outputs" / "m4" / REQUEST_ID / GATE6A_REPORT
    gate6a = _load_json(gate6a_path)

    if gate6a.get("version") != "6.0.0":
        raise Gate6BV610Error("Gate6A version lock is not 6.0.0.")
    if gate6a.get("status") != "offline_risk_engine_validated":
        raise Gate6BV610Error("Gate6A did not pass.")
    if gate6a.get("request_id") != REQUEST_ID:
        raise Gate6BV610Error("Gate6A request_id mismatch.")

    results = []
    all_passed = True
    for case in _demo_sequences():
        got = aggregate_frames(case["frames"])
        passed = (
            got["sequence_risk"] == case["expected_risk"]
            and got["sequence_decision"] == case["expected_decision"]
        )
        all_passed = all_passed and passed
        results.append({
            "name": case["name"],
            "expected_risk": case["expected_risk"],
            "expected_decision": case["expected_decision"],
            "actual": got,
            "passed": passed,
        })

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "6B",
        "version": "6.1.0",
        "stage": "offline_vision_contract_and_replay_validation",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": "offline_vision_contract_validated" if all_passed else "offline_vision_contract_failed",
        "created_unix": time.time(),
        "source_gate6a": {
            "report_file": str(gate6a_path),
            "status": gate6a.get("status"),
        },
        "vision_contract": {
            "allowed_defects": sorted(ALLOWED_DEFECTS),
            "detection_fields": [
                "defect_type",
                "confidence",
                "bbox_norm",
                "source",
            ],
            "attention_confidence_demo_default": ATTENTION_CONFIDENCE,
            "critical_confidence_demo_default": CRITICAL_CONFIDENCE,
            "critical_defects_demo_default": sorted(CRITICAL_DEFECTS),
            "note": "These confidence thresholds are interface-validation defaults, not production safety thresholds.",
        },
        "replay_case_count": len(results),
        "replay_pass_count": sum(1 for r in results if r["passed"]),
        "replay_results": results,
        "policy": {
            "office_only": True,
            "camera_connection_attempted": False,
            "printer_connection_attempted": False,
            "ai_model_loaded": False,
            "model_accuracy_claimed": False,
            "mqtt_publish_count": 0,
            "device_control_count": 0,
            "automatic_pause_stop_enabled": False,
        },
        "deferred_to_final_lab": {
            "real_x1c_camera_stream": True,
            "real_ai_model_inference_on_live_frames": True,
            "gate5b_live_mqtt_monitor": True,
        },
        "next_phase": "m4_gate6c_offline_fusion_decision",
    }

    output = project_root / "outputs" / "m4" / REQUEST_ID / OUTPUT_REPORT
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise Gate6BV610Error(f"Refusing to overwrite existing report: {output}")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report | {"report_file": str(output)}
