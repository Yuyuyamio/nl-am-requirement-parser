from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass
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
from .mesh_repair import (
    MESH_REPAIR_RECEIPT_FILENAME,
    REPAIRED_MODEL_FILENAME,
    ensure_valid_mesh_repair_receipt,
)
from .providers import (
    ensure_valid_provider_request,
)


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]

MESH_VALIDATION_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_mesh_validation.schema.json"
)

NORMALIZATION_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_normalization_receipt.schema.json"
)

M2_MANIFEST_FILENAME = "m2_manifest.json"
PROVIDER_REQUEST_FILENAME = "provider_request.json"
ARTIFACT_RECEIPT_FILENAME = (
    "artifact_receipt.json"
)

RAW_MODEL_FILENAME = "raw_model.glb"
NORMALIZED_MODEL_FILENAME = (
    "normalized_model.glb"
)

RAW_MESH_VALIDATION_FILENAME = (
    "mesh_validation.json"
)
REPAIRED_MESH_VALIDATION_FILENAME = (
    "repaired_mesh_validation.json"
)
NORMALIZED_MESH_VALIDATION_FILENAME = (
    "normalized_mesh_validation.json"
)

NORMALIZATION_RECEIPT_FILENAME = (
    "normalization_receipt.json"
)


@dataclass(frozen=True)
class ModelRecord:
    filename: str
    path: Path
    sha256: str
    size_bytes: int
    stage: str


