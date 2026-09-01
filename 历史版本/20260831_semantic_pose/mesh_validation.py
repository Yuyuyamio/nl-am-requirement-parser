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

M2_MANIFEST_FILENAME = "m2_manifest.json"
PROVIDER_REQUEST_FILENAME = "provider_request.json"
ARTIFACT_RECEIPT_FILENAME = "artifact_receipt.json"
RAW_MESH_VALIDATION_FILENAME = "mesh_validation.json"
REPAIRED_MESH_VALIDATION_FILENAME = (
    "repaired_mesh_validation.json"
)

# Backward-compatible alias for older imports.
MESH_VALIDATION_FILENAME = RAW_MESH_VALIDATION_FILENAME


def _read_json_object(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_MESH_FILE_MISSING",
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
            "M2_MESH_INVALID_JSON",
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
            "M2_MESH_READ_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_MESH_INVALID_OBJECT",
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
            "M2_MESH_WRITE_FAILED",
            "无法写入Mesh验证结果",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _load_validation_schema() -> dict[str, Any]:
    schema = json.loads(
        MESH_VALIDATION_SCHEMA_PATH.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(schema, dict):
        raise M2ProviderError(
            "M2_MESH_SCHEMA_INVALID",
            "Mesh Validation Schema必须是JSON对象",
        )

    Draft202012Validator.check_schema(
        schema
    )

    return schema


def validate_mesh_report(
    data: Any,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: document must be a JSON object"
        ]

    validator = Draft202012Validator(
        _load_validation_schema()
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


def ensure_valid_mesh_report(
    data: Any,
) -> None:
    errors = validate_mesh_report(
        data
    )

    if errors:
        raise M2ProviderError(
            "M2_MESH_REPORT_INVALID",
            "Mesh验证报告不符合Schema",
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

    mesh_geometries = [
        geometry
        for geometry in scene.geometry.values()
        if isinstance(
            geometry,
            trimesh.Trimesh,
        )
    ]

    geometry_count = len(
        mesh_geometries
    )

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

    has_geometry = (
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
        raw_volume = float(
            mesh.volume
        )
    else:
        raw_volume = 0.0

    volume = _round_number(
        raw_volume
    )

    raw_extents: list[
        float | None
    ]

    if (
        has_geometry
        and mesh.extents is not None
        and len(mesh.extents) == 3
    ):
        raw_extents = [
            _round_number(
                float(value)
            )
            for value in mesh.extents
        ]
    else:
        raw_extents = [
            None,
            None,
            None,
        ]

    x_extent = raw_extents[0]
    y_extent = raw_extents[1]
    z_extent = raw_extents[2]

    single_component = (
        connected_components == 1
    )

    positive_volume = bool(
        volume is not None
        and volume > 0
    )

    nonzero_extents = all(
        value is not None
        and value > 0
        for value in raw_extents
    )

    hard_checks = {
        "has_geometry": has_geometry,
        "triangular_faces": (
            triangular_faces
        ),
        "single_component": (
            single_component
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

    scale_factor: float | None = None

    if (
        target_height_mm is not None
        and z_extent is not None
        and z_extent > 0
    ):
        scale_factor = _round_number(
            target_height_mm
            / z_extent
        )

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
        "extents_mm": {
            "x": x_extent,
            "y": y_extent,
            "z": z_extent,
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


def _validation_filename_for_model(
    model_filename: str,
) -> str:
    if model_filename == "raw_model.glb":
        return RAW_MESH_VALIDATION_FILENAME

    if model_filename == REPAIRED_MODEL_FILENAME:
        return REPAIRED_MESH_VALIDATION_FILENAME

    raise M2ProviderError(
        "M2_MESH_MODEL_PROVENANCE_UNSUPPORTED",
        "当前Mesh验证尚不支持该主模型来源",
        details={"model_file": model_filename},
    )


def _expected_model_record(
    *,
    task_path: Path,
    manifest: dict[str, Any],
    artifact_receipt: dict[str, Any],
    model_filename: str,
) -> tuple[str, int]:
    if model_filename == artifact_receipt.get("local_file"):
        return (
            artifact_receipt["sha256"],
            artifact_receipt["size_bytes"],
        )

    if model_filename == REPAIRED_MODEL_FILENAME:
        repair_receipt = _read_json_object(
            task_path / MESH_REPAIR_RECEIPT_FILENAME,
            label="Mesh Repair Receipt",
        )
        ensure_valid_mesh_repair_receipt(repair_receipt)

        if (
            repair_receipt.get("request_id")
            != manifest.get("request_id")
            or repair_receipt.get("source_model_file")
            != artifact_receipt.get("local_file")
            or repair_receipt.get("source_model_sha256")
            != artifact_receipt.get("sha256")
            or repair_receipt.get("repaired_model_file")
            != model_filename
        ):
            raise M2ProviderError(
                "M2_MESH_REPAIR_PROVENANCE_CONFLICT",
                "修复模型来源记录与当前任务不一致",
            )

        return (
            repair_receipt["repaired_model_sha256"],
            repair_receipt["repaired_size_bytes"],
        )

    raise M2ProviderError(
        "M2_MESH_MODEL_PROVENANCE_UNSUPPORTED",
        "当前Mesh验证无法确认主模型来源",
        details={"model_file": model_filename},
    )


def _updated_manifest(
    manifest: dict[str, Any],
    *,
    hard_constraints_passed: bool,
    validation_filename: str,
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

    ensure_valid_m2_manifest(
        updated
    )

    return updated


def _validate_m2_mesh_impl(
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

    receipt_path = (
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

    receipt = _read_json_object(
        receipt_path,
        label="Artifact Receipt",
    )

    ensure_valid_m2_manifest(
        manifest
    )

    ensure_valid_provider_request(
        provider_request
    )

    ensure_valid_artifact_receipt(
        receipt
    )

    request_ids = {
        manifest.get("request_id"),
        provider_request.get(
            "request_id"
        ),
        receipt.get("request_id"),
    }

    if len(request_ids) != 1:
        raise M2ProviderError(
            "M2_MESH_REQUEST_ID_MISMATCH",
            "Mesh验证输入文件的request_id不一致",
            details={
                "request_ids": [
                    str(value)
                    for value in request_ids
                ],
            },
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

    model_path = (
        task_path
        / model_filename
    )

    if not model_path.is_file():
        raise M2ProviderError(
            "M2_MESH_MODEL_FILE_MISSING",
            "本地模型文件不存在",
            details={"path": str(model_path)},
        )

    expected_sha256, expected_size = (
        _expected_model_record(
            task_path=task_path,
            manifest=manifest,
            artifact_receipt=receipt,
            model_filename=model_filename,
        )
    )

    model_sha256 = _sha256_file(model_path)
    model_size_bytes = model_path.stat().st_size

    if (
        model_sha256 != expected_sha256
        or model_size_bytes != expected_size
    ):
        raise M2ProviderError(
            (
                "M2_MESH_ARTIFACT_HASH_MISMATCH"
                if model_filename == receipt.get("local_file")
                else "M2_MESH_MODEL_RECORD_MISMATCH"
            ),
            "主模型与对应来源记录不一致",
            details={
                "expected_sha256": expected_sha256,
                "actual_sha256": model_sha256,
                "expected_size_bytes": expected_size,
                "actual_size_bytes": model_size_bytes,
            },
        )

    validation_filename = (
        _validation_filename_for_model(model_filename)
    )
    validation_path = (
        task_path / validation_filename
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

        ensure_valid_mesh_report(
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
        ):
            raise M2ProviderError(
                "M2_MESH_VALIDATION_CONFLICT",
                "已有Mesh验证报告与当前模型不一致",
            )

        updated_manifest = (
            _updated_manifest(
                manifest,
                hard_constraints_passed=bool(
                    existing_report.get(
                        "hard_constraints_passed"
                    )
                ),
                validation_filename=(
                    validation_filename
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
            model_path
        )
    )

    target_height_value = (
        provider_request.get(
            "target_height_mm"
        )
    )

    if isinstance(
        target_height_value,
        bool,
    ):
        target_height_mm = None
    elif isinstance(
        target_height_value,
        (
            int,
            float,
        ),
    ):
        target_height_mm = float(
            target_height_value
        )
    else:
        target_height_mm = None

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
        "model_file": model_filename,
        "model_sha256": model_sha256,
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
            inspection["watertight"]
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

    ensure_valid_mesh_report(
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
        validation_filename=(
            validation_filename
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

    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "validated",
        "request_id": report[
            "request_id"
        ],
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

def inspect_mesh_model(
    model_path: str | Path,
    *,
    target_height_mm: float | None,
) -> dict[str, Any]:
    """
    检查任意本地Mesh模型。

    供原始模型验证、归一化模型验证和后续修复验证复用。
    """

    resolved_path = Path(
        model_path
    ).expanduser().resolve()

    if not resolved_path.is_file():
        raise M2ProviderError(
            "M2_MESH_MODEL_FILE_MISSING",
            "待检查模型文件不存在",
            details={
                "path": str(resolved_path),
            },
        )

    mesh, geometry_count = _load_combined_mesh(
        resolved_path
    )

    inspection = _inspect_mesh(
        mesh=mesh,
        geometry_count=geometry_count,
        target_height_mm=target_height_mm,
    )

    base_z_mm: float | None = None

    if (
        len(mesh.vertices) > 0
        and mesh.bounds is not None
        and len(mesh.bounds) == 2
    ):
        base_z_mm = _round_number(
            float(mesh.bounds[0][2])
        )

    result = dict(inspection)
    result["base_z_mm"] = base_z_mm

    return result

# Gate 8C: use the provenance-aware validation implementation.
from .model_pipeline_v2 import (
    validate_m2_mesh_v2 as validate_m2_mesh,
)

# GATE8B_MESH_VALIDATION_ERROR_COMPAT_V2
def validate_m2_mesh(
    task_directory: str | Path,
) -> dict[str, Any]:
    # NORMALIZED_MODEL_ROUTING_V1
    #
    # normalized_model.glb has stronger validation semantics
    # than a raw/repaired mesh:
    # - exact target-height verification
    # - normalization provenance
    # - normalized validation schema
    #
    # Route by manifest provenance instead of pretending that
    # normalized_model.glb is a normal raw mesh.
    task_path = Path(
        task_directory
    ).expanduser().resolve()

    try:
        manifest_probe = json.loads(
            (
                task_path
                / "m2_manifest.json"
            ).read_text(
                encoding="utf-8-sig"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        manifest_probe = None

    if (
        isinstance(
            manifest_probe,
            dict,
        )
        and manifest_probe.get(
            "primary_model"
        )
        == "normalized_model.glb"
    ):
        from .normalized_validation import (
            validate_normalized_m2_mesh,
        )

        return validate_normalized_m2_mesh(
            task_path
        )

    try:
        return _validate_m2_mesh_impl(
            task_directory
        )

    except M2ProviderError as error:
        if error.code not in {
            "M2_MODEL_RECORD_MISMATCH",
            "M2_MESH_MODEL_RECORD_MISMATCH",
        }:
            raise

        task_path = Path(
            task_directory
        ).expanduser().resolve()

        try:
            manifest = json.loads(
                (
                    task_path
                    / "m2_manifest.json"
                ).read_text(
                    encoding="utf-8-sig"
                )
            )

            artifact_receipt = json.loads(
                (
                    task_path
                    / "artifact_receipt.json"
                ).read_text(
                    encoding="utf-8-sig"
                )
            )

            primary_model = manifest.get(
                "primary_model"
            )

            artifact_model = (
                artifact_receipt.get(
                    "local_file"
                )
            )

        except Exception:
            raise error

        if (
            isinstance(
                primary_model,
                str,
            )
            and primary_model
            == artifact_model
        ):
            details = getattr(
                error,
                "details",
                {},
            )

            if not isinstance(
                details,
                dict,
            ):
                details = {}

            mapped_details = dict(
                details
            )

            mapped_details[
                "original_error_code"
            ] = error.code

            mapped_details[
                "model_file"
            ] = primary_model

            raise M2ProviderError(
                "M2_MESH_ARTIFACT_HASH_MISMATCH",
                "本地模型与Artifact Receipt不一致",
                details=mapped_details,
            ) from error

        raise

# GATE8C_NORMALIZED_PROVENANCE_ADAPTER_V1
_gate8b_expected_model_record = _expected_model_record
_gate8b_validation_filename_for_model = _validation_filename_for_model


def _expected_model_record(
    *,
    task_path: Path,
    manifest: dict[str, Any],
    artifact_receipt: dict[str, Any],
    model_filename: str,
) -> tuple[str, int]:
    if model_filename != "normalized_model.glb":
        return _gate8b_expected_model_record(
            task_path=task_path,
            manifest=manifest,
            artifact_receipt=artifact_receipt,
            model_filename=model_filename,
        )

    receipt = _read_json_object(
        task_path / "normalization_receipt.json",
        label="Normalization Receipt",
    )

    required_fields = {
        "request_id",
        "source_model_file",
        "source_model_sha256",
        "normalized_model_file",
        "normalized_model_sha256",
        "normalized_size_bytes",
        "status",
    }
    missing_fields = sorted(
        required_fields - set(receipt)
    )

    if missing_fields:
        raise M2ProviderError(
            "M2_MESH_NORMALIZATION_RECEIPT_INVALID",
            "Normalization Receipt缺少必要字段",
            details={
                "missing_fields": missing_fields,
            },
        )

    if (
        receipt.get("request_id")
        != manifest.get("request_id")
        or receipt.get("normalized_model_file")
        != model_filename
        or receipt.get("status")
        != "normalized"
    ):
        raise M2ProviderError(
            "M2_MESH_NORMALIZATION_PROVENANCE_CONFLICT",
            "归一化模型来源记录与当前任务不一致",
            details={
                "manifest_request_id": manifest.get(
                    "request_id"
                ),
                "receipt_request_id": receipt.get(
                    "request_id"
                ),
                "model_file": model_filename,
                "receipt_model_file": receipt.get(
                    "normalized_model_file"
                ),
            },
        )

    normalized_sha256 = receipt.get(
        "normalized_model_sha256"
    )
    normalized_size = receipt.get(
        "normalized_size_bytes"
    )

    if (
        not isinstance(normalized_sha256, str)
        or len(normalized_sha256) != 64
        or not isinstance(normalized_size, int)
        or normalized_size <= 0
    ):
        raise M2ProviderError(
            "M2_MESH_NORMALIZATION_RECEIPT_INVALID",
            "Normalization Receipt中的模型记录无效",
        )

    return normalized_sha256, normalized_size


def _validation_filename_for_model(
    model_filename: str,
) -> str:
    if model_filename == "normalized_model.glb":
        return "normalized_mesh_validation.json"

    return _gate8b_validation_filename_for_model(
        model_filename
    )
