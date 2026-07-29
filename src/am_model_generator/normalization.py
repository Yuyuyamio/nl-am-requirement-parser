from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path
from typing import Any

import trimesh
from jsonschema import Draft202012Validator

from .artifacts import (
    ensure_valid_artifact_receipt,
)
from .contracts import (
    M2ProviderError,
    ensure_valid_m2_manifest,
)
from .mesh_validation import (
    ensure_valid_mesh_report,
)
from .providers import (
    ensure_valid_provider_request,
)


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]

NORMALIZATION_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_normalization_receipt.schema.json"
)

M2_MANIFEST_FILENAME = "m2_manifest.json"
PROVIDER_REQUEST_FILENAME = "provider_request.json"
ARTIFACT_RECEIPT_FILENAME = "artifact_receipt.json"
RAW_VALIDATION_FILENAME = "mesh_validation.json"

NORMALIZED_MODEL_FILENAME = (
    "normalized_model.glb"
)

NORMALIZATION_RECEIPT_FILENAME = (
    "normalization_receipt.json"
)


def _read_json_object(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_NORMALIZATION_FILE_MISSING",
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
            "M2_NORMALIZATION_INVALID_JSON",
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
            "M2_NORMALIZATION_READ_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_NORMALIZATION_INVALID_OBJECT",
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
    temporary_path = path.with_name(
        f".{path.name}.tmp-{uuid.uuid4().hex}"
    )

    try:
        temporary_path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        temporary_path.replace(path)

    except OSError as error:
        try:
            temporary_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise M2ProviderError(
            "M2_NORMALIZATION_WRITE_FAILED",
            "无法写入尺寸归一化记录",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _load_normalization_schema(
) -> dict[str, Any]:
    schema = json.loads(
        NORMALIZATION_SCHEMA_PATH.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(schema, dict):
        raise M2ProviderError(
            "M2_NORMALIZATION_SCHEMA_INVALID",
            "Normalization Schema必须是JSON对象",
        )

    Draft202012Validator.check_schema(
        schema
    )

    return schema


def validate_normalization_receipt(
    data: Any,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: document must be a JSON object"
        ]

    validator = Draft202012Validator(
        _load_normalization_schema()
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


def ensure_valid_normalization_receipt(
    data: Any,
) -> None:
    errors = validate_normalization_receipt(
        data
    )

    if errors:
        raise M2ProviderError(
            "M2_NORMALIZATION_RECEIPT_INVALID",
            "Normalization Receipt不符合Schema",
            details={
                "errors": errors,
            },
        )


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


def _round_number(
    value: float,
) -> float:
    return round(
        float(value),
        6,
    )


def _extents_dict(
    mesh: trimesh.Trimesh,
) -> dict[str, float]:
    if (
        mesh.extents is None
        or len(mesh.extents) != 3
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_EXTENTS_INVALID",
            "无法获得模型包围盒尺寸",
        )

    values = [
        float(value)
        for value in mesh.extents
    ]

    if not all(
        math.isfinite(value)
        and value > 0
        for value in values
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_EXTENTS_INVALID",
            "模型包围盒包含无效尺寸",
            details={
                "extents": values,
            },
        )

    return {
        "x": _round_number(values[0]),
        "y": _round_number(values[1]),
        "z": _round_number(values[2]),
    }


def _load_combined_mesh(
    model_path: Path,
) -> trimesh.Trimesh:
    try:
        scene = trimesh.load_scene(
            model_path,
            process=False,
        )
    except Exception as error:
        raise M2ProviderError(
            "M2_NORMALIZATION_LOAD_FAILED",
            "Trimesh无法读取原始模型",
            details={
                "path": str(model_path),
                "error_type": (
                    type(error).__name__
                ),
                "reason": str(error),
            },
        ) from error

    mesh_geometries = [
        geometry
        for geometry in scene.geometry.values()
        if isinstance(
            geometry,
            trimesh.Trimesh,
        )
    ]

    if not mesh_geometries:
        raise M2ProviderError(
            "M2_NORMALIZATION_NO_GEOMETRY",
            "原始模型不包含可缩放几何体",
        )

    try:
        if hasattr(
            scene,
            "to_mesh",
        ):
            combined = scene.to_mesh()
        else:
            combined = scene.dump(
                concatenate=True
            )
    except Exception as error:
        raise M2ProviderError(
            "M2_NORMALIZATION_COMBINE_FAILED",
            "无法合并原始模型场景",
            details={
                "error_type": (
                    type(error).__name__
                ),
                "reason": str(error),
            },
        ) from error

    if not isinstance(
        combined,
        trimesh.Trimesh,
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_COMBINE_INVALID",
            "合并后的模型不是Trimesh",
            details={
                "actual_type": (
                    type(combined).__name__
                ),
            },
        )

    if (
        len(combined.vertices) == 0
        or len(combined.faces) == 0
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_NO_GEOMETRY",
            "合并后的模型没有有效三角面",
        )

    return combined


def _export_glb_atomic(
    mesh: trimesh.Trimesh,
    destination_path: Path,
) -> None:
    temporary_path = destination_path.with_name(
        (
            f".{destination_path.name}."
            f"tmp-{uuid.uuid4().hex}"
        )
    )

    try:
        scene = trimesh.Scene(
            mesh
        )

        glb_data = (
            trimesh.exchange.gltf.export_glb(
                scene
            )
        )

        if not isinstance(
            glb_data,
            bytes,
        ):
            raise M2ProviderError(
                "M2_NORMALIZATION_EXPORT_INVALID",
                "Trimesh没有返回GLB二进制数据",
                details={
                    "actual_type": (
                        type(
                            glb_data
                        ).__name__
                    ),
                },
            )

        temporary_path.write_bytes(
            glb_data
        )

        temporary_path.replace(
            destination_path
        )

    except Exception:
        try:
            temporary_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise


def _updated_manifest(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(manifest)

    updated["status"] = "generated"
    updated["primary_model"] = (
        NORMALIZED_MODEL_FILENAME
    )

    # 原mesh_validation.json验证的是raw_model.glb。
    # 换成normalized_model.glb后必须重新验证。
    updated["validation_file"] = None
    updated[
        "hard_constraints_passed"
    ] = None
    updated["next_module"] = None

    ensure_valid_m2_manifest(
        updated
    )

    return updated


def _result_data(
    *,
    task_path: Path,
    receipt: dict[str, Any],
    reused_existing_normalization: bool,
    manifest_changed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "normalized",
        "request_id": receipt[
            "request_id"
        ],
        "source_model": receipt[
            "source_model_file"
        ],
        "normalized_model": receipt[
            "normalized_model_file"
        ],
        "source_height_mm": receipt[
            "source_height_mm"
        ],
        "target_height_mm": receipt[
            "target_height_mm"
        ],
        "scale_factor": receipt[
            "scale_factor"
        ],
        "extents_before_mm": receipt[
            "extents_before_mm"
        ],
        "extents_after_mm": receipt[
            "extents_after_mm"
        ],
        "normalized_model_sha256": (
            receipt[
                "normalized_model_sha256"
            ]
        ),
        "normalized_size_bytes": (
            receipt[
                "normalized_size_bytes"
            ]
        ),
        "task_directory": str(
            task_path
        ),
        "normalization_receipt_file": str(
            task_path
            / NORMALIZATION_RECEIPT_FILENAME
        ),
        "reused_existing_normalization": (
            reused_existing_normalization
        ),
        "manifest_changed": (
            manifest_changed
        ),
    }


def normalize_m2_model(
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

    artifact_receipt_path = (
        task_path
        / ARTIFACT_RECEIPT_FILENAME
    )

    raw_validation_path = (
        task_path
        / RAW_VALIDATION_FILENAME
    )

    normalized_model_path = (
        task_path
        / NORMALIZED_MODEL_FILENAME
    )

    normalization_receipt_path = (
        task_path
        / NORMALIZATION_RECEIPT_FILENAME
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

    artifact_receipt = (
        _read_json_object(
            artifact_receipt_path,
            label="Artifact Receipt",
        )
    )

    raw_validation = (
        _read_json_object(
            raw_validation_path,
            label="Raw Mesh Validation",
        )
    )

    ensure_valid_m2_manifest(
        manifest
    )

    ensure_valid_provider_request(
        provider_request
    )

    ensure_valid_artifact_receipt(
        artifact_receipt
    )

    ensure_valid_mesh_report(
        raw_validation
    )

    request_ids = {
        manifest.get("request_id"),
        provider_request.get(
            "request_id"
        ),
        artifact_receipt.get(
            "request_id"
        ),
        raw_validation.get(
            "request_id"
        ),
    }

    if len(request_ids) != 1:
        raise M2ProviderError(
            "M2_NORMALIZATION_REQUEST_ID_MISMATCH",
            "归一化输入文件的request_id不一致",
            details={
                "request_ids": [
                    str(value)
                    for value in request_ids
                ],
            },
        )

    if manifest.get("status") != "generated":
        raise M2ProviderError(
            "M2_NORMALIZATION_STATUS_INVALID",
            "只有generated任务可以进行尺寸归一化",
            details={
                "status": manifest.get(
                    "status"
                ),
            },
        )

    if not raw_validation.get(
        "hard_constraints_passed"
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_RAW_MESH_INVALID",
            "原始模型未通过基础Mesh硬约束，不能进行尺寸归一化",
            details={
                "errors": raw_validation.get(
                    "errors",
                    [],
                ),
            },
        )

    source_filename = (
        artifact_receipt.get(
            "local_file"
        )
    )

    if (
        not isinstance(
            source_filename,
            str,
        )
        or not source_filename.strip()
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_SOURCE_INVALID",
            "Artifact Receipt没有有效原始模型文件名",
        )

    source_model_path = (
        task_path
        / source_filename
    )

    if not source_model_path.is_file():
        raise M2ProviderError(
            "M2_NORMALIZATION_SOURCE_MISSING",
            "原始模型文件不存在",
            details={
                "path": str(
                    source_model_path
                ),
            },
        )

    source_sha256 = _sha256_file(
        source_model_path
    )

    if (
        source_sha256
        != artifact_receipt.get(
            "sha256"
        )
        or source_sha256
        != raw_validation.get(
            "model_sha256"
        )
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_SOURCE_CHANGED",
            "原始模型与Artifact或Mesh验证记录不一致",
            details={
                "actual_sha256": (
                    source_sha256
                ),
                "artifact_sha256": (
                    artifact_receipt.get(
                        "sha256"
                    )
                ),
                "validation_sha256": (
                    raw_validation.get(
                        "model_sha256"
                    )
                ),
            },
        )

    target_height_value = (
        provider_request.get(
            "target_height_mm"
        )
    )

    if (
        isinstance(
            target_height_value,
            bool,
        )
        or not isinstance(
            target_height_value,
            (
                int,
                float,
            ),
        )
        or not math.isfinite(
            float(target_height_value)
        )
        or float(
            target_height_value
        ) <= 0
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_TARGET_HEIGHT_INVALID",
            "Provider Request没有有效目标高度",
            details={
                "target_height_mm": (
                    target_height_value
                ),
            },
        )

    target_height_mm = float(
        target_height_value
    )

    normalized_exists = (
        normalized_model_path.exists()
    )

    receipt_exists = (
        normalization_receipt_path.exists()
    )

    if normalized_exists != receipt_exists:
        raise M2ProviderError(
            "M2_NORMALIZATION_STATE_INCOMPLETE",
            "归一化模型和Receipt状态不完整，已停止以避免覆盖",
            details={
                "normalized_model_exists": (
                    normalized_exists
                ),
                "normalization_receipt_exists": (
                    receipt_exists
                ),
            },
        )

    if normalized_exists and receipt_exists:
        receipt = _read_json_object(
            normalization_receipt_path,
            label=(
                "Normalization Receipt"
            ),
        )

        ensure_valid_normalization_receipt(
            receipt
        )

        actual_normalized_sha256 = (
            _sha256_file(
                normalized_model_path
            )
        )

        actual_normalized_size = (
            normalized_model_path.stat().st_size
        )

        if (
            receipt.get("request_id")
            != manifest.get(
                "request_id"
            )
            or receipt.get(
                "source_model_file"
            )
            != source_filename
            or receipt.get(
                "source_model_sha256"
            )
            != source_sha256
            or receipt.get(
                "normalized_model_file"
            )
            != NORMALIZED_MODEL_FILENAME
            or receipt.get(
                "normalized_model_sha256"
            )
            != actual_normalized_sha256
            or receipt.get(
                "normalized_size_bytes"
            )
            != actual_normalized_size
            or not math.isclose(
                float(
                    receipt.get(
                        "target_height_mm"
                    )
                ),
                target_height_mm,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        ):
            raise M2ProviderError(
                "M2_NORMALIZATION_RECEIPT_CONFLICT",
                "已有归一化结果与当前任务不一致",
            )

        updated_manifest = (
            _updated_manifest(
                manifest
            )
        )

        manifest_changed = (
            updated_manifest != manifest
        )

        if manifest_changed:
            _write_json_atomic(
                manifest_path,
                updated_manifest,
            )

        return _result_data(
            task_path=task_path,
            receipt=receipt,
            reused_existing_normalization=True,
            manifest_changed=(
                manifest_changed
            ),
        )

    mesh = _load_combined_mesh(
        source_model_path
    )

    extents_before = _extents_dict(
        mesh
    )

    source_height_mm = (
        extents_before["z"]
    )

    scale_factor = (
        target_height_mm
        / source_height_mm
    )

    if (
        not math.isfinite(scale_factor)
        or scale_factor <= 0
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_SCALE_INVALID",
            "计算得到的缩放系数无效",
            details={
                "source_height_mm": (
                    source_height_mm
                ),
                "target_height_mm": (
                    target_height_mm
                ),
                "scale_factor": (
                    scale_factor
                ),
            },
        )

    normalized_mesh = mesh.copy()

    normalized_mesh.apply_scale(
        scale_factor
    )

    bounds = normalized_mesh.bounds

    if (
        bounds is None
        or len(bounds) != 2
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_BOUNDS_INVALID",
            "缩放后无法获得模型边界",
        )

    minimum_z = float(
        bounds[0][2]
    )

    if not math.isfinite(
        minimum_z
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_BOUNDS_INVALID",
            "缩放后的模型边界无效",
        )

    # 将模型底面移动到z=0。
    normalized_mesh.apply_translation(
        (
            0.0,
            0.0,
            -minimum_z,
        )
    )

    extents_after = _extents_dict(
        normalized_mesh
    )

    if not math.isclose(
        extents_after["z"],
        target_height_mm,
        rel_tol=1e-6,
        abs_tol=1e-5,
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_HEIGHT_MISMATCH",
            "归一化后的高度不符合目标高度",
            details={
                "actual_height_mm": (
                    extents_after["z"]
                ),
                "target_height_mm": (
                    target_height_mm
                ),
            },
        )

    _export_glb_atomic(
        normalized_mesh,
        normalized_model_path,
    )

    normalized_sha256 = (
        _sha256_file(
            normalized_model_path
        )
    )

    normalized_size_bytes = (
        normalized_model_path.stat().st_size
    )

    receipt = {
        "schema_version": "0.1.0",
        "module": "M2",
        "request_id": (
            manifest["request_id"]
        ),
        "source_model_file": (
            source_filename
        ),
        "source_model_sha256": (
            source_sha256
        ),
        "source_height_mm": (
            _round_number(
                source_height_mm
            )
        ),
        "target_height_mm": (
            _round_number(
                target_height_mm
            )
        ),
        "scale_factor": (
            _round_number(
                scale_factor
            )
        ),
        "normalized_model_file": (
            NORMALIZED_MODEL_FILENAME
        ),
        "normalized_model_sha256": (
            normalized_sha256
        ),
        "normalized_size_bytes": (
            normalized_size_bytes
        ),
        "extents_before_mm": (
            extents_before
        ),
        "extents_after_mm": (
            extents_after
        ),
        "base_aligned_z0": True,
        "status": "normalized",
        "library": "trimesh",
        "library_version": str(
            trimesh.__version__
        ),
    }

    ensure_valid_normalization_receipt(
        receipt
    )

    _write_json_atomic(
        normalization_receipt_path,
        receipt,
    )

    updated_manifest = (
        _updated_manifest(
            manifest
        )
    )

    manifest_changed = (
        updated_manifest != manifest
    )

    if manifest_changed:
        _write_json_atomic(
            manifest_path,
            updated_manifest,
        )

    return _result_data(
        task_path=task_path,
        receipt=receipt,
        reused_existing_normalization=False,
        manifest_changed=(
            manifest_changed
        ),
    )