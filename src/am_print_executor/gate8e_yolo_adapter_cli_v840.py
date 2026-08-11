from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
from typing import Any
from .gate8e_yolo_adapter_v840 import Gate8EAdapterError, Gate8EYOLOAdapter

REQUEST_ID = "M2-1E4B2301FADD"
GATE8D_REPORT = "m4_gate8d_replacement_model_candidate_v831.json"
GATE8E_REPORT = "m4_gate8e_deployment_adapter_validation_v840.json"

def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise Gate8EAdapterError(f"Failed to read JSON {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise Gate8EAdapterError(f"Expected JSON object: {path}")
    return obj

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project-root", required=True)
    args = p.parse_args(argv)
    root = Path(args.project_root).resolve()
    task_dir = root / "outputs" / "m4" / REQUEST_ID
    gate8d_path = task_dir / GATE8D_REPORT
    out_path = task_dir / GATE8E_REPORT
    try:
        if out_path.exists():
            raise Gate8EAdapterError(f"Refusing to overwrite existing report: {out_path}")
        gate8d = _load_json(gate8d_path)
        if gate8d.get("version") != "8.3.1":
            raise Gate8EAdapterError("Gate8D version lock is not 8.3.1.")
        if gate8d.get("status") != "replacement_model_candidate_evaluated":
            raise Gate8EAdapterError("Gate8D candidate evaluation is incomplete.")
        if gate8d.get("candidate_quality_status") != "CANDIDATE_METRICS_READY_FOR_REVIEW":
            raise Gate8EAdapterError("Gate8D candidate metrics are not ready.")
        weights = Path(gate8d["model"]["best_weights"]).resolve()
        if not weights.is_file():
            raise Gate8EAdapterError(f"Gate8D best.pt missing: {weights}")
        held = gate8d.get("held_out_test") or {}
        refs = held.get("predictions") or []
        if len(refs) != 75:
            raise Gate8EAdapterError(f"Expected 75 Gate8D held-out predictions, found {len(refs)}.")
        adapter = Gate8EYOLOAdapter(weights, imgsz=224)
        rows = []
        class_match_count = 0
        binary_match_count = 0
        max_delta = 0.0
        for ref in refs:
            current = adapter.classify(ref["image_file"])
            class_match = current["raw_model_class"] == ref["predicted_class"]
            binary_match = current["binary_prediction"] == ref["predicted_binary"]
            delta = abs(float(current["confidence"]) - float(ref["confidence"]))
            class_match_count += int(class_match)
            binary_match_count += int(binary_match)
            max_delta = max(max_delta, delta)
            rows.append({
                "image_file": current["image_file"],
                "gate8d_predicted_class": ref["predicted_class"],
                "gate8e_raw_model_class": current["raw_model_class"],
                "class_match": class_match,
                "gate8d_binary_prediction": ref["predicted_binary"],
                "gate8e_binary_prediction": current["binary_prediction"],
                "binary_match": binary_match,
                "confidence_abs_delta": delta,
                "vision_risk": current["vision_risk"],
                "recommended_action": current["recommended_action"],
            })
        passed = (
            class_match_count == len(refs)
            and binary_match_count == len(refs)
            and max_delta <= 1e-6
        )
        report = {
            "schema_version": "0.1.0",
            "module": "M4",
            "phase": "8E",
            "version": "8.4.0",
            "stage": "replacement_model_deployment_adapter_validation",
            "request_id": REQUEST_ID,
            "status": "deployment_adapter_validated" if passed else "deployment_adapter_mismatch",
            "source_gate8d": {
                "report_file": str(gate8d_path),
                "held_out_metrics": {
                    "sample_count": held.get("sample_count"),
                    "multiclass_accuracy": held.get("multiclass_accuracy"),
                    "binary_success_failure": held.get("binary_success_failure"),
                    "delta_vs_printguard_gate8c": held.get("delta_vs_printguard_gate8c"),
                },
            },
            "model_lock": {
                "weights_file": str(weights),
                "weights_sha256": _sha256(weights),
                "expected_classes": sorted(adapter.index_to_name.values()),
                "imgsz": 224,
            },
            "adapter_contract": {
                "normal": {"binary_prediction": "success", "vision_risk": "normal", "recommended_action": "CONTINUE"},
                "all_non_normal_classes": {
                    "binary_prediction": "failure",
                    "vision_risk": "attention",
                    "recommended_action": "REVIEW",
                    "note": "Single-frame classification never directly pauses or stops the printer."
                },
                "defect_type": None,
                "reason": "No automatic Gate7 defect taxonomy mapping is claimed without semantic review."
            },
            "deployment_consistency": {
                "sample_count": len(refs),
                "class_match_count": class_match_count,
                "binary_match_count": binary_match_count,
                "max_confidence_abs_delta": max_delta,
                "passed": passed,
                "rows": rows,
            },
            "quality_statement": {
                "new_accuracy_claim_generated": False,
                "gate8d_metrics_reused_as_source_of_truth": True,
                "production_acceptance_claimed": False,
                "x1c_camera_generalization_claimed": False,
                "automatic_pause_stop_enabled": False,
            },
            "policy": {
                "office_only": True,
                "printer_connection_attempted": False,
                "camera_connection_attempted": False,
                "mqtt_publish_count": 0,
                "ftps_connection_attempted": False,
                "printer_control_count": 0,
            },
            "next_phase": "gate8f_multiframe_shadow_fusion" if passed else "repair_gate8e_adapter",
        }
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "module":"M4","phase":"8E","version":"8.4.0","status":report["status"],
            "weights_sha256":report["model_lock"]["weights_sha256"],
            "deployment_consistency":{
                "sample_count":len(refs),
                "class_match_count":class_match_count,
                "binary_match_count":binary_match_count,
                "max_confidence_abs_delta":max_delta,
                "passed":passed
            },
            "report_file":str(out_path),
            "next_phase":report["next_phase"]
        }, ensure_ascii=False, indent=2))
        return 0 if passed else 2
    except Gate8EAdapterError as exc:
        print(json.dumps({"module":"M4","phase":"8E","version":"8.4.0","status":"gate8e_blocked","error":str(exc)}, ensure_ascii=False, indent=2))
        return 2

if __name__ == "__main__":
    sys.exit(main())