def _read_json_object(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_PIPELINE_FILE_MISSING",
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
            "M2_PIPELINE_INVALID_JSON",
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
            "M2_PIPELINE_READ_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_PIPELINE_INVALID_OBJECT",
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
            "M2_PIPELINE_WRITE_FAILED",
            "无法写入M2管线记录",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _load_schema(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        schema = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as error:
        raise M2ProviderError(
            "M2_PIPELINE_SCHEMA_LOAD_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(schema, dict):
        raise M2ProviderError(
            "M2_PIPELINE_SCHEMA_INVALID",
            f"{label}必须是JSON对象",
        )

    Draft202012Validator.check_schema(
        schema
    )

    return schema


def _format_schema_errors(
    *,
    data: Any,
    schema: dict[str, Any],
) -> list[str]:
    validator = Draft202012Validator(
        schema
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


def _ensure_valid_mesh_report(
    data: Any,
) -> None:
    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_MESH_REPORT_INVALID",
            "Mesh验证报告必须是JSON对象",
        )

    errors = _format_schema_errors(
        data=data,
        schema=_load_schema(
            MESH_VALIDATION_SCHEMA_PATH,
            label="Mesh Validation Schema",
        ),
    )

    if errors:
        raise M2ProviderError(
            "M2_MESH_REPORT_INVALID",
            "Mesh验证报告不符合Schema",
            details={
                "errors": errors,
            },
        )


def _ensure_valid_normalization_receipt(
    data: Any,
) -> None:
    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_NORMALIZATION_RECEIPT_INVALID",
            "Normalization Receipt必须是JSON对象",
        )

    errors = _format_schema_errors(
        data=data,
        schema=_load_schema(
            NORMALIZATION_SCHEMA_PATH,
            label="Normalization Schema",
        ),
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


def _verify_file_record(
    record: ModelRecord,
) -> None:
    if not record.path.is_file():
        raise M2ProviderError(
            "M2_MODEL_FILE_MISSING",
            "模型文件不存在",
            details={
                "path": str(record.path),
                "stage": record.stage,
            },
        )

    actual_sha256 = _sha256_file(
        record.path
    )
    actual_size = (
        record.path.stat().st_size
    )

    if (
        actual_sha256 != record.sha256
        or actual_size != record.size_bytes
    ):
        raise M2ProviderError(
            "M2_MODEL_RECORD_MISMATCH",
            "模型文件与来源记录不一致",
            details={
                "stage": record.stage,
                "path": str(record.path),
                "expected_sha256": (
                    record.sha256
                ),
                "actual_sha256": (
                    actual_sha256
                ),
                "expected_size_bytes": (
                    record.size_bytes
                ),
                "actual_size_bytes": (
                    actual_size
                ),
            },
        )


def _resolve_model_record(
    *,
    task_path: Path,
    manifest: dict[str, Any],
    artifact_receipt: dict[str, Any],
    filename: str,
    resolving: tuple[str, ...] = (),
) -> ModelRecord:
    if filename in resolving:
        raise M2ProviderError(
            "M2_MODEL_PROVENANCE_CYCLE",
            "模型来源记录形成循环",
            details={
                "chain": [
                    *resolving,
                    filename,
                ],
            },
        )

    if (
        filename
        == artifact_receipt.get(
            "local_file"
        )
    ):
        if (
            artifact_receipt.get(
                "request_id"
            )
            != manifest.get(
                "request_id"
            )
        ):
            raise M2ProviderError(
                "M2_ARTIFACT_REQUEST_ID_MISMATCH",
                "Artifact Receipt与Manifest的request_id不一致",
            )

        record = ModelRecord(
            filename=filename,
            path=task_path / filename,
            sha256=artifact_receipt[
                "sha256"
            ],
            size_bytes=artifact_receipt[
                "size_bytes"
            ],
            stage="artifact",
        )

        _verify_file_record(record)

        return record

    if filename == REPAIRED_MODEL_FILENAME:
        receipt_path = (
            task_path
            / MESH_REPAIR_RECEIPT_FILENAME
        )

        receipt = _read_json_object(
            receipt_path,
            label="Mesh Repair Receipt",
        )

        ensure_valid_mesh_repair_receipt(
            receipt
        )

        if (
            receipt.get("request_id")
            != manifest.get("request_id")
            or receipt.get(
                "repaired_model_file"
            )
            != filename
        ):
            raise M2ProviderError(
                "M2_REPAIR_PROVENANCE_CONFLICT",
                "Mesh Repair Receipt与当前任务不一致",
            )

        source_filename = receipt.get(
            "source_model_file"
        )

        if not isinstance(
            source_filename,
            str,
        ):
            raise M2ProviderError(
                "M2_REPAIR_PROVENANCE_INVALID",
                "Mesh Repair Receipt缺少源模型",
            )

        source_record = (
            _resolve_model_record(
                task_path=task_path,
                manifest=manifest,
                artifact_receipt=(
                    artifact_receipt
                ),
                filename=source_filename,
                resolving=(
                    *resolving,
                    filename,
                ),
            )
        )

        if (
            receipt.get(
                "source_model_sha256"
            )
            != source_record.sha256
            or receipt.get(
                "source_model_size_bytes"
            )
            != source_record.size_bytes
        ):
            raise M2ProviderError(
                "M2_REPAIR_SOURCE_RECORD_MISMATCH",
                "Mesh Repair Receipt中的源模型记录不一致",
            )

        record = ModelRecord(
            filename=filename,
            path=task_path / filename,
            sha256=receipt[
                "repaired_model_sha256"
            ],
            size_bytes=receipt[
                "repaired_size_bytes"
            ],
            stage="repair",
        )

        _verify_file_record(record)

        return record

    if filename == NORMALIZED_MODEL_FILENAME:
        receipt_path = (
            task_path
            / NORMALIZATION_RECEIPT_FILENAME
        )

        receipt = _read_json_object(
            receipt_path,
            label=(
                "Normalization Receipt"
            ),
        )

        _ensure_valid_normalization_receipt(
            receipt
        )

        if (
            receipt.get("request_id")
            != manifest.get("request_id")
            or receipt.get(
                "normalized_model_file"
            )
            != filename
        ):
            raise M2ProviderError(
                "M2_NORMALIZATION_PROVENANCE_CONFLICT",
                "Normalization Receipt与当前任务不一致",
            )

        source_filename = receipt.get(
            "source_model_file"
        )

        if not isinstance(
            source_filename,
            str,
        ):
            raise M2ProviderError(
                "M2_NORMALIZATION_PROVENANCE_INVALID",
                "Normalization Receipt缺少源模型",
            )

        source_record = (
            _resolve_model_record(
                task_path=task_path,
                manifest=manifest,
                artifact_receipt=(
                    artifact_receipt
                ),
                filename=source_filename,
                resolving=(
                    *resolving,
                    filename,
                ),
            )
        )

        if (
            receipt.get(
                "source_model_sha256"
            )
            != source_record.sha256
        ):
            raise M2ProviderError(
                "M2_NORMALIZATION_SOURCE_RECORD_MISMATCH",
                "Normalization Receipt中的源模型哈希不一致",
            )

        record = ModelRecord(
            filename=filename,
            path=task_path / filename,
            sha256=receipt[
                "normalized_model_sha256"
            ],
            size_bytes=receipt[
                "normalized_size_bytes"
            ],
            stage="normalization",
        )

        _verify_file_record(record)

        return record

    raise M2ProviderError(
        "M2_MODEL_PROVENANCE_UNSUPPORTED",
        "当前M2管线无法确认该模型的来源",
        details={
            "model_file": filename,
        },
    )


def _validation_filename(
    model_filename: str,
) -> str:
    mapping = {
        RAW_MODEL_FILENAME: (
            RAW_MESH_VALIDATION_FILENAME
        ),
        REPAIRED_MODEL_FILENAME: (
            REPAIRED_MESH_VALIDATION_FILENAME
        ),
        NORMALIZED_MODEL_FILENAME: (
            NORMALIZED_MESH_VALIDATION_FILENAME
        ),
    }

    try:
        return mapping[model_filename]
    except KeyError as error:
        raise M2ProviderError(
            "M2_VALIDATION_MODEL_UNSUPPORTED",
            "当前M2管线不支持验证该模型阶段",
            details={
                "model_file": (
                    model_filename
                ),
            },
        ) from error


def _round_optional(
    value: float | None,
) -> float | None:
    if value is None:
        return None

    if not math.isfinite(value):
        return None

    return round(
        float(value),
        6,
    )


def _load_combined_mesh(
    model_path: Path,
) -> tuple[
    trimesh.Trimesh,
    int,
]:
    try:
        scene = trimesh.load_scene(
            model_path,
            process=False,
        )
    except Exception as error:
        raise M2ProviderError(
            "M2_MESH_LOAD_FAILED",
            "Trimesh无法读取本地GLB",
            details={
                "path": str(model_path),
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
        return (
            trimesh.Trimesh(
                vertices=[],
                faces=[],
                process=False,
            ),
            0,
        )

    try:
        if hasattr(scene, "to_mesh"):
            combined = scene.to_mesh()
        else:
            combined = scene.dump(
                concatenate=True
            )
    except Exception as error:
        raise M2ProviderError(
            "M2_MESH_COMBINE_FAILED",
            "无法合并GLB场景中的Mesh",
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
            "M2_MESH_COMBINE_INVALID",
            "合并后的对象不是Trimesh",
            details={
                "actual_type": (
                    type(combined).__name__
                ),
            },
        )

    return (
        combined,
        geometry_count,
    )


def _inspect_mesh(
    *,
    mesh: trimesh.Trimesh,
    geometry_count: int,
    target_height_mm: float | None,
) -> dict[str, Any]:
    vertex_count = int(
        len(mesh.vertices)
    )
    face_count = int(
        len(mesh.faces)
    )

    has_geometry = bool(
        geometry_count > 0
        and vertex_count > 0
        and face_count > 0
    )

    triangular_faces = bool(
        face_count > 0
        and len(mesh.faces.shape) == 2
        and mesh.faces.shape[1] == 3
    )

    if has_geometry:
        try:
            connected_components = len(
                mesh.split(
                    only_watertight=False
                )
            )
        except Exception as error:
            raise M2ProviderError(
                "M2_MESH_COMPONENT_ANALYSIS_FAILED",
                "无法分析Mesh连通体",
                details={
                    "error_type": (
                        type(error).__name__
                    ),
                    "reason": str(error),
                },
            ) from error
    else:
        connected_components = 0

    watertight = bool(
        has_geometry
        and mesh.is_watertight
    )
    winding_consistent = bool(
        has_geometry
        and mesh.is_winding_consistent
    )

    if has_geometry:
        volume = _round_optional(
            float(mesh.volume)
        )
    else:
        volume = 0.0

    if (
        has_geometry
        and mesh.extents is not None
        and len(mesh.extents) == 3
    ):
        extents = [
            _round_optional(
                float(value)
            )
            for value in mesh.extents
        ]
    else:
        extents = [
            None,
            None,
            None,
        ]

    nonzero_extents = all(
        value is not None
        and value > 0
        for value in extents
    )

    positive_volume = bool(
        volume is not None
        and volume > 0
    )

    hard_checks = {
        "has_geometry": has_geometry,
        "triangular_faces": (
            triangular_faces
        ),
        "single_component": (
            connected_components == 1
        ),
        "watertight": watertight,
        "winding_consistent": (
            winding_consistent
        ),
        "positive_volume": (
            positive_volume
        ),
        "nonzero_extents": (
            nonzero_extents
        ),
    }

    hard_constraints_passed = all(
        hard_checks.values()
    )

    z_extent = extents[2]

    if (
        target_height_mm is not None
        and z_extent is not None
        and z_extent > 0
    ):
        scale_factor = _round_optional(
            target_height_mm
            / z_extent
        )
    else:
        scale_factor = None

    warnings: list[str] = []

    if (
        scale_factor is not None
        and not math.isclose(
            scale_factor,
            1.0,
            rel_tol=0.01,
            abs_tol=0.01,
        )
    ):
        warnings.append(
            "model_height_requires_normalization"
        )

    errors = [
        name
        for name, passed
        in hard_checks.items()
        if not passed
    ]

    return {
        "geometry_count": (
            geometry_count
        ),
        "vertex_count": vertex_count,
        "face_count": face_count,
        "connected_components": (
            connected_components
        ),
        "watertight": watertight,
        "winding_consistent": (
            winding_consistent
        ),
        "volume": volume,
        "extents_mm": {
            "x": extents[0],
            "y": extents[1],
            "z": extents[2],
        },
        "scale_factor_to_target_height": (
            scale_factor
        ),
        "hard_checks": hard_checks,
        "hard_constraints_passed": (
            hard_constraints_passed
        ),
        "warnings": warnings,
        "errors": errors,
    }


def _target_height(
    provider_request: dict[str, Any],
) -> float | None:
    value = provider_request.get(
        "target_height_mm"
    )

    if isinstance(value, bool):
        return None

    if isinstance(
        value,
        (
            int,
            float,
        ),
    ):
        number = float(value)

        if (
            math.isfinite(number)
            and number > 0
        ):
            return number

    return None


def _update_manifest_after_validation(
    manifest: dict[str, Any],
    *,
    validation_filename: str,
    hard_constraints_passed: bool,
) -> dict[str, Any]:
    updated = dict(manifest)

    updated["status"] = "generated"
    updated["validation_file"] = (
        validation_filename
    )
    updated[
        "hard_constraints_passed"
    ] = hard_constraints_passed
    updated["next_module"] = None

    ensure_valid_m2_manifest(updated)

    return updated


def validate_m2_mesh_v2(
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

    ensure_valid_m2_manifest(manifest)
    ensure_valid_provider_request(
        provider_request
    )
    ensure_valid_artifact_receipt(
        artifact_receipt
    )

    request_ids = {
        manifest.get("request_id"),
        provider_request.get(
            "request_id"
        ),
        artifact_receipt.get(
            "request_id"
        ),
    }

    if len(request_ids) != 1:
        raise M2ProviderError(
            "M2_MESH_REQUEST_ID_MISMATCH",
            "Mesh验证输入文件的request_id不一致",
        )

    if manifest.get("status") != "generated":
        raise M2ProviderError(
            "M2_MESH_STATUS_INVALID",
            "只有generated任务可以执行Mesh验证",
            details={
                "status": manifest.get(
                    "status"
                ),
            },
        )

    model_filename = manifest.get(
        "primary_model"
    )

    if (
        not isinstance(
            model_filename,
            str,
        )
        or not model_filename.strip()
    ):
        raise M2ProviderError(
            "M2_MESH_PRIMARY_MODEL_MISSING",
            "Manifest没有可验证的primary_model",
        )

    record = _resolve_model_record(
        task_path=task_path,
        manifest=manifest,
        artifact_receipt=(
            artifact_receipt
        ),
        filename=model_filename,
    )

    validation_filename = (
        _validation_filename(
            model_filename
        )
    )
    validation_path = (
        task_path
        / validation_filename
    )

    if validation_path.is_file():
        existing_report = (
            _read_json_object(
                validation_path,
                label=(
                    "Existing Mesh Validation"
                ),
            )
        )

        _ensure_valid_mesh_report(
            existing_report
        )

        if (
            existing_report.get(
                "request_id"
            )
            != manifest.get(
                "request_id"
            )
            or existing_report.get(
                "model_file"
            )
            != model_filename
            or existing_report.get(
                "model_sha256"
            )
            != record.sha256
            or existing_report.get(
                "model_size_bytes"
            )
            != record.size_bytes
        ):
            raise M2ProviderError(
                "M2_MESH_VALIDATION_CONFLICT",
                "已有Mesh验证报告与当前模型不一致",
            )

        updated_manifest = (
            _update_manifest_after_validation(
                manifest,
                validation_filename=(
                    validation_filename
                ),
                hard_constraints_passed=bool(
                    existing_report.get(
                        "hard_constraints_passed"
                    )
                ),
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

        return {
            "schema_version": "0.1.0",
            "module": "M2",
            "status": "validated",
            "request_id": (
                existing_report[
                    "request_id"
                ]
            ),
            "validation_file": str(
                validation_path
            ),
            "hard_constraints_passed": (
                existing_report[
                    "hard_constraints_passed"
                ]
            ),
            "reused_existing_validation": (
                True
            ),
            "manifest_changed": (
                manifest_changed
            ),
            "report": existing_report,
        }

    mesh, geometry_count = (
        _load_combined_mesh(
            record.path
        )
    )

    target_height_mm = _target_height(
        provider_request
    )

    inspection = _inspect_mesh(
        mesh=mesh,
        geometry_count=geometry_count,
        target_height_mm=(
            target_height_mm
        ),
    )

    report = {
        "schema_version": "0.1.0",
        "module": "M2",
        "request_id": (
            manifest["request_id"]
        ),
        "model_file": model_filename,
        "model_sha256": record.sha256,
        "model_size_bytes": (
            record.size_bytes
        ),
        "format": "glb",
        "library": "trimesh",
        "library_version": str(
            trimesh.__version__
        ),
        "geometry_count": (
            inspection[
                "geometry_count"
            ]
        ),
        "vertex_count": (
            inspection[
                "vertex_count"
            ]
        ),
        "face_count": (
            inspection["face_count"]
        ),
        "connected_components": (
            inspection[
                "connected_components"
            ]
        ),
        "watertight": (
            inspection["watertight"]
        ),
        "winding_consistent": (
            inspection[
                "winding_consistent"
            ]
        ),
        "volume": inspection["volume"],
        "extents_mm": (
            inspection["extents_mm"]
        ),
        "target_height_mm": (
            target_height_mm
        ),
        "scale_factor_to_target_height": (
            inspection[
                "scale_factor_to_target_height"
            ]
        ),
        "hard_checks": (
            inspection["hard_checks"]
        ),
        "hard_constraints_passed": (
            inspection[
                "hard_constraints_passed"
            ]
        ),
        "warnings": (
            inspection["warnings"]
        ),
        "errors": (
            inspection["errors"]
        ),
    }

    _ensure_valid_mesh_report(report)

    _write_json_atomic(
        validation_path,
        report,
    )

    updated_manifest = (
        _update_manifest_after_validation(
            manifest,
            validation_filename=(
                validation_filename
            ),
            hard_constraints_passed=(
                report[
                    "hard_constraints_passed"
                ]
            ),
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

    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "validated",
        "request_id": (
            report["request_id"]
        ),
        "validation_file": str(
            validation_path
        ),
        "hard_constraints_passed": (
            report[
                "hard_constraints_passed"
            ]
        ),
        "reused_existing_validation": (
            False
        ),
        "manifest_changed": (
            manifest_changed
        ),
        "report": report,
    }


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
        "x": round(values[0], 6),
        "y": round(values[1], 6),
        "z": round(values[2], 6),
    }


def _export_glb_atomic(
    mesh: trimesh.Trimesh,
    destination_path: Path,
) -> None:
    temporary_path = (
        destination_path.with_name(
            (
                f".{destination_path.name}."
                f"tmp-{uuid.uuid4().hex}"
            )
        )
    )

    try:
        data = (
            trimesh.exchange.gltf.export_glb(
                trimesh.Scene(mesh)
            )
        )

        if not isinstance(data, bytes):
            raise M2ProviderError(
                "M2_NORMALIZATION_EXPORT_INVALID",
                "Trimesh没有返回GLB二进制数据",
                details={
                    "actual_type": (
                        type(data).__name__
                    ),
                },
            )

        temporary_path.write_bytes(data)
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


def _update_manifest_after_normalization(
    manifest: dict[str, Any],
    *,
    preserve_normalized_validation: bool,
) -> dict[str, Any]:
    updated = dict(manifest)

    updated["status"] = "generated"
    updated["primary_model"] = (
        NORMALIZED_MODEL_FILENAME
    )

    if not preserve_normalized_validation:
        updated["validation_file"] = None
        updated[
            "hard_constraints_passed"
        ] = None

    updated["next_module"] = None

    ensure_valid_m2_manifest(updated)

    return updated


def _normalization_result(
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
        "request_id": (
            receipt["request_id"]
        ),
        "source_model": (
            receipt[
                "source_model_file"
            ]
        ),
        "normalized_model": (
            receipt[
                "normalized_model_file"
            ]
        ),
        "source_height_mm": (
            receipt["source_height_mm"]
        ),
        "target_height_mm": (
            receipt["target_height_mm"]
        ),
        "scale_factor": (
            receipt["scale_factor"]
        ),
        "extents_before_mm": (
            receipt["extents_before_mm"]
        ),
        "extents_after_mm": (
            receipt["extents_after_mm"]
        ),
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
        "task_directory": str(task_path),
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


def normalize_m2_model_v2(
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

    ensure_valid_m2_manifest(manifest)
    ensure_valid_provider_request(
        provider_request
    )
    ensure_valid_artifact_receipt(
        artifact_receipt
    )

    request_ids = {
        manifest.get("request_id"),
        provider_request.get(
            "request_id"
        ),
        artifact_receipt.get(
            "request_id"
        ),
    }

    if len(request_ids) != 1:
        raise M2ProviderError(
            "M2_NORMALIZATION_REQUEST_ID_MISMATCH",
            "归一化输入文件的request_id不一致",
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

    target_height_mm = _target_height(
        provider_request
    )

    if target_height_mm is None:
        raise M2ProviderError(
            "M2_NORMALIZATION_TARGET_HEIGHT_INVALID",
            "Provider Request没有有效目标高度",
            details={
                "target_height_mm": (
                    provider_request.get(
                        "target_height_mm"
                    )
                ),
            },
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

        _ensure_valid_normalization_receipt(
            receipt
        )

        if (
            receipt.get("request_id")
            != manifest.get("request_id")
            or receipt.get(
                "normalized_model_file"
            )
            != NORMALIZED_MODEL_FILENAME
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

        source_filename = receipt.get(
            "source_model_file"
        )

        if not isinstance(
            source_filename,
            str,
        ):
            raise M2ProviderError(
                "M2_NORMALIZATION_SOURCE_INVALID",
                "Normalization Receipt缺少源模型",
            )

        source_record = (
            _resolve_model_record(
                task_path=task_path,
                manifest=manifest,
                artifact_receipt=(
                    artifact_receipt
                ),
                filename=source_filename,
            )
        )

        if (
            receipt.get(
                "source_model_sha256"
            )
            != source_record.sha256
        ):
            raise M2ProviderError(
                "M2_NORMALIZATION_SOURCE_CHANGED",
                "Normalization Receipt中的源模型哈希不一致",
            )

        normalized_record = (
            _resolve_model_record(
                task_path=task_path,
                manifest=manifest,
                artifact_receipt=(
                    artifact_receipt
                ),
                filename=(
                    NORMALIZED_MODEL_FILENAME
                ),
            )
        )

        if (
            receipt.get(
                "normalized_model_sha256"
            )
            != normalized_record.sha256
            or receipt.get(
                "normalized_size_bytes"
            )
            != normalized_record.size_bytes
        ):
            raise M2ProviderError(
                "M2_NORMALIZATION_RECEIPT_CONFLICT",
                "归一化模型与Receipt不一致",
            )

        preserve_validation = bool(
            manifest.get(
                "primary_model"
            )
            == NORMALIZED_MODEL_FILENAME
            and manifest.get(
                "validation_file"
            )
            == NORMALIZED_MESH_VALIDATION_FILENAME
        )

        updated_manifest = (
            _update_manifest_after_normalization(
                manifest,
                preserve_normalized_validation=(
                    preserve_validation
                ),
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

        return _normalization_result(
            task_path=task_path,
            receipt=receipt,
            reused_existing_normalization=True,
            manifest_changed=(
                manifest_changed
            ),
        )

    source_filename = manifest.get(
        "primary_model"
    )
    source_validation_filename = (
        manifest.get(
            "validation_file"
        )
    )

    if (
        source_filename
        not in {
            RAW_MODEL_FILENAME,
            REPAIRED_MODEL_FILENAME,
        }
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_SOURCE_UNSUPPORTED",
            "归一化只接受已验证的原始或修复模型",
            details={
                "primary_model": (
                    source_filename
                ),
            },
        )

    expected_validation_filename = (
        _validation_filename(
            source_filename
        )
    )

    if (
        source_validation_filename
        != expected_validation_filename
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_VALIDATION_REFERENCE_INVALID",
            "Manifest没有指向当前主模型的验证报告",
            details={
                "primary_model": (
                    source_filename
                ),
                "validation_file": (
                    source_validation_filename
                ),
                "expected_validation_file": (
                    expected_validation_filename
                ),
            },
        )

    source_validation_path = (
        task_path
        / source_validation_filename
    )

    source_validation = (
        _read_json_object(
            source_validation_path,
            label=(
                "Source Mesh Validation"
            ),
        )
    )

    _ensure_valid_mesh_report(
        source_validation
    )

    source_record = (
        _resolve_model_record(
            task_path=task_path,
            manifest=manifest,
            artifact_receipt=(
                artifact_receipt
            ),
            filename=source_filename,
        )
    )

    if (
        source_validation.get(
            "request_id"
        )
        != manifest.get(
            "request_id"
        )
        or source_validation.get(
            "model_file"
        )
        != source_filename
        or source_validation.get(
            "model_sha256"
        )
        != source_record.sha256
        or source_validation.get(
            "model_size_bytes"
        )
        != source_record.size_bytes
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_VALIDATION_CONFLICT",
            "源Mesh验证报告与当前主模型不一致",
        )

    if not source_validation.get(
        "hard_constraints_passed"
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_SOURCE_MESH_INVALID",
            "源模型未通过Mesh硬约束，不能进行尺寸归一化",
            details={
                "errors": (
                    source_validation.get(
                        "errors",
                        [],
                    )
                ),
            },
        )

    source_mesh, geometry_count = (
        _load_combined_mesh(
            source_record.path
        )
    )

    if (
        geometry_count <= 0
        or len(source_mesh.vertices) <= 0
        or len(source_mesh.faces) <= 0
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_NO_GEOMETRY",
            "源模型不包含可缩放几何体",
        )

    extents_before = _extents_dict(
        source_mesh
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
        )

    normalized_mesh = (
        source_mesh.copy()
    )
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

    if not math.isfinite(minimum_z):
        raise M2ProviderError(
            "M2_NORMALIZATION_BOUNDS_INVALID",
            "缩放后的模型边界无效",
        )

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

    normalized_bounds = (
        normalized_mesh.bounds
    )

    if (
        normalized_bounds is None
        or not math.isclose(
            float(
                normalized_bounds[0][2]
            ),
            0.0,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
    ):
        raise M2ProviderError(
            "M2_NORMALIZATION_BASE_ALIGNMENT_FAILED",
            "归一化模型底面没有对齐z=0",
        )

    source_sha256_before = (
        source_record.sha256
    )

    _export_glb_atomic(
        normalized_mesh,
        normalized_model_path,
    )

    if (
        _sha256_file(
            source_record.path
        )
        != source_sha256_before
    ):
        normalized_model_path.unlink(
            missing_ok=True
        )
        raise M2ProviderError(
            "M2_NORMALIZATION_SOURCE_CHANGED",
            "源模型在归一化期间发生变化",
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
            source_record.sha256
        ),
        "source_height_mm": round(
            source_height_mm,
            6,
        ),
        "target_height_mm": round(
            target_height_mm,
            6,
        ),
        "scale_factor": round(
            scale_factor,
            6,
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

    _ensure_valid_normalization_receipt(
        receipt
    )

    _write_json_atomic(
        normalization_receipt_path,
        receipt,
    )

    updated_manifest = (
        _update_manifest_after_normalization(
            manifest,
            preserve_normalized_validation=False,
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

    return _normalization_result(
        task_path=task_path,
        receipt=receipt,
        reused_existing_normalization=False,
        manifest_changed=(
            manifest_changed
        ),
    )
