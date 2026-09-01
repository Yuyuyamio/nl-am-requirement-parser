from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path
from typing import Any

import trimesh
from .coordinate_frame import load_print_scene
from jsonschema import Draft202012Validator

from .contracts import (
    M2ProviderError,
    ensure_valid_m2_manifest,
)
from .normalization import (
    ensure_valid_normalization_receipt,
)
from .providers import (
    ensure_valid_provider_request,
)


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]

NORMALIZED_VALIDATION_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_normalized_mesh_validation.schema.json"
)

M2_MANIFEST_FILENAME = "m2_manifest.json"
PROVIDER_REQUEST_FILENAME = "provider_request.json"

NORMALIZATION_RECEIPT_FILENAME = (
    "normalization_receipt.json"
)

NORMALIZED_MODEL_FILENAME = (
    "normalized_model.glb"
)

NORMALIZED_VALIDATION_FILENAME = (
    "normalized_mesh_validation.json"
)


def _read_json_object(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_FILE_MISSING",
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
            "M2_NORMALIZED_VALIDATION_INVALID_JSON",
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
            "M2_NORMALIZED_VALIDATION_READ_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_INVALID_OBJECT",
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
            "M2_NORMALIZED_VALIDATION_WRITE_FAILED",
            "无法写入归一化模型验证报告",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(
            NORMALIZED_VALIDATION_SCHEMA_PATH.read_text(
                encoding="utf-8-sig"
            )
        )
    except OSError as error:
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_SCHEMA_READ_FAILED",
            "无法读取归一化验证Schema",
            details={
                "path": str(
                    NORMALIZED_VALIDATION_SCHEMA_PATH
                ),
                "reason": str(error),
            },
        ) from error
    except json.JSONDecodeError as error:
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_SCHEMA_JSON_INVALID",
            "归一化验证Schema不是合法JSON",
            details={
                "line": error.lineno,
                "column": error.colno,
                "reason": error.msg,
            },
        ) from error

    if not isinstance(schema, dict):
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_SCHEMA_INVALID",
            "归一化验证Schema必须是JSON对象",
        )

    Draft202012Validator.check_schema(
        schema
    )

    return schema


def validate_normalized_mesh_report(
    data: Any,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: document must be a JSON object"
        ]

    validator = Draft202012Validator(
        _load_schema()
    )

    schema_errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    formatted_errors: list[str] = []

    for error in schema_errors:
        path = "$"

        for part in error.absolute_path:
            if isinstance(part, int):
                path += f"[{part}]"
            else:
                path += f".{part}"

        formatted_errors.append(
            f"{path}: {error.message}"
        )

    return formatted_errors


def ensure_valid_normalized_mesh_report(
    data: Any,
) -> None:
    errors = validate_normalized_mesh_report(
        data
    )

    if errors:
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_REPORT_INVALID",
            "归一化模型验证报告不符合Schema",
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
    value: float | None,
) -> float | None:
    if value is None:
        return None

    numeric_value = float(value)

    if not math.isfinite(
        numeric_value
    ):
        return None

    return round(
        numeric_value,
        6,
    )


