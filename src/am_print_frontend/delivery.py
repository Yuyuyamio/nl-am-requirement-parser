"""Expose only the final, receipt-bound files of a completed production job."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


DOWNLOAD_NAMES = {
    "gcode": "01_含真实支撑切片_先打开这个.gcode.3mf",
    "project": "02_可编辑工程_切片后显示支撑.3mf",
    "stl": "03_仅成品本体_不含支撑.stl",
}


class DeliveryUnavailable(ValueError):
    pass


def verified_files(state: Mapping[str, Any], job_directory: Path) -> dict[str, tuple[Path, str]]:
    """Resolve the Bambu-produced files and hash them before serving.

    Paths are restricted to this job, including after resolving symlinks. The
    browser can select a file kind, never supply a filesystem path.
    """
    if state.get("status") not in {"ready_to_print", "print_started", "credentials_required"}:
        raise DeliveryUnavailable("Bambu Studio 尚未完成本任务的切片。")
    stages = state.get("stages") or {}
    record = stages.get("bambu_slice") or {}
    prepared = record.get("result") or {}
    if not (
        record.get("status") == "completed"
        and prepared.get("status") == "slice_complete"
        and prepared.get("pipeline") == "bambu_native_direct_print_v2"
        and prepared.get("acceptance_basis") == "bambu_cli_slice_success"
        and prepared.get("post_slice_validation_performed") is False
    ):
        raise DeliveryUnavailable("没有找到新版 Bambu Studio 成功切片记录；旧任务请重新切片。")
    artifact = prepared.get("artifact") or {}
    project = prepared.get("project") or {}
    candidates = {
        "project": (project.get("path"), project.get("sha256")),
        "gcode": (artifact.get("path"), artifact.get("sha256")),
        "stl": (prepared.get("geometry_path"), prepared.get("geometry_sha256")),
    }
    root = job_directory.resolve()
    files = {}
    for kind, (value, expected_hash) in candidates.items():
        if not value or not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise DeliveryUnavailable("Bambu 切片文件缺少完整性记录。")
        path = Path(str(value)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise DeliveryUnavailable("Bambu 切片文件丢失或不属于这个任务。")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise DeliveryUnavailable("Bambu 切片完成后文件发生了变化，请重新切片。")
        files[kind] = (path, actual_hash)
    if artifact.get("sha256") != files["gcode"][1]:
        raise DeliveryUnavailable("切片文件完整性记录不一致。")
    return files


def delivery_summary(state: Mapping[str, Any], job_directory: Path) -> dict[str, Any]:
    try:
        files = verified_files(state, job_directory)
    except (DeliveryUnavailable, OSError, TypeError, AttributeError) as exc:
        return {"available": False, "message": str(exc) if isinstance(exc, DeliveryUnavailable)
                else "无法读取 Bambu 切片文件，请重新切片。"}
    job_id = job_directory.name
    return {
        "available": True,
        "support_mode": "detachable",
        "physical_removal_verified": False,
        "preview_url": f"/api/jobs/{job_id}/support-preview",
        "message": "Bambu Studio 已成功生成树状支撑切片，系统未再执行额外可打印性检查。",
        "files": [{"kind": kind, "name": DOWNLOAD_NAMES[kind], "sha256": digest,
                   "url": f"/api/jobs/{job_id}/files/{kind}"}
                  for kind in ("gcode", "project", "stl")
                  for _, digest in (files[kind],)],
    }
