from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from am_model_generator.coordinate_frame import load_print_scene, export_print_glb
from jsonschema import Draft202012Validator

from .artifacts import ensure_valid_artifact_receipt
from .contracts import M2ProviderError, ensure_valid_m2_manifest


SCHEMA_VERSION = "0.1.0"
TOOL_NAME = "m2_mesh_repair"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MESH_REPAIR_SCHEMA_PATH = (
    PROJECT_ROOT / "schemas" / "m2_mesh_repair_receipt.schema.json"
)
MESH_VALIDATION_SCHEMA_PATH = (
    PROJECT_ROOT / "schemas" / "m2_mesh_validation.schema.json"
)

M2_MANIFEST_FILENAME = "m2_manifest.json"
ARTIFACT_RECEIPT_FILENAME = "artifact_receipt.json"
REPAIRED_MODEL_FILENAME = "repaired_model.glb"
MESH_REPAIR_RECEIPT_FILENAME = "mesh_repair_receipt.json"


class RepairError(RuntimeError):
    """Raised when a repair candidate cannot be produced safely."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            block = file.read(1024 * 1024)

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


def write_json_atomic(
    path: Path,
    data: dict[str, Any],
) -> None:
    temporary = path.with_name(
        f".{path.name}.tmp-{uuid.uuid4().hex}"
    )

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


def export_glb_atomic(
    mesh: trimesh.Trimesh,
    destination: Path,
) -> None:
    temporary = destination.with_name(
        f".{destination.name}.tmp-{uuid.uuid4().hex}"
    )

    data = export_print_glb(mesh)

    if not isinstance(data, bytes):
        raise RepairError(
            "Trimesh did not return GLB bytes"
        )

    temporary.write_bytes(data)
    temporary.replace(destination)


def load_combined_mesh(
    path: Path,
) -> tuple[trimesh.Trimesh, int]:
    try:
        scene = load_print_scene(
            path,
            process=False,
        )
    except Exception as error:
        raise RepairError(
            f"Cannot load input GLB: {error}"
        ) from error

    geometries = [
        value
        for value in scene.geometry.values()
        if isinstance(
            value,
            trimesh.Trimesh,
        )
    ]

    geometry_count = len(geometries)

    if geometry_count == 0:
        raise RepairError(
            "Input GLB contains no mesh geometry"
        )

    try:
        if hasattr(scene, "to_mesh"):
            mesh = scene.to_mesh()
        else:
            mesh = scene.dump(
                concatenate=True
            )
    except Exception as error:
        raise RepairError(
            f"Cannot combine GLB scene: {error}"
        ) from error

    if not isinstance(
        mesh,
        trimesh.Trimesh,
    ):
        raise RepairError(
            "Combined object is not a Trimesh"
        )

    if (
        len(mesh.vertices) == 0
        or len(mesh.faces) == 0
    ):
        raise RepairError(
            "Input mesh has no usable triangles"
        )

    return mesh, geometry_count


def remove_bad_faces(
    mesh: trimesh.Trimesh,
) -> None:
    if hasattr(mesh, "nondegenerate_faces"):
        mesh.update_faces(
            mesh.nondegenerate_faces()
        )
    elif hasattr(
        mesh,
        "remove_degenerate_faces",
    ):
        mesh.remove_degenerate_faces()

    if hasattr(mesh, "unique_faces"):
        mesh.update_faces(
            mesh.unique_faces()
        )
    elif hasattr(
        mesh,
        "remove_duplicate_faces",
    ):
        mesh.remove_duplicate_faces()


def clean_mesh(
    mesh: trimesh.Trimesh,
) -> trimesh.Trimesh:
    cleaned = mesh.copy()

    if hasattr(
        cleaned,
        "remove_infinite_values",
    ):
        cleaned.remove_infinite_values()

    remove_bad_faces(cleaned)

    if hasattr(
        cleaned,
        "remove_unreferenced_vertices",
    ):
        cleaned.remove_unreferenced_vertices()

    if hasattr(cleaned, "merge_vertices"):
        cleaned.merge_vertices()

    try:
        trimesh.repair.fix_winding(
            cleaned
        )
    except Exception:
        pass

    try:
        trimesh.repair.fix_normals(
            cleaned,
            multibody=True,
        )
    except TypeError:
        trimesh.repair.fix_normals(
            cleaned
        )

    if cleaned.is_watertight:
        try:
            if float(cleaned.volume) < 0:
                cleaned.invert()
        except Exception:
            pass

    return cleaned


def component_key(
    mesh: trimesh.Trimesh,
) -> tuple[float, int]:
    volume = 0.0

    if mesh.is_watertight:
        try:
            volume = abs(
                float(mesh.volume)
            )
        except Exception:
            volume = 0.0

    return (
        volume,
        int(len(mesh.faces)),
    )


def keep_largest_component(
    mesh: trimesh.Trimesh,
) -> tuple[
    trimesh.Trimesh,
    int,
]:
    components = mesh.split(
        only_watertight=False
    )

    if not components:
        raise RepairError(
            "Repair candidate has no connected component"
        )

    if len(components) == 1:
        return components[0], 0

    largest = max(
        components,
        key=component_key,
    )

    return (
        largest.copy(),
        len(components) - 1,
    )


def mesh_stats(
    mesh: trimesh.Trimesh,
    *,
    geometry_count: int = 1,
) -> dict[str, Any]:
    components = mesh.split(
        only_watertight=False
    )

    extents = [
        round(float(value), 8)
        for value in mesh.extents
    ]

    bounds = [
        [
            round(float(value), 8)
            for value in row
        ]
        for row in mesh.bounds
    ]

    try:
        broken_face_count = int(
            len(
                trimesh.repair.broken_faces(
                    mesh
                )
            )
        )
    except Exception:
        broken_face_count = -1

    volume = float(mesh.volume)

    if not math.isfinite(volume):
        volume = 0.0

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
        "broken_face_count": (
            broken_face_count
        ),
        "volume": round(
            volume,
            8,
        ),
        "extents": extents,
        "bounds": bounds,
    }


def try_surface_repair(
    source: trimesh.Trimesh,
) -> tuple[
    trimesh.Trimesh,
    dict[str, Any],
]:
    candidate = clean_mesh(source)

    before_faces = int(
        len(candidate.faces)
    )

    try:
        fill_result = (
            trimesh.repair.fill_holes(
                candidate,
                use_fan=True,
            )
        )
    except TypeError:
        fill_result = (
            trimesh.repair.fill_holes(
                candidate
            )
        )
    except Exception:
        fill_result = False

    candidate = clean_mesh(
        candidate
    )

    candidate, removed_components = (
        keep_largest_component(
            candidate
        )
    )

    candidate = clean_mesh(
        candidate
    )

    metadata = {
        "fill_holes_result": bool(
            fill_result
        ),
        "faces_added_or_removed": (
            int(len(candidate.faces))
            - before_faces
        ),
        "discarded_components": (
            removed_components
        ),
    }

    return candidate, metadata


def voxel_repair(
    source: trimesh.Trimesh,
    *,
    resolution: int,
) -> tuple[
    trimesh.Trimesh,
    dict[str, Any],
]:
    if resolution < 32:
        raise RepairError(
            "voxel resolution must be at least 32"
        )

    if resolution > 256:
        raise RepairError(
            "voxel resolution must not exceed 256"
        )

    extents = np.asarray(
        source.extents,
        dtype=float,
    )

    maximum_extent = float(
        np.max(extents)
    )

    if (
        not math.isfinite(
            maximum_extent
        )
        or maximum_extent <= 0
    ):
        raise RepairError(
            "Input mesh has invalid extents"
        )

    pitch = (
        maximum_extent
        / float(resolution)
    )

    try:
        voxels = source.voxelized(
            pitch=pitch,
            method="subdivide",
        )
        voxels = voxels.fill()
    except Exception as error:
        raise RepairError(
            f"Voxelization failed: {error}"
        ) from error

    if voxels.is_empty:
        raise RepairError(
            "Voxelization produced an empty grid"
        )

    try:
        candidate = (
            voxels.marching_cubes
        )
    except Exception as error:
        raise RepairError(
            "Marching cubes failed. "
            "Install scikit-image in the main "
            f"Python environment: {error}"
        ) from error

    # VoxelGrid.marching_cubes returns grid coordinates.
    # Apply the grid transform to restore source coordinates.
    candidate.apply_transform(
        voxels.transform
    )

    candidate = clean_mesh(
        candidate
    )

    candidate, removed_components = (
        keep_largest_component(
            candidate
        )
    )

    candidate = clean_mesh(
        candidate
    )

    metadata = {
        "voxel_resolution": resolution,
        "voxel_pitch": round(
            pitch,
            10,
        ),
        "voxel_shape": [
            int(value)
            for value in voxels.shape
        ],
        "filled_voxel_count": int(
            len(voxels.sparse_indices)
        ),
        "discarded_components": (
            removed_components
        ),
    }

    return candidate, metadata


def candidate_passes(
    mesh: trimesh.Trimesh,
) -> bool:
    components = mesh.split(
        only_watertight=False
    )

    volume = float(mesh.volume)

    return bool(
        len(mesh.vertices) > 0
        and len(mesh.faces) > 0
        and len(components) == 1
        and mesh.is_watertight
        and mesh.is_winding_consistent
        and math.isfinite(volume)
        and volume > 0
    )


def request_fingerprint(
    *,
    input_sha256: str,
    voxel_resolution: int,
) -> str:
    payload = {
        "input_sha256": input_sha256,
        "voxel_resolution": (
            voxel_resolution
        ),
        "tool": TOOL_NAME,
        "schema_version": (
            SCHEMA_VERSION
        ),
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_REPAIR_FILE_MISSING",
            f"{label}不存在",
            details={"path": str(path)},
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise M2ProviderError(
            "M2_REPAIR_INVALID_JSON",
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
            "M2_REPAIR_READ_FAILED",
            f"无法读取{label}",
            details={"path": str(path), "reason": str(error)},
        ) from error
    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_REPAIR_INVALID_OBJECT",
            f"{label}必须是JSON对象",
            details={"path": str(path)},
        )
    return data


def _load_schema(path: Path, *, label: str) -> dict[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise M2ProviderError(
            "M2_REPAIR_SCHEMA_LOAD_FAILED",
            f"无法读取{label}",
            details={"path": str(path), "reason": str(error)},
        ) from error
    if not isinstance(schema, dict):
        raise M2ProviderError(
            "M2_REPAIR_SCHEMA_INVALID",
            f"{label}必须是JSON对象",
        )
    Draft202012Validator.check_schema(schema)
    return schema


def _schema_errors(data: Any, schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    result: list[str] = []
    for error in errors:
        path = "$"
        for part in error.absolute_path:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        result.append(f"{path}: {error.message}")
    return result


def validate_mesh_repair_receipt(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["$: document must be a JSON object"]
    return _schema_errors(
        data,
        _load_schema(
            MESH_REPAIR_SCHEMA_PATH,
            label="Mesh Repair Schema",
        ),
    )


def ensure_valid_mesh_repair_receipt(data: Any) -> None:
    errors = validate_mesh_repair_receipt(data)
    if errors:
        raise M2ProviderError(
            "M2_REPAIR_RECEIPT_INVALID",
            "Mesh Repair Receipt不符合Schema",
            details={"errors": errors},
        )


def _ensure_valid_mesh_report(data: Any) -> None:
    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_REPAIR_SOURCE_VALIDATION_INVALID",
            "源Mesh验证报告必须是JSON对象",
        )
    errors = _schema_errors(
        data,
        _load_schema(
            MESH_VALIDATION_SCHEMA_PATH,
            label="Mesh Validation Schema",
        ),
    )
    if errors:
        raise M2ProviderError(
            "M2_REPAIR_SOURCE_VALIDATION_INVALID",
            "源Mesh验证报告不符合Schema",
            details={"errors": errors},
        )


def _updated_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    updated = dict(manifest)
    updated["status"] = "generated"
    updated["primary_model"] = REPAIRED_MODEL_FILENAME
    updated["validation_file"] = None
    updated["hard_constraints_passed"] = None
    updated["next_module"] = None
    ensure_valid_m2_manifest(updated)
    return updated


def _result_data(
    *,
    task_path: Path,
    receipt: dict[str, Any],
    reused_existing_repair: bool,
    manifest_changed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "repaired",
        "request_id": receipt["request_id"],
        "source_model": receipt["source_model_file"],
        "repaired_model": receipt["repaired_model_file"],
        "method": receipt["method"],
        "repaired_model_sha256": receipt["repaired_model_sha256"],
        "repaired_size_bytes": receipt["repaired_size_bytes"],
        "task_directory": str(task_path),
        "repair_receipt_file": str(
            task_path / MESH_REPAIR_RECEIPT_FILENAME
        ),
        "reused_existing_repair": reused_existing_repair,
        "manifest_changed": manifest_changed,
    }


def repair_m2_mesh(
    task_directory: str | Path,
    *,
    voxel_resolution: int = 128,
) -> dict[str, Any]:
    task_path = Path(task_directory).expanduser().resolve()
    if not task_path.is_dir():
        raise M2ProviderError(
            "M2_TASK_DIRECTORY_MISSING",
            "M2任务目录不存在",
            details={"path": str(task_path)},
        )
    if (
        isinstance(voxel_resolution, bool)
        or not isinstance(voxel_resolution, int)
        or not 32 <= voxel_resolution <= 256
    ):
        raise M2ProviderError(
            "M2_REPAIR_RESOLUTION_INVALID",
            "体素分辨率必须是32到256之间的整数",
            details={"voxel_resolution": voxel_resolution},
        )

    manifest_path = task_path / M2_MANIFEST_FILENAME
    artifact_path = task_path / ARTIFACT_RECEIPT_FILENAME
    repaired_path = task_path / REPAIRED_MODEL_FILENAME
    repair_receipt_path = task_path / MESH_REPAIR_RECEIPT_FILENAME

    manifest = _read_json_object(manifest_path, label="M2 Manifest")
    artifact = _read_json_object(artifact_path, label="Artifact Receipt")
    ensure_valid_m2_manifest(manifest)
    ensure_valid_artifact_receipt(artifact)

    if manifest.get("status") != "generated":
        raise M2ProviderError(
            "M2_REPAIR_STATUS_INVALID",
            "只有generated任务可以执行Mesh修复",
            details={"status": manifest.get("status")},
        )

    primary_model = manifest.get("primary_model")
    raw_model = artifact.get("local_file")
    if primary_model == REPAIRED_MODEL_FILENAME:
        if not repaired_path.is_file() or not repair_receipt_path.is_file():
            raise M2ProviderError(
                "M2_REPAIR_STATE_INCOMPLETE",
                "Manifest指向修复模型但修复工件不完整",
            )
        source_path = task_path / str(raw_model)
        source_sha256 = artifact["sha256"]
        source_size = artifact["size_bytes"]
        source_validation_file = None
        source_validation_sha256 = None
    elif primary_model == raw_model:
        source_path = task_path / str(raw_model)
        validation_name = manifest.get("validation_file")
        if not isinstance(validation_name, str) or not validation_name:
            raise M2ProviderError(
                "M2_REPAIR_VALIDATION_MISSING",
                "修复前必须先完成原始Mesh验证",
            )
        validation_path = task_path / validation_name
        validation = _read_json_object(
            validation_path,
            label="Source Mesh Validation",
        )
        _ensure_valid_mesh_report(validation)
        if (
            validation.get("request_id") != manifest.get("request_id")
            or validation.get("model_file") != raw_model
            or validation.get("model_sha256") != artifact.get("sha256")
        ):
            raise M2ProviderError(
                "M2_REPAIR_VALIDATION_CONFLICT",
                "源Mesh验证报告与当前模型不一致",
            )
        if validation.get("hard_constraints_passed"):
            raise M2ProviderError(
                "M2_REPAIR_NOT_REQUIRED",
                "源模型已经通过Mesh硬约束，不需要修复",
            )
        hard_checks = validation.get("hard_checks", {})
        preconditions = {
            key: bool(hard_checks.get(key))
            for key in ("has_geometry", "triangular_faces", "nonzero_extents")
        }
        if not all(preconditions.values()):
            raise M2ProviderError(
                "M2_REPAIR_PRECONDITIONS_FAILED",
                "源模型缺少可修复的基础几何条件",
                details={"required_preconditions": preconditions},
            )
        source_sha256 = sha256_file(source_path)
        source_size = source_path.stat().st_size
        if (
            source_sha256 != artifact.get("sha256")
            or source_size != artifact.get("size_bytes")
        ):
            raise M2ProviderError(
                "M2_REPAIR_SOURCE_CHANGED",
                "原始模型与Artifact Receipt不一致",
            )
        source_validation_file = validation_name
        source_validation_sha256 = sha256_file(validation_path)
    else:
        raise M2ProviderError(
            "M2_REPAIR_SOURCE_UNSUPPORTED",
            "当前修复阶段只接受原始Provider Artifact",
            details={
                "primary_model": primary_model,
                "artifact_model": raw_model,
            },
        )

    if not source_path.is_file():
        raise M2ProviderError(
            "M2_REPAIR_SOURCE_MISSING",
            "待修复模型文件不存在",
            details={"path": str(source_path)},
        )

    repaired_exists = repaired_path.exists()
    receipt_exists = repair_receipt_path.exists()
    if repaired_exists != receipt_exists:
        raise M2ProviderError(
            "M2_REPAIR_STATE_INCOMPLETE",
            "修复模型和Receipt状态不完整，已停止以避免覆盖",
            details={
                "repaired_model_exists": repaired_exists,
                "repair_receipt_exists": receipt_exists,
            },
        )

    if repaired_exists and receipt_exists:
        receipt = _read_json_object(
            repair_receipt_path,
            label="Mesh Repair Receipt",
        )
        ensure_valid_mesh_repair_receipt(receipt)
        actual_sha = sha256_file(repaired_path)
        actual_size = repaired_path.stat().st_size
        if (
            receipt.get("request_id") != manifest.get("request_id")
            or receipt.get("source_model_file") != raw_model
            or receipt.get("source_model_sha256") != artifact.get("sha256")
            or receipt.get("repaired_model_file") != REPAIRED_MODEL_FILENAME
            or receipt.get("repaired_model_sha256") != actual_sha
            or receipt.get("repaired_size_bytes") != actual_size
            or receipt.get("parameters", {}).get("voxel_resolution")
            != voxel_resolution
        ):
            raise M2ProviderError(
                "M2_REPAIR_RECEIPT_CONFLICT",
                "已有修复结果与当前任务或参数不一致",
            )
        updated = _updated_manifest(manifest)
        changed = updated != manifest
        if changed:
            write_json_atomic(manifest_path, updated)
        return _result_data(
            task_path=task_path,
            receipt=receipt,
            reused_existing_repair=True,
            manifest_changed=changed,
        )

    source_mesh, geometry_count = load_combined_mesh(source_path)
    source_mesh = clean_mesh(source_mesh)
    source_stats = mesh_stats(
        source_mesh,
        geometry_count=geometry_count,
    )
    surface_candidate, surface_metadata = try_surface_repair(source_mesh)
    surface_stats = mesh_stats(surface_candidate)

    if candidate_passes(surface_candidate):
        method = "surface_repair"
        candidate = surface_candidate
        method_metadata = surface_metadata
    else:
        method = "voxel_remesh"
        candidate, method_metadata = voxel_repair(
            source_mesh,
            resolution=voxel_resolution,
        )

    candidate = clean_mesh(candidate)
    repaired_stats = mesh_stats(candidate)
    if not candidate_passes(candidate):
        raise M2ProviderError(
            "M2_REPAIR_RESULT_INVALID",
            "修复结果仍未通过封闭、单连通和方向一致检查",
            details={"repaired_stats": repaired_stats},
        )

    export_glb_atomic(candidate, repaired_path)
    if sha256_file(source_path) != source_sha256:
        repaired_path.unlink(missing_ok=True)
        raise M2ProviderError(
            "M2_REPAIR_SOURCE_CHANGED",
            "原始模型在修复期间发生变化",
        )

    repaired_sha = sha256_file(repaired_path)
    repaired_size = repaired_path.stat().st_size
    receipt = {
        "schema_version": "0.1.0",
        "module": "M2",
        "request_id": manifest["request_id"],
        "source_model_file": raw_model,
        "source_model_sha256": source_sha256,
        "source_model_size_bytes": source_size,
        "source_validation_file": source_validation_file,
        "source_validation_sha256": source_validation_sha256,
        "repaired_model_file": REPAIRED_MODEL_FILENAME,
        "repaired_model_sha256": repaired_sha,
        "repaired_size_bytes": repaired_size,
        "method": method,
        "parameters": {"voxel_resolution": voxel_resolution},
        "source_stats": source_stats,
        "surface_attempt": {
            "metadata": surface_metadata,
            "stats": surface_stats,
        },
        "method_metadata": method_metadata,
        "repaired_stats": repaired_stats,
        "source_unchanged": True,
        "status": "repaired",
        "library": "trimesh",
        "library_version": str(trimesh.__version__),
    }
    ensure_valid_mesh_repair_receipt(receipt)
    write_json_atomic(repair_receipt_path, receipt)

    updated = _updated_manifest(manifest)
    changed = updated != manifest
    if changed:
        write_json_atomic(manifest_path, updated)

    return _result_data(
        task_path=task_path,
        receipt=receipt,
        reused_existing_repair=False,
        manifest_changed=changed,
    )