def _load_combined_mesh(
    model_path: Path,
) -> tuple[
    trimesh.Trimesh,
    int,
]:
    try:
        scene = load_print_scene(
            model_path,
            process=False,
        )
    except Exception as error:
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_LOAD_FAILED",
            "Trimesh无法读取归一化模型",
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

    geometry_count = len(
        geometries
    )

    if geometry_count == 0:
        empty_mesh = trimesh.Trimesh(
            vertices=[],
            faces=[],
            process=False,
        )

        return (
            empty_mesh,
            0,
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
            "M2_NORMALIZED_VALIDATION_COMBINE_FAILED",
            "无法合并归一化模型场景",
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
            "M2_NORMALIZED_VALIDATION_COMBINE_INVALID",
            "合并结果不是Trimesh",
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
    target_height_mm: float,
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
            components = mesh.split(
                only_watertight=False
            )

            connected_components = len(
                components
            )
        except Exception as error:
            raise M2ProviderError(
                "M2_NORMALIZED_VALIDATION_COMPONENT_FAILED",
                "无法分析归一化模型连通体",
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
        volume = _round_number(
            float(mesh.volume)
        )
    else:
        volume = None

    if (
        has_geometry
        and mesh.extents is not None
        and len(mesh.extents) == 3
    ):
        extent_values = [
            _round_number(
                float(value)
            )
            for value in mesh.extents
        ]
    else:
        extent_values = [
            None,
            None,
            None,
        ]

    extents_mm = {
        "x": extent_values[0],
        "y": extent_values[1],
        "z": extent_values[2],
    }

    actual_height_mm = (
        extent_values[2]
    )

    # NORMALIZED_VALIDATION_SCALE_FACTOR_V1
    if (
        actual_height_mm is None
        or actual_height_mm <= 0
    ):
        scale_factor_to_target_height = None
    else:
        scale_factor_to_target_height = (
            _round_number(
                target_height_mm
                / actual_height_mm
            )
        )

    if actual_height_mm is None:
        height_error_mm = None
        height_error_ratio = None
    else:
        height_error_mm = _round_number(
            abs(
                actual_height_mm
                - target_height_mm
            )
        )

        height_error_ratio = _round_number(
            height_error_mm
            / target_height_mm
        )

    height_tolerance_mm = _round_number(
        max(
            0.05,
            target_height_mm * 0.001,
        )
    )

    height_within_tolerance = bool(
        height_error_mm is not None
        and height_error_mm
        <= height_tolerance_mm
    )

    positive_volume = bool(
        volume is not None
        and volume > 0
    )

    nonzero_extents = all(
        value is not None
        and value > 0
        for value in extent_values
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
        "target_height_match": (
            height_within_tolerance
        ),
    }

    hard_constraints_passed = all(
        hard_checks.values()
    )

    warnings: list[str] = []

    if (
        height_error_mm is not None
        and height_error_mm > 0
        and height_within_tolerance
    ):
        warnings.append(
            "minor_height_rounding_within_tolerance"
        )

    errors = [
        check_name
        for check_name, passed
        in hard_checks.items()
        if not passed
    ]

    return {
        "geometry_count": (
            geometry_count
        ),
        "vertex_count": (
            vertex_count
        ),
        "face_count": face_count,
        "connected_components": (
            connected_components
        ),
        "watertight": watertight,
        "winding_consistent": (
            winding_consistent
        ),
        "volume": volume,
        "extents_mm": extents_mm,
        "actual_height_mm": (
            actual_height_mm
        ),
        "scale_factor_to_target_height": (
            scale_factor_to_target_height
        ),
        "height_error_mm": (
            height_error_mm
        ),
        "height_error_ratio": (
            height_error_ratio
        ),
        "height_tolerance_mm": (
            height_tolerance_mm
        ),
        "height_within_tolerance": (
            height_within_tolerance
        ),
        "hard_checks": hard_checks,
        "hard_constraints_passed": (
            hard_constraints_passed
        ),
        "warnings": warnings,
        "errors": errors,
    }


def _updated_manifest(
    manifest: dict[str, Any],
    *,
    hard_constraints_passed: bool,
) -> dict[str, Any]:
    updated = dict(manifest)

    updated["status"] = "generated"
    updated["primary_model"] = (
        NORMALIZED_MODEL_FILENAME
    )
    updated["validation_file"] = (
        NORMALIZED_VALIDATION_FILENAME
    )
    updated[
        "hard_constraints_passed"
    ] = hard_constraints_passed
    updated["next_module"] = None

    ensure_valid_m2_manifest(
        updated
    )

    return updated


def _result_data(
    *,
    task_path: Path,
    report: dict[str, Any],
    reused_existing_validation: bool,
    manifest_changed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "validated",
        "request_id": report[
            "request_id"
        ],
        "model_file": report[
            "model_file"
        ],
        "validation_file": str(
            task_path
            / NORMALIZED_VALIDATION_FILENAME
        ),
        "hard_constraints_passed": (
            report[
                "hard_constraints_passed"
            ]
        ),
        "target_height_mm": (
            report[
                "target_height_mm"
            ]
        ),
        "actual_height_mm": (
            report[
                "actual_height_mm"
            ]
        ),
        "height_error_mm": (
            report[
                "height_error_mm"
            ]
        ),
        "height_within_tolerance": (
            report[
                "height_within_tolerance"
            ]
        ),
        "reused_existing_validation": (
            reused_existing_validation
        ),
        "manifest_changed": (
            manifest_changed
        ),
        "report": report,
    }


def validate_normalized_m2_mesh(
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

    normalization_receipt_path = (
        task_path
        / NORMALIZATION_RECEIPT_FILENAME
    )

    model_path = (
        task_path
        / NORMALIZED_MODEL_FILENAME
    )

    validation_path = (
        task_path
        / NORMALIZED_VALIDATION_FILENAME
    )

    manifest = _read_json_object(
        manifest_path,
        label="M2 Manifest",
    )

    provider_request = _read_json_object(
        provider_request_path,
        label="Provider Request",
    )

    normalization_receipt = (
        _read_json_object(
            normalization_receipt_path,
            label="Normalization Receipt",
        )
    )

    ensure_valid_m2_manifest(
        manifest
    )

    ensure_valid_provider_request(
        provider_request
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
    }

    if len(request_ids) != 1:
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_REQUEST_ID_MISMATCH",
            "归一化模型验证输入的request_id不一致",
            details={
                "request_ids": [
                    str(value)
                    for value in request_ids
                ],
            },
        )

    if manifest.get("status") != "generated":
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_STATUS_INVALID",
            "只有generated任务可以验证归一化模型",
            details={
                "status": manifest.get(
                    "status"
                ),
            },
        )

    if (
        manifest.get("primary_model")
        != NORMALIZED_MODEL_FILENAME
    ):
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_PRIMARY_MODEL_INVALID",
            "Manifest当前主模型不是normalized_model.glb",
            details={
                "primary_model": (
                    manifest.get(
                        "primary_model"
                    )
                ),
            },
        )

    if (
        normalization_receipt.get(
            "normalized_model_file"
        )
        != NORMALIZED_MODEL_FILENAME
    ):
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_RECEIPT_MODEL_INVALID",
            "Normalization Receipt未指向归一化模型",
        )

    if not model_path.is_file():
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_MODEL_MISSING",
            "归一化模型文件不存在",
            details={
                "path": str(model_path),
            },
        )

    model_sha256 = _sha256_file(
        model_path
    )

    model_size_bytes = (
        model_path.stat().st_size
    )

    if (
        model_sha256
        != normalization_receipt.get(
            "normalized_model_sha256"
        )
        or model_size_bytes
        != normalization_receipt.get(
            "normalized_size_bytes"
        )
    ):
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_MODEL_CHANGED",
            "归一化模型与Normalization Receipt不一致",
            details={
                "expected_sha256": (
                    normalization_receipt.get(
                        "normalized_model_sha256"
                    )
                ),
                "actual_sha256": (
                    model_sha256
                ),
                "expected_size_bytes": (
                    normalization_receipt.get(
                        "normalized_size_bytes"
                    )
                ),
                "actual_size_bytes": (
                    model_size_bytes
                ),
            },
        )

    request_target = (
        provider_request.get(
            "target_height_mm"
        )
    )

    receipt_target = (
        normalization_receipt.get(
            "target_height_mm"
        )
    )

    if (
        isinstance(
            request_target,
            bool,
        )
        or not isinstance(
            request_target,
            (
                int,
                float,
            ),
        )
        or isinstance(
            receipt_target,
            bool,
        )
        or not isinstance(
            receipt_target,
            (
                int,
                float,
            ),
        )
    ):
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_TARGET_INVALID",
            "目标高度记录无效",
        )

    target_height_mm = float(
        request_target
    )

    if not math.isclose(
        target_height_mm,
        float(receipt_target),
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise M2ProviderError(
            "M2_NORMALIZED_VALIDATION_TARGET_MISMATCH",
            "Provider Request与Normalization Receipt目标高度不一致",
            details={
                "provider_request_target": (
                    target_height_mm
                ),
                "receipt_target": (
                    receipt_target
                ),
            },
        )

    if validation_path.is_file():
        existing_report = _read_json_object(
            validation_path,
            label=(
                "Existing Normalized "
                "Mesh Validation"
            ),
        )

        ensure_valid_normalized_mesh_report(
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
                "model_sha256"
            )
            != model_sha256
            or not math.isclose(
                float(
                    existing_report.get(
                        "target_height_mm"
                    )
                ),
                target_height_mm,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        ):
            raise M2ProviderError(
                "M2_NORMALIZED_VALIDATION_CONFLICT",
                "已有归一化模型验证报告与当前模型不一致",
            )

        updated_manifest = _updated_manifest(
            manifest,
            hard_constraints_passed=bool(
                existing_report[
                    "hard_constraints_passed"
                ]
            ),
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
            report=existing_report,
            reused_existing_validation=True,
            manifest_changed=(
                manifest_changed
            ),
        )

    mesh, geometry_count = (
        _load_combined_mesh(
            model_path
        )
    )

    inspection = _inspect_mesh(
        mesh=mesh,
        geometry_count=(
            geometry_count
        ),
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
        "model_file": (
            NORMALIZED_MODEL_FILENAME
        ),
        "model_sha256": (
            model_sha256
        ),
        "model_size_bytes": (
            model_size_bytes
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
            inspection[
                "face_count"
            ]
        ),
        "connected_components": (
            inspection[
                "connected_components"
            ]
        ),
        "watertight": (
            inspection[
                "watertight"
            ]
        ),
        "winding_consistent": (
            inspection[
                "winding_consistent"
            ]
        ),
        "volume": (
            inspection["volume"]
        ),
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
        "actual_height_mm": (
            inspection[
                "actual_height_mm"
            ]
        ),
        "height_error_mm": (
            inspection[
                "height_error_mm"
            ]
        ),
        "height_error_ratio": (
            inspection[
                "height_error_ratio"
            ]
        ),
        "height_tolerance_mm": (
            inspection[
                "height_tolerance_mm"
            ]
        ),
        "height_within_tolerance": (
            inspection[
                "height_within_tolerance"
            ]
        ),
        "hard_checks": (
            inspection[
                "hard_checks"
            ]
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

    ensure_valid_normalized_mesh_report(
        report
    )

    _write_json_atomic(
        validation_path,
        report,
    )

    updated_manifest = _updated_manifest(
        manifest,
        hard_constraints_passed=(
            report[
                "hard_constraints_passed"
            ]
        ),
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
        report=report,
        reused_existing_validation=False,
        manifest_changed=(
            manifest_changed
        ),
    )
