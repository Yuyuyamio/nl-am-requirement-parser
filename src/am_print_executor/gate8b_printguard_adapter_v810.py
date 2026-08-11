from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"

GATE8_REPORT = "m4_gate8_offline_end_to_end_validation_v800.json"
OUTPUT_REPORT = "m4_gate8b_real_ai_inference_v810.json"

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class Gate8BV810Error(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate8BV810Error(f"Required report missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate8BV810Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise Gate8BV810Error(f"Expected JSON object: {path}")
    return obj


def validate_printguard_response(obj: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise Gate8BV810Error("PrintGuard response must be a JSON object.")

    prediction = str(obj.get("prediction") or "").strip().lower()
    if prediction not in {"success", "failure", "unknown"}:
        raise Gate8BV810Error(
            f"Unsupported PrintGuard prediction: {prediction!r}"
        )

    distances = obj.get("distances")
    if not isinstance(distances, dict):
        raise Gate8BV810Error("PrintGuard distances object is missing.")

    for key in ("success", "failure"):
        value = distances.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise Gate8BV810Error(
                f"PrintGuard distances.{key} must be numeric."
            )

    margin = obj.get("margin")
    if not isinstance(margin, (int, float)) or isinstance(margin, bool):
        raise Gate8BV810Error("PrintGuard margin must be numeric.")

    defect_score = obj.get("defect_score")
    if defect_score is not None:
        if not isinstance(defect_score, (int, float)) or isinstance(defect_score, bool):
            raise Gate8BV810Error("PrintGuard defect_score must be numeric when present.")
        if not 0.0 <= float(defect_score) <= 1.0:
            raise Gate8BV810Error("PrintGuard defect_score must be within [0,1].")

    # PrintGuard is a generic success/failure model here, not a multi-class
    # defect taxonomy. Do not invent a spaghetti/warping/etc. label.
    if prediction == "success":
        vision_risk = "normal"
        gate6b_compatible_decision = "continue_monitoring"
    elif prediction == "failure":
        vision_risk = "attention"
        gate6b_compatible_decision = "continue_monitoring_and_review"
    else:
        vision_risk = "unknown"
        gate6b_compatible_decision = "hold_for_review"

    return {
        "prediction": prediction,
        "distances": {
            "success": float(distances["success"]),
            "failure": float(distances["failure"]),
        },
        "margin": float(margin),
        "defect_score": (
            float(defect_score) if defect_score is not None else None
        ),
        "vision_risk": vision_risk,
        "gate6b_compatible_decision": gate6b_compatible_decision,
        "defect_type": None,
        "model_capability": "generic_fdm_failure_binary",
    }


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def check_health(base_url: str, token: str | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/health"
    req = urllib.request.Request(url, headers=_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise Gate8BV810Error(
            f"PrintGuard health HTTP {exc.code}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise Gate8BV810Error(
            "PrintGuard Hub is not reachable at "
            f"{base_url}. Start the local Windows Hub first. Detail: {exc}"
        ) from exc

    if status != 200:
        raise Gate8BV810Error(f"PrintGuard health returned HTTP {status}.")

    try:
        obj = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise Gate8BV810Error("PrintGuard health did not return valid JSON.") from exc

    if not isinstance(obj, dict) or obj.get("ok") is not True:
        raise Gate8BV810Error(f"PrintGuard health is not ready: {obj!r}")

    return obj


def classify_jpeg(
    base_url: str,
    image_path: Path,
    token: str | None = None,
) -> dict[str, Any]:
    image_path = image_path.resolve()
    if not image_path.is_file():
        raise Gate8BV810Error(f"JPEG file does not exist: {image_path}")
    if image_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise Gate8BV810Error(
            f"PrintGuard office validation requires JPEG input: {image_path}"
        )

    data = image_path.read_bytes()
    if len(data) < 100:
        raise Gate8BV810Error(f"JPEG file is unexpectedly small: {image_path}")

    headers = _headers(token)
    headers["Content-Type"] = "image/jpeg"

    url = base_url.rstrip("/") + "/api/v1/classify"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise Gate8BV810Error(
            f"PrintGuard classify HTTP {exc.code}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise Gate8BV810Error(
            f"PrintGuard classify request failed: {exc}"
        ) from exc

    if status != 200:
        raise Gate8BV810Error(f"PrintGuard classify returned HTTP {status}.")

    try:
        obj = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise Gate8BV810Error(
            "PrintGuard classify did not return valid JSON."
        ) from exc

    validated = validate_printguard_response(obj)
    return {
        "image_file": str(image_path),
        "image_size_bytes": len(data),
        "raw_response": obj,
        "validated_signal": validated,
    }


def run(
    project_root: Path,
    image_paths: list[Path],
    *,
    base_url: str = DEFAULT_BASE_URL,
    token: str | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    task_dir = project_root / "outputs" / "m4" / REQUEST_ID

    gate8_path = task_dir / GATE8_REPORT
    gate8 = _load_json(gate8_path)

    if gate8.get("version") != "8.0.0":
        raise Gate8BV810Error("Gate8 version lock is not 8.0.0.")
    if gate8.get("status") != "office_offline_end_to_end_validated":
        raise Gate8BV810Error("Gate8 did not pass.")
    if gate8.get("request_id") != REQUEST_ID:
        raise Gate8BV810Error("Gate8 request_id mismatch.")

    if not image_paths:
        raise Gate8BV810Error(
            "At least one real JPEG 3D-print image is required."
        )

    health = check_health(base_url, token)

    inference_results = [
        classify_jpeg(base_url, path, token)
        for path in image_paths
    ]

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "8B",
        "version": "8.1.0",
        "stage": "real_local_ai_model_inference_validation",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": "real_local_ai_inference_validated",
        "created_unix": time.time(),
        "source_gate8": {
            "report_file": str(gate8_path),
            "version": gate8.get("version"),
            "status": gate8.get("status"),
        },
        "ai_backend": {
            "name": "PrintGuard",
            "base_url": base_url,
            "health": health,
            "execution": "local_hub",
            "cloud_required": False,
            "camera_required_for_this_gate": False,
            "printer_required_for_this_gate": False,
            "input_endpoint": "POST /api/v1/classify",
            "input_media_type": "image/jpeg",
            "capability": "generic_fdm_failure_binary",
            "does_not_claim_multiclass_defect_type": True,
        },
        "image_count": len(inference_results),
        "inference_results": inference_results,
        "contract_bridge": {
            "PrintGuard success": "Gate6B vision_risk=normal",
            "PrintGuard failure": "Gate6B vision_risk=attention for a single frame",
            "PrintGuard unknown": "Gate6B vision_risk=unknown",
            "multiframe_escalation": (
                "Final live integration must aggregate repeated failure frames "
                "before escalating to critical."
            ),
            "invented_defect_labels": False,
        },
        "policy": {
            "office_only": True,
            "printer_connection_attempted": False,
            "x1c_camera_connection_attempted": False,
            "mqtt_publish_count": 0,
            "ftps_connection_attempted": False,
            "printer_control_count": 0,
            "gcode_file_modified": False,
            "bambu_profile_modified": False,
            "model_accuracy_claimed": False,
            "inference_api_functionality_validated": True,
        },
        "deferred_to_final_lab": {
            "gate5b_live_mqtt_monitor": True,
            "x1c_camera_to_printguard_live_frames": True,
            "multiframe_live_ai_detection": True,
            "gate6c_live_fusion": True,
            "pause_stop_command_validation": True,
        },
        "next_phase": "m4_gate8c_real_ai_multiframe_replay_or_gate9_lab_package",
    }

    output = task_dir / OUTPUT_REPORT
    if output.exists():
        raise Gate8BV810Error(f"Refusing to overwrite existing report: {output}")

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report | {"report_file": str(output)}
