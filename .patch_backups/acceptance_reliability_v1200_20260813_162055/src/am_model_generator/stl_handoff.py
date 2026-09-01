from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from jsonschema import Draft202012Validator

from .contracts import (
    M2ProviderError,
    ensure_valid_m2_manifest,
)
from .mesh_validation import (
    validate_m2_mesh,
)
from .normalization import (
    ensure_valid_normalization_receipt,
)
from .normalized_validation import (
    ensure_valid_normalized_mesh_report,
)


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]

STL_HANDOFF_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_stl_handoff.schema.json"
)

M2_MANIFEST_FILENAME = "m2_manifest.json"
PROVIDER_REQUEST_FILENAME = "provider_request.json"

NORMALIZED_MODEL_FILENAME = (
    "normalized_model.glb"
)
NORMALIZATION_RECEIPT_FILENAME = (
    "normalization_receipt.json"
)
NORMALIZED_VALIDATION_FILENAME = (
    "normalized_mesh_validation.json"
)

M2_STL_FILENAME = "m2_output.stl"
STL_VALIDATION_FILENAME = (
    "stl_validation.json"
)
STL_HANDOFF_FILENAME = (
    "m2_stl_handoff.json"
)


def _read_json_object(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_STL_FILE_MISSING",
            f"{label}不存在",
            details={
                "path": str(path),
            },
        )

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except json.JSONDecodeError as error:
        raise M2ProviderError(
            "M2_STL_INVALID_JSON",
            f"{label}不是合法JSON",
            details={
                "path": str(path),
                "line": error.lineno,
                "column": error.colno,
                "reason": error.msg,
            },
        ) from error
    except OSError as error:
        raise M2ProviderError(
            "M2_STL_READ_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_STL_INVALID_OBJECT",
            f"{label}必须是JSON对象",
            details={
                "path": str(path),
            },
        )

    return data


