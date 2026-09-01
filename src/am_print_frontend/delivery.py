"""Expose only the final, receipt-bound files of a completed production job."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


# A later failed attempt must never fall back to an earlier passing slice.
FINAL_STAGES = (
    ("bambu_regeneration_support_reslice", "m3_regeneration_support_printability"),
    ("bambu_regeneration_slice", "m3_regeneration_printability"),
    ("bambu_support_reslice", "m3_support_printability"),
    ("bambu_slice", "m3_printability"),
)
DOWNLOAD_NAMES = {
    "gcode": "01_含真实支撑切片_先打开这个.gcode.3mf",
    "project": "02_可编辑工程_切片后显示支撑.3mf",
    "stl": "03_仅成品本体_不含支撑.stl",
}


class DeliveryUnavailable(ValueError):
    pass


def verified_files(state: Mapping[str, Any], job_directory: Path) -> dict[str, tuple[Path, str]]:
    """Resolve an allowlist, check gates and hash every file before serving it.

    Paths are restricted to this job, including after resolving symlinks. The
    browser can select a file kind, never supply a filesystem path.
    """
    if state.get("status") not in {"ready_to_print", "print_started", "credentials_required"}:
        raise DeliveryUnavailable("任务尚未通过最终检查，暂不提供成品下载。")
    stages = state.get("stages") or {}
    selected = next(((name, gate) for name, gate in FINAL_STAGES
                     if stages.get(name, {}).get("status") != "skipped" and name in stages), None)
    if selected is None:
        raise DeliveryUnavailable("没有最终验收记录；旧任务请重新生成。")
    name, gate = selected
    record = stages[name]
    prepared = record.get("result") or {}
    receipt = prepared.get("acceptance") or {}
    validation = stages.get(gate) or {}
    oriented = receipt.get("auto_orient") or {}
    project_output = oriented.get("output") or {}
    if not (
        record.get("status") == "completed"
        and validation.get("status") == "completed"
        and (validation.get("result") or {}).get("status") == "pass"
        and prepared.get("status") == "slice_complete"
        and prepared.get("pipeline") == "verified_support_orient_reslice_v3"
        and prepared.get("support_mode") == receipt.get("support_mode") == "detachable"
        and prepared.get("model_self_support_required") is False
        and receipt.get("model_self_support_required") is False
        and prepared.get("auto_orient_applied") is True
        and receipt.get("status") == "pass"
        and receipt.get("before_orientation_status") == receipt.get("after_orientation_status") == "pass"
        and receipt.get("dangerous_layer_count") == 0
        and receipt.get("source_unchanged") is True
        and (receipt.get("flat_base") or {}).get("base_flatness_passed") is True
        and (receipt.get("removal") or {}).get("status") == "pass"
        and oriented.get("status") == "auto_orient_complete"
        and (project_output.get("upright_pose") or {}).get("status") == "pass"
        and (project_output.get("flat_base_after_orientation") or {}).get("base_flatness_passed") is True
    ):
        raise DeliveryUnavailable("最终平底、朝向、刀路或可拆支撑记录不完整，请重新生成。")
    artifact = prepared.get("artifact") or {}
    candidates = {
        "project": (receipt.get("project"), project_output.get("sha256")),
        "gcode": (artifact.get("path"), receipt.get("artifact_sha256")),
        "stl": (prepared.get("geometry_path"), receipt.get("geometry_sha256")),
    }
    root = job_directory.resolve()
    if (Path(str(receipt.get("geometry", ""))).resolve() != Path(str(prepared.get("geometry_path", ""))).resolve()
            or Path(str(receipt.get("project", ""))).resolve() != Path(str(project_output.get("path", ""))).resolve()):
        raise DeliveryUnavailable("模型路径与最终验收记录不一致。")
    files = {}
    for kind, (value, expected_hash) in candidates.items():
        if not value or not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise DeliveryUnavailable("最终文件缺少校验记录。")
        path = Path(str(value)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise DeliveryUnavailable("最终文件丢失或不属于这个任务。")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise DeliveryUnavailable("最终文件发生变化；请重新检查，不能沿用之前的通过记录。")
        files[kind] = (path, actual_hash)
    if artifact.get("sha256") != files["gcode"][1]:
        raise DeliveryUnavailable("切片文件与验收记录不一致。")
    return files


def delivery_summary(state: Mapping[str, Any], job_directory: Path) -> dict[str, Any]:
    try:
        files = verified_files(state, job_directory)
    except (DeliveryUnavailable, OSError, TypeError, AttributeError) as exc:
        return {"available": False, "message": str(exc) if isinstance(exc, DeliveryUnavailable)
                else "无法核验最终文件，请重新生成。"}
    job_id = job_directory.name
    return {
        "available": True,
        "support_mode": "detachable",
        "physical_removal_verified": False,
        "preview_url": f"/api/jobs/{job_id}/support-preview",
        "message": "已通过软件检查。默认预览和 01 文件包含真实可拆支撑；实物首层与拆卸仍需确认。",
        "files": [{"kind": kind, "name": DOWNLOAD_NAMES[kind], "sha256": digest,
                   "url": f"/api/jobs/{job_id}/files/{kind}"}
                  for kind in ("gcode", "project", "stl")
                  for _, digest in (files[kind],)],
    }
