from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
GATE8B_REPORT = "m4_gate8b_real_ai_inference_v810.json"
OUTPUT_REPORT = "m4_gate8c_model_screening_multiframe_v820.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
PREDICTIONS = {"success", "failure", "unknown"}


class Gate8CV820Error(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate8CV820Error(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate8CV820Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise Gate8CV820Error(f"Expected JSON object: {path}")
    return obj


def _jpeg_files(directory: Path) -> list[Path]:
    directory = directory.resolve()
    if not directory.is_dir():
        raise Gate8CV820Error(f"Directory does not exist: {directory}")
    files = sorted(
        [
            p for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"}
        ],
        key=lambda p: p.name.lower(),
    )
    if not files:
        raise Gate8CV820Error(f"No JPEG files found in: {directory}")
    return files


def _headers(token: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def check_health(base_url: str, token: str | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/health",
        headers=_headers(token),
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise Gate8CV820Error(
            f"PrintGuard health HTTP {exc.code}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise Gate8CV820Error(
            f"PrintGuard Hub unreachable at {base_url}: {exc}"
        ) from exc

    if status != 200:
        raise Gate8CV820Error(f"PrintGuard health returned HTTP {status}.")

    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise Gate8CV820Error("PrintGuard health response was not valid JSON.") from exc

    if not isinstance(obj, dict) or obj.get("ok") is not True:
        raise Gate8CV820Error(f"PrintGuard health is not ready: {obj!r}")
    return obj


def classify_jpeg(
    base_url: str,
    image_path: Path,
    token: str | None = None,
) -> dict[str, Any]:
    image_path = image_path.resolve()
    if not image_path.is_file():
        raise Gate8CV820Error(f"Image missing: {image_path}")
    if image_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise Gate8CV820Error(f"Expected JPEG image: {image_path}")

    data = image_path.read_bytes()
    if len(data) < 100:
        raise Gate8CV820Error(f"JPEG unexpectedly small: {image_path}")

    headers = _headers(token)
    headers["Content-Type"] = "image/jpeg"

    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/v1/classify",
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise Gate8CV820Error(
            f"PrintGuard classify HTTP {exc.code}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise Gate8CV820Error(
            f"PrintGuard classify request failed for {image_path.name}: {exc}"
        ) from exc

    if status != 200:
        raise Gate8CV820Error(
            f"PrintGuard classify returned HTTP {status} for {image_path.name}."
        )

    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise Gate8CV820Error(
            f"PrintGuard classify returned invalid JSON for {image_path.name}."
        ) from exc

    if not isinstance(obj, dict):
        raise Gate8CV820Error(
            f"PrintGuard classify returned non-object JSON for {image_path.name}."
        )

    prediction = str(obj.get("prediction") or "").strip().lower()
    if prediction not in PREDICTIONS:
        raise Gate8CV820Error(
            f"Unsupported prediction {prediction!r} for {image_path.name}."
        )

    distances = obj.get("distances")
    if not isinstance(distances, dict):
        raise Gate8CV820Error(f"Missing distances object for {image_path.name}.")

    return {
        "image_file": str(image_path),
        "prediction": prediction,
        "distances": distances,
        "margin": obj.get("margin"),
        "defect_score": obj.get("defect_score"),
    }


def evaluate_labeled_results(
    success_results: list[dict[str, Any]],
    failure_results: list[dict[str, Any]],
) -> dict[str, Any]:
    if not success_results or not failure_results:
        raise Gate8CV820Error(
            "Both success and failure labeled result sets are required."
        )

    counts = {
        "true_success_pred_success": 0,
        "true_success_pred_failure": 0,
        "true_success_pred_unknown": 0,
        "true_failure_pred_success": 0,
        "true_failure_pred_failure": 0,
        "true_failure_pred_unknown": 0,
    }

    for item in success_results:
        counts[f"true_success_pred_{item['prediction']}"] += 1
    for item in failure_results:
        counts[f"true_failure_pred_{item['prediction']}"] += 1

    success_total = len(success_results)
    failure_total = len(failure_results)
    total = success_total + failure_total

    success_recall = counts["true_success_pred_success"] / success_total
    failure_recall = counts["true_failure_pred_failure"] / failure_total
    unknown_rate = (
        counts["true_success_pred_unknown"]
        + counts["true_failure_pred_unknown"]
    ) / total
    overall_accuracy = (
        counts["true_success_pred_success"]
        + counts["true_failure_pred_failure"]
    ) / total
    balanced_accuracy = (success_recall + failure_recall) / 2.0

    return {
        "counts": counts,
        "success_sample_count": success_total,
        "failure_sample_count": failure_total,
        "total_sample_count": total,
        "success_recall": success_recall,
        "failure_recall": failure_recall,
        "overall_accuracy_unknown_counted_wrong": overall_accuracy,
        "balanced_accuracy_unknown_counted_wrong": balanced_accuracy,
        "unknown_rate": unknown_rate,
        "quality_interpretation": "METRICS_ONLY_NO_PRODUCTION_ACCEPTANCE_CLAIM",
    }


def aggregate_predictions(predictions: list[str]) -> dict[str, Any]:
    normalized = [str(x).strip().lower() for x in predictions]
    if not normalized:
        raise Gate8CV820Error("Multiframe sequence cannot be empty.")
    if any(x not in PREDICTIONS for x in normalized):
        raise Gate8CV820Error(
            f"Multiframe sequence contains unsupported predictions: {normalized}"
        )

    failure_count = sum(x == "failure" for x in normalized)
    success_count = sum(x == "success" for x in normalized)
    unknown_count = sum(x == "unknown" for x in normalized)

    if failure_count >= 2:
        risk = "critical"
        decision = "WOULD_PAUSE"
    elif failure_count == 1 or unknown_count > 0:
        risk = "attention"
        decision = "REVIEW"
    else:
        risk = "normal"
        decision = "CONTINUE"

    return {
        "frame_count": len(normalized),
        "failure_frame_count": failure_count,
        "success_frame_count": success_count,
        "unknown_frame_count": unknown_count,
        "vision_risk": risk,
        "fusion_compatible_decision": decision,
    }


def evaluate_sequence_manifest(
    manifest_path: Path,
    base_url: str,
    token: str | None,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = _load_json(manifest_path)
    sequences = manifest.get("sequences")

    if not isinstance(sequences, list) or not sequences:
        raise Gate8CV820Error(
            "Sequence manifest must contain a non-empty 'sequences' list."
        )

    results: list[dict[str, Any]] = []

    for sequence in sequences:
        if not isinstance(sequence, dict):
            raise Gate8CV820Error("Each sequence entry must be an object.")

        name = str(sequence.get("name") or "").strip()
        expected = str(sequence.get("expected") or "").strip().lower()
        frames = sequence.get("frames")

        if not name:
            raise Gate8CV820Error("Sequence name is required.")
        if expected not in {"success", "failure"}:
            raise Gate8CV820Error(
                f"Sequence {name!r} expected must be success or failure."
            )
        if not isinstance(frames, list) or len(frames) < 2:
            raise Gate8CV820Error(
                f"Sequence {name!r} requires at least two JPEG frames."
            )

        frame_results = []
        for frame in frames:
            frame_path = Path(str(frame))
            if not frame_path.is_absolute():
                frame_path = manifest_path.parent / frame_path
            frame_results.append(classify_jpeg(base_url, frame_path, token))

        aggregate = aggregate_predictions(
            [x["prediction"] for x in frame_results]
        )

        predicted_sequence = (
            "failure"
            if aggregate["vision_risk"] == "critical"
            else "success"
            if aggregate["vision_risk"] == "normal"
            else "review"
        )

        results.append({
            "name": name,
            "expected": expected,
            "predicted_sequence": predicted_sequence,
            "correct": predicted_sequence == expected,
            "aggregate": aggregate,
            "frames": frame_results,
        })

    return {
        "manifest_file": str(manifest_path),
        "sequence_count": len(results),
        "sequence_correct_count": sum(x["correct"] for x in results),
        "sequences": results,
    }


def run(
    project_root: Path,
    success_dir: Path,
    failure_dir: Path,
    *,
    sequence_manifest: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
    token: str | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    task_dir = project_root / "outputs" / "m4" / REQUEST_ID

    gate8b_path = task_dir / GATE8B_REPORT
    gate8b = _load_json(gate8b_path)

    if gate8b.get("version") != "8.1.0":
        raise Gate8CV820Error("Gate8B version lock is not 8.1.0.")
    if gate8b.get("status") != "real_local_ai_inference_validated":
        raise Gate8CV820Error("Gate8B inference chain did not pass.")
    if gate8b.get("request_id") != REQUEST_ID:
        raise Gate8CV820Error("Gate8B request_id mismatch.")

    health = check_health(base_url, token)
    success_files = _jpeg_files(success_dir)
    failure_files = _jpeg_files(failure_dir)

    success_results = [classify_jpeg(base_url, p, token) for p in success_files]
    failure_results = [classify_jpeg(base_url, p, token) for p in failure_files]

    labeled_metrics = evaluate_labeled_results(
        success_results,
        failure_results,
    )

    sequence_results = None
    if sequence_manifest is not None:
        sequence_results = evaluate_sequence_manifest(
            sequence_manifest,
            base_url,
            token,
        )

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "8C",
        "version": "8.2.0",
        "stage": "real_ai_labeled_screening_and_multiframe_replay",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": "real_ai_screening_completed",
        "created_unix": time.time(),
        "source_gate8b": {
            "report_file": str(gate8b_path),
            "version": gate8b.get("version"),
            "status": gate8b.get("status"),
        },
        "ai_backend": {
            "name": "PrintGuard",
            "base_url": base_url,
            "health": health,
            "capability": "generic_fdm_failure_binary",
        },
        "labeled_dataset": {
            "success_directory": str(success_dir.resolve()),
            "failure_directory": str(failure_dir.resolve()),
            "success_results": success_results,
            "failure_results": failure_results,
        },
        "model_screening_metrics": labeled_metrics,
        "multiframe_replay": sequence_results,
        "model_quality_status": "NOT_ACCEPTED_YET_REVIEW_METRICS",
        "model_quality_policy": {
            "automatic_accuracy_pass_fail_threshold_used": False,
            "reason": (
                "No production acceptance threshold has been established "
                "for this project. Gate8C reports real labeled metrics "
                "without inventing a pass criterion."
            ),
            "failure_recall_is_high_priority": True,
            "single_failure_sample_is_not_sufficient": True,
        },
        "policy": {
            "office_only": True,
            "printer_connection_attempted": False,
            "x1c_camera_connection_attempted": False,
            "mqtt_publish_count": 0,
            "ftps_connection_attempted": False,
            "printer_control_count": 0,
            "model_accuracy_claimed": False,
            "real_labeled_inference_performed": True,
            "gcode_file_modified": False,
            "bambu_profile_modified": False,
        },
        "next_phase": "review_gate8c_metrics_then_freeze_model_or_replace_model",
    }

    output = task_dir / OUTPUT_REPORT
    if output.exists():
        raise Gate8CV820Error(f"Refusing to overwrite existing report: {output}")

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report | {"report_file": str(output)}