def _write_json_atomic(
    path: Path,
    data: dict[str, Any],
) -> None:
    temporary = path.with_name(
        f".{path.name}.tmp-{uuid.uuid4().hex}"
    )

    try:
        temporary.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    except OSError as error:
        try:
            temporary.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise M2ProviderError(
            "M2_STL_WRITE_FAILED",
            "无法写入STL交接记录",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _write_bytes_atomic(
    path: Path,
    data: bytes,
) -> None:
    if not isinstance(data, bytes):
        raise M2ProviderError(
            "M2_STL_EXPORT_INVALID",
            "STL导出器没有返回二进制数据",
            details={
                "actual_type": (
                    type(data).__name__
                ),
            },
        )

    if len(data) < 84:
        raise M2ProviderError(
            "M2_STL_EXPORT_INVALID",
            "STL二进制数据过小或已损坏",
            details={
                "size_bytes": len(data),
            },
        )

    temporary = path.with_name(
        f".{path.name}.tmp-{uuid.uuid4().hex}"
    )

    try:
        temporary.write_bytes(data)
        temporary.replace(path)

    except OSError as error:
        try:
            temporary.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise M2ProviderError(
            "M2_STL_WRITE_FAILED",
            "无法写入STL文件",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _sha256_file(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            block = file.read(
                1024 * 1024
            )

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


def _load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(
            STL_HANDOFF_SCHEMA_PATH.read_text(
                encoding="utf-8-sig"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as error:
        raise M2ProviderError(
            "M2_STL_SCHEMA_LOAD_FAILED",
            "无法读取STL Handoff Schema",
            details={
                "path": str(
                    STL_HANDOFF_SCHEMA_PATH
                ),
                "reason": str(error),
            },
        ) from error

    if not isinstance(schema, dict):
        raise M2ProviderError(
            "M2_STL_SCHEMA_INVALID",
            "STL Handoff Schema必须是JSON对象",
        )

    Draft202012Validator.check_schema(
        schema
    )

    return schema


def validate_stl_handoff(
    data: Any,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: document must be a JSON object"
        ]

    validator = Draft202012Validator(
        _load_schema()
    )

    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    formatted: list[str] = []

    for error in errors:
        path = "$"

        for part in error.absolute_path:
            if isinstance(part, int):
                path += f"[{part}]"
            else:
                path += f".{part}"

        formatted.append(
            f"{path}: {error.message}"
        )

    return formatted


def ensure_valid_stl_handoff(
    data: Any,
) -> None:
    errors = validate_stl_handoff(
        data
    )

    if errors:
        raise M2ProviderError(
            "M2_STL_HANDOFF_INVALID",
            "STL Handoff不符合Schema",
            details={
                "errors": errors,
            },
        )


def _load_combined_mesh(
    path: Path,
    *,
    process: bool,
) -> tuple[
    trimesh.Trimesh,
    int,
]:
    try:
        scene = trimesh.load_scene(
            path,
            process=process,
        )
    except Exception as error:
        raise M2ProviderError(
            "M2_STL_MODEL_LOAD_FAILED",
            "Trimesh无法读取模型",
            details={
                "path": str(path),
                "error_type": (
                    type(error).__name__
                ),
                "reason": str(error),
            },
        ) from error

    geometries = [
        geometry
        for geometry in scene.geometry.values()
        if isinstance(
            geometry,
            trimesh.Trimesh,
        )
    ]

    geometry_count = len(geometries)

    if geometry_count == 0:
        raise M2ProviderError(
            "M2_STL_NO_GEOMETRY",
            "模型不包含Mesh几何体",
            details={
                "path": str(path),
            },
        )

    try:
        if hasattr(scene, "to_mesh"):
            mesh = scene.to_mesh()
        else:
            mesh = scene.dump(
                concatenate=True
            )
    except Exception as error:
        raise M2ProviderError(
            "M2_STL_COMBINE_FAILED",
            "无法合并模型场景",
            details={
                "path": str(path),
                "error_type": (
                    type(error).__name__
                ),
                "reason": str(error),
            },
        ) from error

    if not isinstance(
        mesh,
        trimesh.Trimesh,
    ):
        raise M2ProviderError(
            "M2_STL_COMBINE_INVALID",
            "合并后的对象不是Trimesh",
        )

    if (
        len(mesh.vertices) == 0
        or len(mesh.faces) == 0
    ):
        raise M2ProviderError(
            "M2_STL_NO_GEOMETRY",
            "模型没有有效三角面",
        )

    return mesh, geometry_count


def _round_number(
    value: float,
    *,
    digits: int = 6,
) -> float:
    number = float(value)

    if not math.isfinite(number):
        raise M2ProviderError(
            "M2_STL_NONFINITE_VALUE",
            "模型分析出现非有限数值",
            details={
                "value": repr(value),
            },
        )

    return round(number, digits)


def _mesh_summary(
    mesh: trimesh.Trimesh,
    *,
    geometry_count: int,
) -> dict[str, Any]:
    components = mesh.split(
        only_watertight=False
    )

    if (
        mesh.extents is None
        or len(mesh.extents) != 3
        or mesh.bounds is None
        or len(mesh.bounds) != 2
    ):
        raise M2ProviderError(
            "M2_STL_BOUNDS_INVALID",
            "模型边界或包围盒无效",
        )

    return {
        "geometry_count": int(
            geometry_count
        ),
        "vertex_count": int(
            len(mesh.vertices)
        ),
        "face_count": int(
            len(mesh.faces)
        ),
        "connected_components": int(
            len(components)
        ),
        "watertight": bool(
            mesh.is_watertight
        ),
        "winding_consistent": bool(
            mesh.is_winding_consistent
        ),
        "volume_mm3": _round_number(
            float(mesh.volume)
        ),
        "surface_area_mm2": (
            _round_number(
                float(mesh.area)
            )
        ),
        "extents_mm": [
            _round_number(value)
            for value in mesh.extents
        ],
        "bounds_mm": [
            [
                _round_number(value)
                for value in row
            ]
            for row in mesh.bounds
        ],
    }


def _vector_matches(
    first: list[float],
    second: list[float],
    *,
    tolerance: float,
) -> bool:
    return bool(
        len(first) == len(second)
        and all(
            math.isclose(
                float(a),
                float(b),
                rel_tol=1e-7,
                abs_tol=tolerance,
            )
            for a, b in zip(
                first,
                second,
                strict=True,
            )
        )
    )


def _updated_manifest(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(manifest)

    updated["status"] = "complete"
    updated["primary_model"] = (
        M2_STL_FILENAME
    )
    updated["validation_file"] = (
        STL_VALIDATION_FILENAME
    )
    updated[
        "hard_constraints_passed"
    ] = True
    updated["next_module"] = "M3"

    ensure_valid_m2_manifest(
        updated
    )

    return updated


def _result(
    *,
    task_path: Path,
    handoff: dict[str, Any],
    reused_existing_handoff: bool,
    manifest_changed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "stl_handoff_ready",
        "request_id": (
            handoff["request_id"]
        ),
        "m3_model_file": str(
            task_path
            / M2_STL_FILENAME
        ),
        "m3_model_format": "stl",
        "stl_validation_file": str(
            task_path
            / STL_VALIDATION_FILENAME
        ),
        "stl_handoff_file": str(
            task_path
            / STL_HANDOFF_FILENAME
        ),
        "reused_existing_handoff": (
            reused_existing_handoff
        ),
        "manifest_changed": (
            manifest_changed
        ),
        "next_module": "M3",
    }


def create_m2_stl_handoff(
    task_directory: str | Path,
) -> dict[str, Any]:
    task_path = Path(
        task_directory
    ).expanduser().resolve()

    if not task_path.is_dir():
        raise M2ProviderError(
            "M2_TASK_DIRECTORY_MISSING",
            "M2任务目录不存在",
            details={
                "path": str(task_path),
            },
        )

    manifest_path = (
        task_path
        / M2_MANIFEST_FILENAME
    )
    provider_request_path = (
        task_path
        / PROVIDER_REQUEST_FILENAME
    )
    normalized_model_path = (
        task_path
        / NORMALIZED_MODEL_FILENAME
    )
    normalization_receipt_path = (
        task_path
        / NORMALIZATION_RECEIPT_FILENAME
    )
    normalized_validation_path = (
        task_path
        / NORMALIZED_VALIDATION_FILENAME
    )
    stl_path = (
        task_path
        / M2_STL_FILENAME
    )
    stl_validation_path = (
        task_path
        / STL_VALIDATION_FILENAME
    )
    handoff_path = (
        task_path
        / STL_HANDOFF_FILENAME
    )

    manifest = _read_json_object(
        manifest_path,
        label="M2 Manifest",
    )
    provider_request = (
        _read_json_object(
            provider_request_path,
            label="Provider Request",
        )
    )
    normalization_receipt = (
        _read_json_object(
            normalization_receipt_path,
            label=(
                "Normalization Receipt"
            ),
        )
    )
    normalized_validation = (
        _read_json_object(
            normalized_validation_path,
            label=(
                "Normalized Mesh Validation"
            ),
        )
    )

    ensure_valid_m2_manifest(
        manifest
    )
    ensure_valid_normalization_receipt(
        normalization_receipt
    )

    request_ids = {
        manifest.get("request_id"),
        provider_request.get(
            "request_id"
        ),
        normalization_receipt.get(
            "request_id"
        ),
        normalized_validation.get(
            "request_id"
        ),
    }

    if len(request_ids) != 1:
        raise M2ProviderError(
            "M2_STL_REQUEST_ID_MISMATCH",
            "STL交接输入文件的request_id不一致",
            details={
                "request_ids": [
                    str(value)
                    for value in request_ids
                ],
            },
        )

    if manifest.get("status") not in {
        "generated",
        "complete",
    }:
        raise M2ProviderError(
            "M2_STL_STATUS_INVALID",
            "当前Manifest状态不能生成STL交接",
            details={
                "status": manifest.get(
                    "status"
                ),
            },
        )

    if manifest.get("status") == (
        "generated"
    ):
        if (
            manifest.get(
                "primary_model"
            )
            != NORMALIZED_MODEL_FILENAME
            or manifest.get(
                "validation_file"
            )
            != NORMALIZED_VALIDATION_FILENAME
            or manifest.get(
                "hard_constraints_passed"
            )
            is not True
        ):
            raise M2ProviderError(
                "M2_STL_NORMALIZED_MODEL_NOT_READY",
                "必须先完成归一化模型验证",
                details={
                    "primary_model": (
                        manifest.get(
                            "primary_model"
                        )
                    ),
                    "validation_file": (
                        manifest.get(
                            "validation_file"
                        )
                    ),
                    "hard_constraints_passed": (
                        manifest.get(
                            "hard_constraints_passed"
                        )
                    ),
                },
            )

    if manifest.get("status") == (
        "complete"
    ):
        if (
            manifest.get(
                "primary_model"
            )
            != M2_STL_FILENAME
            or manifest.get(
                "validation_file"
            )
            != STL_VALIDATION_FILENAME
            or manifest.get(
                "hard_constraints_passed"
            )
            is not True
            or manifest.get(
                "next_module"
            )
            != "M3"
        ):
            raise M2ProviderError(
                "M2_STL_COMPLETE_STATE_CONFLICT",
                "complete状态的Manifest不是STL唯一交付结构",
            )

    if manifest.get("status") == (
        "generated"
    ):
        ensure_valid_normalized_mesh_report(
            normalized_validation
        )

    normalized_sha256 = (
        _sha256_file(
            normalized_model_path
        )
    )
    normalized_size = (
        normalized_model_path.stat().st_size
    )

    if (
        normalization_receipt.get(
            "normalized_model_file"
        )
        != NORMALIZED_MODEL_FILENAME
        or normalization_receipt.get(
            "normalized_model_sha256"
        )
        != normalized_sha256
        or normalization_receipt.get(
            "normalized_size_bytes"
        )
        != normalized_size
        or normalized_validation.get(
            "model_file"
        )
        != NORMALIZED_MODEL_FILENAME
        or normalized_validation.get(
            "model_sha256"
        )
        != normalized_sha256
        or normalized_validation.get(
            "model_size_bytes"
        )
        != normalized_size
        or normalized_validation.get(
            "hard_constraints_passed"
        )
        is not True
    ):
        raise M2ProviderError(
            "M2_STL_SOURCE_RECORD_MISMATCH",
            "归一化模型与Receipt或验证报告不一致",
        )

    target_height = (
        provider_request.get(
            "target_height_mm"
        )
    )

    if (
        isinstance(target_height, bool)
        or not isinstance(
            target_height,
            (
                int,
                float,
            ),
        )
        or not math.isfinite(
            float(target_height)
        )
        or float(target_height) <= 0
    ):
        raise M2ProviderError(
            "M2_STL_TARGET_HEIGHT_INVALID",
            "Provider Request没有有效目标高度",
        )

    target_height_mm = float(
        target_height
    )

    states = [
        stl_path.exists(),
        stl_validation_path.exists(),
        handoff_path.exists(),
    ]

    if any(states) and not all(states):
        raise M2ProviderError(
            "M2_STL_STATE_INCOMPLETE",
            "STL、验证报告和Handoff状态不完整，已停止覆盖",
            details={
                "stl_exists": states[0],
                "validation_exists": (
                    states[1]
                ),
                "handoff_exists": (
                    states[2]
                ),
            },
        )

    if all(states):
        handoff = _read_json_object(
            handoff_path,
            label="M2 STL Handoff",
        )
        stl_validation = (
            _read_json_object(
                stl_validation_path,
                label="STL Validation",
            )
        )

        ensure_valid_stl_handoff(
            handoff
        )

        actual_stl_sha256 = (
            _sha256_file(stl_path)
        )
        actual_stl_size = (
            stl_path.stat().st_size
        )

        if (
            handoff.get("request_id")
            != manifest.get("request_id")
            or handoff.get(
                "source_model_sha256"
            )
            != normalized_sha256
            or handoff.get(
                "stl_sha256"
            )
            != actual_stl_sha256
            or handoff.get(
                "stl_size_bytes"
            )
            != actual_stl_size
            or stl_validation.get(
                "model_file"
            )
            != M2_STL_FILENAME
            or stl_validation.get(
                "model_sha256"
            )
            != actual_stl_sha256
            or stl_validation.get(
                "model_size_bytes"
            )
            != actual_stl_size
            or stl_validation.get(
                "hard_constraints_passed"
            )
            is not True
        ):
            raise M2ProviderError(
                "M2_STL_HANDOFF_CONFLICT",
                "已有STL交接结果与当前任务不一致",
            )

        updated_manifest = (
            _updated_manifest(manifest)
        )
        manifest_changed = (
            updated_manifest != manifest
        )

        if manifest_changed:
            _write_json_atomic(
                manifest_path,
                updated_manifest,
            )

        return _result(
            task_path=task_path,
            handoff=handoff,
            reused_existing_handoff=True,
            manifest_changed=(
                manifest_changed
            ),
        )

    source_mesh, source_geometry_count = (
        _load_combined_mesh(
            normalized_model_path,
            process=False,
        )
    )
    source_summary = _mesh_summary(
        source_mesh,
        geometry_count=(
            source_geometry_count
        ),
    )

    source_bounds = np.asarray(
        source_mesh.bounds,
        dtype=float,
    )

    source_checks = {
        "single_component": (
            source_summary[
                "connected_components"
            ]
            == 1
        ),
        "watertight": bool(
            source_summary["watertight"]
        ),
        "winding_consistent": bool(
            source_summary[
                "winding_consistent"
            ]
        ),
        "positive_volume": (
            source_summary[
                "volume_mm3"
            ]
            > 0
        ),
        "height_matches_target": (
            math.isclose(
                source_summary[
                    "extents_mm"
                ][2],
                target_height_mm,
                rel_tol=1e-7,
                abs_tol=max(
                    0.05,
                    target_height_mm * 0.001,
                ),
            )
        ),
        "base_aligned_z0": (
            math.isclose(
                float(
                    source_bounds[0][2]
                ),
                0.0,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        ),
    }

    if not all(
        source_checks.values()
    ):
        raise M2ProviderError(
            "M2_STL_SOURCE_CHECKS_FAILED",
            "归一化模型未达到STL交付条件",
            details={
                "source_checks": (
                    source_checks
                ),
            },
        )

    translation = -source_bounds[0]

    stl_mesh = source_mesh.copy()
    stl_mesh.apply_translation(
        translation
    )

    stl_preexport_summary = (
        _mesh_summary(
            stl_mesh,
            geometry_count=1,
        )
    )

    preexport_checks = {
        "positive_octant": all(
            value >= -1e-6
            for value in (
                stl_preexport_summary[
                    "bounds_mm"
                ][0]
            )
        ),
        "base_aligned_z0": (
            math.isclose(
                stl_preexport_summary[
                    "bounds_mm"
                ][0][2],
                0.0,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        ),
        "extents_preserved": (
            _vector_matches(
                source_summary[
                    "extents_mm"
                ],
                stl_preexport_summary[
                    "extents_mm"
                ],
                tolerance=1e-6,
            )
        ),
        "single_component": (
            stl_preexport_summary[
                "connected_components"
            ]
            == 1
        ),
        "watertight": bool(
            stl_preexport_summary[
                "watertight"
            ]
        ),
        "winding_consistent": bool(
            stl_preexport_summary[
                "winding_consistent"
            ]
        ),
        "positive_volume": (
            stl_preexport_summary[
                "volume_mm3"
            ]
            > 0
        ),
    }

    if not all(
        preexport_checks.values()
    ):
        raise M2ProviderError(
            "M2_STL_PREEXPORT_CHECKS_FAILED",
            "STL导出前的制造坐标模型未通过检查",
            details={
                "preexport_checks": (
                    preexport_checks
                ),
            },
        )

    try:
        stl_data = (
            trimesh.exchange.stl.export_stl(
                stl_mesh
            )
        )
    except Exception as error:
        raise M2ProviderError(
            "M2_STL_EXPORT_FAILED",
            "STL导出失败",
            details={
                "error_type": (
                    type(error).__name__
                ),
                "reason": str(error),
            },
        ) from error

    normalized_sha256_before = (
        normalized_sha256
    )

    _write_bytes_atomic(
        stl_path,
        stl_data,
    )

    try:
        roundtrip_mesh, geometry_count = (
            _load_combined_mesh(
                stl_path,
                process=True,
            )
        )
        roundtrip_summary = (
            _mesh_summary(
                roundtrip_mesh,
                geometry_count=(
                    geometry_count
                ),
            )
        )
    except Exception:
        stl_path.unlink(
            missing_ok=True
        )
        raise

    stl_checks = {
        "binary_stl": (
            stl_path.stat().st_size
            == (
                84
                + (
                    roundtrip_summary[
                        "face_count"
                    ]
                    * 50
                )
            )
        ),
        "single_component": (
            roundtrip_summary[
                "connected_components"
            ]
            == 1
        ),
        "watertight": bool(
            roundtrip_summary[
                "watertight"
            ]
        ),
        "winding_consistent": bool(
            roundtrip_summary[
                "winding_consistent"
            ]
        ),
        "positive_volume": (
            roundtrip_summary[
                "volume_mm3"
            ]
            > 0
        ),
        "positive_octant": all(
            value >= -1e-5
            for value in (
                roundtrip_summary[
                    "bounds_mm"
                ][0]
            )
        ),
        "base_aligned_z0": (
            math.isclose(
                roundtrip_summary[
                    "bounds_mm"
                ][0][2],
                0.0,
                rel_tol=0.0,
                abs_tol=1e-5,
            )
        ),
        "extents_match_source": (
            _vector_matches(
                roundtrip_summary[
                    "extents_mm"
                ],
                source_summary[
                    "extents_mm"
                ],
                tolerance=1e-4,
            )
        ),
        "height_matches_target": (
            math.isclose(
                roundtrip_summary[
                    "extents_mm"
                ][2],
                target_height_mm,
                rel_tol=1e-7,
                abs_tol=1e-4,
            )
        ),
    }

    hard_constraints_passed = all(
        stl_checks.values()
    )

    if not hard_constraints_passed:
        stl_path.unlink(
            missing_ok=True
        )
        raise M2ProviderError(
            "M2_STL_ROUNDTRIP_FAILED",
            "STL回读验证失败",
            details={
                "stl_checks": stl_checks,
                "roundtrip_summary": (
                    roundtrip_summary
                ),
            },
        )

    if (
        _sha256_file(
            normalized_model_path
        )
        != normalized_sha256_before
    ):
        stl_path.unlink(
            missing_ok=True
        )
        raise M2ProviderError(
            "M2_STL_SOURCE_CHANGED",
            "归一化模型在STL导出期间发生变化",
        )

    stl_sha256 = _sha256_file(
        stl_path
    )
    stl_size = (
        stl_path.stat().st_size
    )

    stl_validation = {
        "schema_version": "0.1.0",
        "module": "M2",
        "request_id": (
            manifest["request_id"]
        ),
        "model_file": (
            M2_STL_FILENAME
        ),
        "model_sha256": stl_sha256,
        "model_size_bytes": (
            stl_size
        ),
        "format": "stl",
        "media_type": "model/stl",
        "binary": True,
        "unit": (
            "millimeter_declared_by_handoff"
        ),
        "source_model_file": (
            NORMALIZED_MODEL_FILENAME
        ),
        "source_model_sha256": (
            normalized_sha256
        ),
        "geometry_count": (
            roundtrip_summary[
                "geometry_count"
            ]
        ),
        "vertex_count": (
            roundtrip_summary[
                "vertex_count"
            ]
        ),
        "face_count": (
            roundtrip_summary[
                "face_count"
            ]
        ),
        "connected_components": (
            roundtrip_summary[
                "connected_components"
            ]
        ),
        "watertight": (
            roundtrip_summary[
                "watertight"
            ]
        ),
        "winding_consistent": (
            roundtrip_summary[
                "winding_consistent"
            ]
        ),
        "volume_mm3": (
            roundtrip_summary[
                "volume_mm3"
            ]
        ),
        "surface_area_mm2": (
            roundtrip_summary[
                "surface_area_mm2"
            ]
        ),
        "extents_mm": (
            roundtrip_summary[
                "extents_mm"
            ]
        ),
        "bounds_mm": (
            roundtrip_summary[
                "bounds_mm"
            ]
        ),
        "target_height_mm": (
            _round_number(
                target_height_mm
            )
        ),
        "hard_checks": stl_checks,
        "hard_constraints_passed": (
            hard_constraints_passed
        ),
        "errors": [],
        "warnings": [
            (
                "STL does not embed units; "
                "M3 must interpret coordinates "
                "as millimeters"
            )
        ],
        "library": "trimesh",
        "library_version": str(
            trimesh.__version__
        ),
    }

    handoff = {
        "schema_version": "0.1.0",
        "module": "M2",
        "request_id": (
            manifest["request_id"]
        ),
        "status": "ready_for_M3",
        "m3_model_file": (
            M2_STL_FILENAME
        ),
        "m3_model_format": "stl",
        "m3_model_media_type": (
            "model/stl"
        ),
        "m3_coordinate_unit": (
            "millimeter"
        ),
        "source_model_file": (
            NORMALIZED_MODEL_FILENAME
        ),
        "source_model_sha256": (
            normalized_sha256
        ),
        "source_model_size_bytes": (
            normalized_size
        ),
        "source_validation_file": (
            NORMALIZED_VALIDATION_FILENAME
        ),
        "stl_validation_file": (
            STL_VALIDATION_FILENAME
        ),
        "stl_sha256": stl_sha256,
        "stl_size_bytes": stl_size,
        "target_height_mm": (
            _round_number(
                target_height_mm
            )
        ),
        "manufacturing_transform": {
            "translation_mm": [
                _round_number(value)
                for value in translation
            ],
            "scale_factor": 1.0,
            "rotation_applied": False,
        },
        "source_checks": (
            source_checks
        ),
        "stl_checks": stl_checks,
        "source_unchanged": True,
        "geometry_handoff_only": True,
        "m3_pending_work": [
            "printer profile",
            "material profile",
            "nozzle diameter",
            "layer height",
            "wall thickness evaluation",
            "clearance evaluation",
            "support strategy",
            "slicing",
            "g-code generation",
        ],
        "next_module": "M3",
        "library": "trimesh",
        "library_version": str(
            trimesh.__version__
        ),
    }

    ensure_valid_stl_handoff(
        handoff
    )

    _write_json_atomic(
        stl_validation_path,
        stl_validation,
    )
    _write_json_atomic(
        handoff_path,
        handoff,
    )

    updated_manifest = (
        _updated_manifest(manifest)
    )
    manifest_changed = (
        updated_manifest != manifest
    )

    if manifest_changed:
        _write_json_atomic(
            manifest_path,
            updated_manifest,
        )

    return _result(
        task_path=task_path,
        handoff=handoff,
        reused_existing_handoff=False,
        manifest_changed=(
            manifest_changed
        ),
    )
