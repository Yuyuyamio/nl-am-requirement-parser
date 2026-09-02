"""Expose only the final, receipt-bound files of a completed production job."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping
import zipfile

from am_print_executor.bambu_project_repair import (
    TEXTURED_PEI_BED_TYPE,
    TEXTURED_PEI_PLATE,
    validate_bambu_gcode_3mf,
    validate_bambu_project_3mf,
)


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
    post_slice_validation = prepared.get("post_slice_validation") or {}
    if not isinstance(post_slice_validation, Mapping):
        post_slice_validation = {}
    post_slice_artifact_validation = post_slice_validation.get("validation") or {}
    if not isinstance(post_slice_artifact_validation, Mapping):
        post_slice_artifact_validation = {}
    project_validation = prepared.get("project_container_validation") or {}
    if not isinstance(project_validation, Mapping):
        project_validation = {}
    delivery_validation = prepared.get("delivery_artifact_validation") or {}
    if not isinstance(delivery_validation, Mapping):
        delivery_validation = {}
    if not (
        record.get("status") == "completed"
        and prepared.get("status") == "slice_complete"
        and prepared.get("pipeline") == "bambu_native_direct_print_v3"
        and prepared.get("acceptance_basis")
        == "bambu_cli_slice_and_artifact_validation"
        and prepared.get("post_slice_validation_performed") is True
        and post_slice_validation.get("status") == "bambu_gcode_3mf_finalized"
        and post_slice_artifact_validation.get("status") == "bambu_gcode_3mf_validated"
        and prepared.get("project_container_validation_performed") is True
        and project_validation.get("status") == "bambu_project_3mf_validated"
        and prepared.get("delivery_artifact_validation_performed") is True
        and delivery_validation.get("status") == "bambu_gcode_3mf_validated"
    ):
        raise DeliveryUnavailable(
            "没有找到完成最终内部校验的纹理 PEI 板切片记录；旧任务请重新切片。"
        )
    build_plate = prepared.get("build_plate") or {}
    if not (
        build_plate.get("curr_bed_type") == TEXTURED_PEI_PLATE
        and build_plate.get("bed_type") == TEXTURED_PEI_BED_TYPE
    ):
        raise DeliveryUnavailable("切片记录中的打印板配置不一致，请重新切片。")
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
    try:
        validate_bambu_project_3mf(
            files["project"][0],
            expected_bed_type=TEXTURED_PEI_PLATE,
        )
        validate_bambu_gcode_3mf(
            files["gcode"][0],
            expected_bed_type=TEXTURED_PEI_PLATE,
            expected_bed_type_code=TEXTURED_PEI_BED_TYPE,
        )
    except (
        RuntimeError,
        OSError,
        ValueError,
        UnicodeError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise DeliveryUnavailable(
            "切片文件内部 ZIP/XML/JSON/MD5 或纹理 PEI 板配置校验失败，请重新切片。"
        ) from exc
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
        "message": (
            "Bambu Studio 已生成纹理 PEI 板树状支撑切片，"
            "ZIP、XML、JSON、G-code MD5 与打印板参数均已校验。"
        ),
        "files": [{"kind": kind, "name": DOWNLOAD_NAMES[kind], "sha256": digest,
                   "url": f"/api/jobs/{job_id}/files/{kind}"}
                  for kind in ("gcode", "project", "stl")
                  for _, digest in (files[kind],)],
    }
