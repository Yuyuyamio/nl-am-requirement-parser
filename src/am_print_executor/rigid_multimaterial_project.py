from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import posixpath
import time
import zipfile
import xml.etree.ElementTree as ET

from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from am_print_executor import multimaterial_project as mm
from am_print_executor.bambu_auto_orient import (
    BambuAutoOrientError,
    auto_orient_with_bambu_cli,
)
from am_print_executor.bambu_headless_cli import (
    run_bambu_cli,
)


class RigidMultiMaterialError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RigidMultiMaterialError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RigidMultiMaterialError(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise RigidMultiMaterialError(f"Expected JSON object: {path}")
    return obj


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def classify_partition_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    """
    Distinguish actual material-region input from synthetic geometric splitting.

    A ratio/absolute plane split of one source mesh is useful for tests, but it
    must never silently become a production colour/material definition.
    """
    partition = manifest.get("partition")
    if not isinstance(partition, dict):
        return {
            "synthetic_geometric_partition": False,
            "explicit_material_semantics": True,
            "reason": "no_generated_partition",
        }

    mode = str(partition.get("mode") or "").strip().lower()
    synthetic = mode in {"ratio", "position", "absolute", "plane"}

    explicit = (
        partition.get("explicit_material_semantics") is True
        or str(partition.get("intent") or "").strip().lower()
        in {
            "explicit_geometric_material_partition",
            "explicit_material_regions",
        }
    )

    return {
        "synthetic_geometric_partition": synthetic and not explicit,
        "explicit_material_semantics": explicit or not synthetic,
        "partition_mode": mode,
        "reason": (
            "generated_plane_partition_without_explicit_material_semantics"
            if synthetic and not explicit
            else "material_semantics_explicit_or_not_generated"
        ),
    }


def _lname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr_local(el: ET.Element, name: str) -> str | None:
    for key, value in el.attrib.items():
        if _lname(key) == name:
            return value
    return None


def _matrix3mf(value: str | None) -> np.ndarray:
    if not value:
        return np.eye(4, dtype=float)

    nums = [float(x) for x in value.split()]
    if len(nums) != 12:
        raise RigidMultiMaterialError(f"Invalid 3MF transform: {value!r}")

    # 3MF row-vector convention.
    return np.array(
        [
            [nums[0], nums[1], nums[2], 0.0],
            [nums[3], nums[4], nums[5], 0.0],
            [nums[6], nums[7], nums[8], 0.0],
            [nums[9], nums[10], nums[11], 1.0],
        ],
        dtype=float,
    )


def _transform_points(vertices: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    h = np.hstack(
        [np.asarray(vertices, dtype=float), np.ones((len(vertices), 1), dtype=float)]
    )
    return (h @ matrix)[:, :3]


def _normalize_part(current: str, ref: str | None) -> str:
    if not ref:
        return current
    ref = ref.replace("\\", "/")
    if ref.startswith("/"):
        return ref.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(current), ref))


def parse_3mf_leaves(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise RigidMultiMaterialError(f"Not a valid 3MF ZIP: {path}")

    parts: dict[str, dict[str, Any]] = {}

    with zipfile.ZipFile(path, "r") as zf:
        for name in [n for n in zf.namelist() if n.lower().endswith(".model")]:
            root = ET.fromstring(zf.read(name))
            objects: dict[str, Any] = {}

            resources = next((c for c in root if _lname(c.tag) == "resources"), None)
            if resources is not None:
                for obj in resources:
                    if _lname(obj.tag) != "object":
                        continue
                    oid = str(obj.attrib.get("id"))

                    mesh_node = next(
                        (c for c in obj if _lname(c.tag) == "mesh"),
                        None,
                    )
                    comps_node = next(
                        (c for c in obj if _lname(c.tag) == "components"),
                        None,
                    )

                    mesh = None
                    components = []

                    if mesh_node is not None:
                        vertices_node = next(
                            (c for c in mesh_node if _lname(c.tag) == "vertices"),
                            None,
                        )
                        triangles_node = next(
                            (c for c in mesh_node if _lname(c.tag) == "triangles"),
                            None,
                        )
                        vertices = []
                        faces = []

                        if vertices_node is not None:
                            for v in vertices_node:
                                if _lname(v.tag) == "vertex":
                                    vertices.append(
                                        [
                                            float(v.attrib["x"]),
                                            float(v.attrib["y"]),
                                            float(v.attrib["z"]),
                                        ]
                                    )

                        if triangles_node is not None:
                            for tri in triangles_node:
                                if _lname(tri.tag) == "triangle":
                                    faces.append(
                                        [
                                            int(tri.attrib["v1"]),
                                            int(tri.attrib["v2"]),
                                            int(tri.attrib["v3"]),
                                        ]
                                    )

                        mesh = {
                            "vertices": np.asarray(vertices, dtype=float),
                            "faces": np.asarray(faces, dtype=np.int64),
                        }

                    if comps_node is not None:
                        for comp in comps_node:
                            if _lname(comp.tag) != "component":
                                continue
                            components.append(
                                {
                                    "objectid": str(comp.attrib["objectid"]),
                                    "path": _attr_local(comp, "path"),
                                    "matrix": _matrix3mf(comp.attrib.get("transform")),
                                }
                            )

                    objects[oid] = {"mesh": mesh, "components": components}

            build_items = []
            build = next((c for c in root if _lname(c.tag) == "build"), None)
            if build is not None:
                for item in build:
                    if _lname(item.tag) == "item":
                        build_items.append(
                            {
                                "objectid": str(item.attrib["objectid"]),
                                "path": _attr_local(item, "path"),
                                "matrix": _matrix3mf(item.attrib.get("transform")),
                            }
                        )

            parts[name] = {"objects": objects, "build": build_items}

    root_part = "3D/3dmodel.model" if "3D/3dmodel.model" in parts else None
    if root_part is None:
        candidates = [name for name, data in parts.items() if data["build"]]
        if len(candidates) != 1:
            raise RigidMultiMaterialError(
                f"Cannot uniquely locate root 3MF model part: {candidates}"
            )
        root_part = candidates[0]

    leaves: list[dict[str, Any]] = []

    def resolve(
        part_name: str,
        object_id: str,
        outer_matrix: np.ndarray,
        stack: tuple[tuple[str, str], ...],
    ) -> None:
        key = (part_name, object_id)
        if key in stack:
            raise RigidMultiMaterialError(f"Recursive 3MF component reference: {key}")

        part = parts.get(part_name)
        if part is None:
            raise RigidMultiMaterialError(f"Missing referenced 3MF part: {part_name}")

        obj = part["objects"].get(object_id)
        if obj is None:
            raise RigidMultiMaterialError(
                f"Object {object_id} missing in part {part_name}"
            )

        if obj["mesh"] is not None:
            leaves.append(
                {
                    "part": part_name,
                    "object_id": object_id,
                    "vertices_local": obj["mesh"]["vertices"],
                    "faces": obj["mesh"]["faces"],
                    "matrix": outer_matrix,
                    "vertices_world": _transform_points(
                        obj["mesh"]["vertices"],
                        outer_matrix,
                    ),
                }
            )
            return

        for comp in obj["components"]:
            target_part = _normalize_part(part_name, comp["path"])
            resolve(
                target_part,
                comp["objectid"],
                comp["matrix"] @ outer_matrix,
                stack + (key,),
            )

    for item in parts[root_part]["build"]:
        target_part = _normalize_part(root_part, item["path"])
        resolve(
            target_part,
            item["objectid"],
            item["matrix"],
            tuple(),
        )

    return leaves


def extract_reference_rotation(reference_project: Path) -> dict[str, Any]:
    leaves = parse_3mf_leaves(reference_project)
    if len(leaves) != 1:
        raise RigidMultiMaterialError(
            f"Reference Auto Orient must produce exactly one leaf; got {len(leaves)}"
        )

    matrix = np.asarray(leaves[0]["matrix"], dtype=float)
    linear = matrix[:3, :3]

    gram = linear @ linear.T
    orth_error = float(np.max(np.abs(gram - np.eye(3))))
    determinant = float(np.linalg.det(linear))

    if orth_error > 1e-4:
        raise RigidMultiMaterialError(
            f"Bambu reference transform is not rigid: orthogonality error={orth_error}"
        )

    if abs(abs(determinant) - 1.0) > 1e-4:
        raise RigidMultiMaterialError(
            f"Bambu reference transform changes scale: det={determinant}"
        )

    rotation = np.eye(4, dtype=float)
    rotation[:3, :3] = linear

    return {
        "rotation_matrix_row_vector": rotation,
        "reference_effective_matrix": matrix,
        "orthogonality_error": orth_error,
        "determinant": determinant,
        "reference_leaf_bounds": (
            np.vstack(
                [
                    np.min(leaves[0]["vertices_world"], axis=0),
                    np.max(leaves[0]["vertices_world"], axis=0),
                ]
            )
            .round(6)
            .tolist()
        ),
    }


def _mesh(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load_mesh(path, process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise RigidMultiMaterialError(f"Expected one mesh: {path}")
    return mesh


def _mesh_stats(mesh: trimesh.Trimesh) -> dict[str, Any]:
    return {
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "bounds": np.asarray(mesh.bounds, dtype=float).round(6).tolist(),
        "volume_abs": float(abs(mesh.volume)),
        "centroid_vertices": (
            np.asarray(mesh.vertices, dtype=float).mean(axis=0).round(6).tolist()
        ),
    }


def build_shared_rigid_regions(
    *,
    manifest: dict[str, Any],
    rotation: np.ndarray,
    output_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    objects = manifest.get("objects")
    if not isinstance(objects, list) or len(objects) < 2:
        raise RigidMultiMaterialError(
            "Rigid multimaterial assembly requires at least two material objects."
        )

    placement = manifest.get("placement")
    if not isinstance(placement, dict):
        raise RigidMultiMaterialError("Manifest placement is missing.")

    target = placement.get("position_mm")
    if not isinstance(target, list) or len(target) != 3:
        raise RigidMultiMaterialError("Manifest placement.position_mm is invalid.")

    source_meshes: list[trimesh.Trimesh] = []
    source_paths: list[Path] = []

    for obj in objects:
        if int(obj.get("count", 1)) != 1:
            raise RigidMultiMaterialError(
                "Rigid material-part mode currently requires count=1 for every part."
            )

        positions = obj.get("positions_mm")
        if not (
            isinstance(positions, list)
            and len(positions) == 1
            and len(positions[0]) == 3
        ):
            raise RigidMultiMaterialError(
                "Rigid material-part mode requires one explicit position per part."
            )

        source_path = Path(str(obj.get("path") or "")).expanduser().resolve()
        if not source_path.is_file():
            raise RigidMultiMaterialError(f"Material part missing: {source_path}")

        source_paths.append(source_path)
        source_meshes.append(_mesh(source_path))

    # Apply exactly the SAME Bambu-chosen rotation to all material parts.
    rotated_vertices = []
    for mesh in source_meshes:
        rotated_vertices.append(
            _transform_points(np.asarray(mesh.vertices, dtype=float), rotation)
        )

    all_vertices = np.vstack(rotated_vertices)
    lo = np.min(all_vertices, axis=0)
    hi = np.max(all_vertices, axis=0)
    center = (lo + hi) / 2.0

    translation = np.array(
        [
            float(target[0]) - float(center[0]),
            float(target[1]) - float(center[1]),
            float(target[2]) - float(lo[2]),
        ],
        dtype=float,
    )

    shared = rotation.copy()
    shared[3, :3] = translation

    rigid_dir = Path(output_dir) / "rigid_regions"
    rigid_dir.mkdir(parents=True, exist_ok=True)

    derived = copy.deepcopy(manifest)
    derived_objects = []
    region_rows = []

    for index, (obj, mesh, src) in enumerate(
        zip(objects, source_meshes, source_paths),
        start=1,
    ):
        vertices = _transform_points(
            np.asarray(mesh.vertices, dtype=float),
            shared,
        )

        out_mesh = trimesh.Trimesh(
            vertices=vertices,
            faces=np.asarray(mesh.faces, dtype=np.int64),
            process=False,
        )

        out_path = rigid_dir / f"material_part_{index:02d}.stl"
        out_mesh.export(out_path)

        new_obj = copy.deepcopy(obj)
        new_obj["path"] = str(out_path.resolve())
        # Geometry is already in final shared world coordinates.
        new_obj["positions_mm"] = [[0.0, 0.0, 0.0]]
        derived_objects.append(new_obj)

        region_rows.append(
            {
                "index": index,
                "source_path": str(src),
                "output_path": str(out_path.resolve()),
                "source": _mesh_stats(mesh),
                "rigid": _mesh_stats(out_mesh),
                "sha256": _sha256(out_path),
            }
        )

    derived["objects"] = derived_objects
    derived["placement"] = {
        **placement,
        "position_mm": [0.0, 0.0, 0.0],
        "placement_applied_to_geometry": True,
    }

    derived["rigid_orientation"] = {
        "schema_version": "1.0.0",
        "mode": "single_reference_auto_orient_shared_transform",
        "shared_transform_row_vector": shared.round(12).tolist(),
        "shared_rotation_row_vector": rotation.round(12).tolist(),
        "shared_translation_mm": translation.round(9).tolist(),
        "material_part_count": len(derived_objects),
        "individual_part_auto_orient_allowed": False,
        "individual_part_arrange_allowed": False,
    }

    return derived, {
        "shared_transform_row_vector": shared,
        "regions": region_rows,
        "combined_rotated_bounds_before_translation": [
            lo.round(6).tolist(),
            hi.round(6).tolist(),
        ],
        "translation_mm": translation.round(9).tolist(),
    }


def pairwise_centroid_signature(meshes: list[trimesh.Trimesh]) -> list[float]:
    centers = [
        np.asarray(mesh.vertices, dtype=float).mean(axis=0)
        for mesh in meshes
    ]

    distances = []
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            distances.append(float(np.linalg.norm(centers[i] - centers[j])))

    return sorted(distances)


def _world_leaf_meshes(project: Path) -> list[trimesh.Trimesh]:
    out = []
    for leaf in parse_3mf_leaves(project):
        out.append(
            trimesh.Trimesh(
                vertices=leaf["vertices_world"],
                faces=leaf["faces"],
                process=False,
            )
        )
    return out


def geometry_gate(
    *,
    expected_region_paths: list[Path],
    project_path: Path,
    centroid_tolerance_mm: float = 0.05,
    bed_tolerance_mm: float = 0.05,
) -> dict[str, Any]:
    expected = [_mesh(Path(path)) for path in expected_region_paths]
    actual = _world_leaf_meshes(project_path)

    expected_sig = pairwise_centroid_signature(expected)
    actual_sig = pairwise_centroid_signature(actual)

    leaf_count_ok = len(expected) == len(actual)

    distance_error = None
    centroid_ok = False

    if len(expected_sig) == len(actual_sig):
        if not expected_sig:
            distance_error = 0.0
        else:
            distance_error = max(
                abs(a - b)
                for a, b in zip(expected_sig, actual_sig)
            )
        centroid_ok = distance_error <= centroid_tolerance_mm

    expected_volume = sum(abs(float(mesh.volume)) for mesh in expected)
    actual_volume = sum(abs(float(mesh.volume)) for mesh in actual)

    volume_error_ratio = (
        abs(actual_volume - expected_volume) / expected_volume
        if expected_volume > 1e-12
        else math.inf
    )
    volume_ok = volume_error_ratio <= 1e-5

    combined = trimesh.util.concatenate(actual)
    try:
        combined.merge_vertices()
    except Exception:
        pass

    pieces = list(combined.split(only_watertight=False))
    components = []
    floating = []

    for index, piece in enumerate(pieces):
        vertices = np.asarray(piece.vertices, dtype=float)
        min_z = float(np.min(vertices[:, 2])) if len(vertices) else math.inf

        row = {
            "component_index": index,
            "min_z": min_z,
            "bounds": np.asarray(piece.bounds, dtype=float).round(6).tolist(),
            "floating": min_z > bed_tolerance_mm,
        }
        components.append(row)
        if row["floating"]:
            floating.append(index)

    global_min_z = min(
        float(np.min(np.asarray(mesh.vertices)[:, 2]))
        for mesh in actual
        if len(mesh.vertices)
    )

    bed_ok = abs(global_min_z) <= bed_tolerance_mm
    floating_ok = len(floating) == 0

    checks = {
        "leaf_count_preserved": leaf_count_ok,
        "pairwise_centroid_distances_preserved": centroid_ok,
        "material_volume_preserved": volume_ok,
        "combined_geometry_on_bed": bed_ok,
        "floating_physical_components_zero": floating_ok,
    }

    return {
        "status": (
            "rigid_geometry_pass"
            if all(checks.values())
            else "rigid_geometry_fail"
        ),
        "checks": checks,
        "failed_checks": [k for k, v in checks.items() if not v],
        "expected_leaf_count": len(expected),
        "actual_leaf_count": len(actual),
        "expected_pairwise_centroid_distances_mm": expected_sig,
        "actual_pairwise_centroid_distances_mm": actual_sig,
        "max_pairwise_centroid_distance_error_mm": distance_error,
        "expected_volume_mm3": expected_volume,
        "actual_volume_mm3": actual_volume,
        "volume_error_ratio": volume_error_ratio,
        "global_min_z_mm": global_min_z,
        "physical_component_count_after_weld": len(components),
        "floating_components": floating,
        "components": components,
    }


def run_reference_auto_orient(
    *,
    studio_exe: Path,
    source_model: Path,
    output_path: Path,
    machine: Path,
    process: Path,
    first_filament: Path,
    build_plate: str,
) -> dict[str, Any]:
    started = time.time()

    try:
        auto_orient = auto_orient_with_bambu_cli(
            studio_exe=studio_exe,
            source_model=source_model,
            output_path=output_path,
            machine_json=machine,
            process_json=process,
            filament_jsons=[first_filament],
            build_plate=build_plate,
            max_attempts=3,
            timeout=600,
        )
    except BambuAutoOrientError as exc:
        raise RigidMultiMaterialError(
            "Bambu reference Auto Orient failed.\n"
            + str(exc)
        ) from exc

    orientation = extract_reference_rotation(output_path)

    return {
        "project": str(output_path),
        "sha256": _sha256(output_path),
        "command": auto_orient["command"],
        "returncode_raw": auto_orient[
            "returncode_raw"
        ],
        "returncode_signed": auto_orient[
            "returncode_signed"
        ],
        "auto_orient_attempt_count": auto_orient[
            "attempt_count"
        ],
        "auto_orient_attempts": auto_orient[
            "attempts"
        ],
        "xml_repair": auto_orient[
            "output"
        ].get("xml_repair"),
        "elapsed_seconds": round(time.time() - started, 3),
        **{
            key: (
                value.tolist()
                if isinstance(value, np.ndarray)
                else value
            )
            for key, value in orientation.items()
        },
        "_rotation_matrix": orientation["rotation_matrix_row_vector"],
    }


def assemble_rigid_project(
    *,
    studio_exe: Path,
    job_manifest: Path,
    source_task_dir: Path,
    output_path: Path,
    build_plate: str,
    allow_synthetic_partition_for_validation: bool = False,
) -> dict[str, Any]:
    studio_exe = Path(studio_exe).expanduser().resolve()
    job_manifest = Path(job_manifest).expanduser().resolve()
    source_task_dir = Path(source_task_dir).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()

    if not studio_exe.is_file():
        raise RigidMultiMaterialError(f"Bambu Studio missing: {studio_exe}")

    raw_manifest = _load_json(job_manifest)
    policy = classify_partition_policy(raw_manifest)

    synthetic = policy["synthetic_geometric_partition"]
    if synthetic and not allow_synthetic_partition_for_validation:
        raise RigidMultiMaterialError(
            "Production blocked: this manifest was generated by a geometric plane/ratio "
            "partition without explicit material semantics. Provide real material regions, "
            "or use --allow-synthetic-partition-for-validation for geometry-only validation."
        )

    try:
        job = mm.load_multimaterial_job(job_manifest)
    except Exception as exc:
        raise RigidMultiMaterialError(str(exc)) from exc

    source_row = raw_manifest.get("source_model")
    if not isinstance(source_row, dict):
        raise RigidMultiMaterialError(
            "Rigid Auto Orient requires source_model provenance in the manifest."
        )

    source_model = Path(str(source_row.get("path") or "")).expanduser().resolve()
    if not source_model.is_file():
        raise RigidMultiMaterialError(f"Source model missing: {source_model}")

    machine = mm.discover_resolved_profile(source_task_dir, "machine")
    process = mm.discover_resolved_profile(source_task_dir, "process")

    filament_paths = [
        Path(row["path"]).expanduser().resolve()
        for row in job["filament_profiles"]
    ]
    if not filament_paths:
        raise RigidMultiMaterialError("No filament profile available.")

    work = output_path.parent / (output_path.stem + ".rigid_work")
    work.mkdir(parents=True, exist_ok=True)

    reference_project = work / "reference_auto_orient.project.3mf"

    reference = run_reference_auto_orient(
        studio_exe=studio_exe,
        source_model=source_model,
        output_path=reference_project,
        machine=machine,
        process=process,
        first_filament=filament_paths[0],
        build_plate=build_plate,
    )

    rotation = reference.pop("_rotation_matrix")

    derived_manifest, rigid_info = build_shared_rigid_regions(
        manifest=raw_manifest,
        rotation=rotation,
        output_dir=work,
    )

    derived_manifest["material_semantics_policy"] = {
        **policy,
        "production_eligible": not synthetic,
        "validation_override_used": bool(
            synthetic and allow_synthetic_partition_for_validation
        ),
    }

    derived_manifest_path = work / "rigid_multimaterial_job.json"
    _write_json(derived_manifest_path, derived_manifest)

    assemble_json = work / "rigid_multimaterial.assemble.json"
    payload = mm.build_assemble_payload(derived_manifest_path)
    _write_json(assemble_json, payload)

    try:
        output_path.unlink()
    except FileNotFoundError:
        pass

    # Critical geometry contract: never re-orient individual material regions.
    # A validated reference orientation (possibly produced after an isolated retry)
    # was already committed from the intact source.
    command = [
        str(studio_exe),
        "--enable-support=1",
        "--support-type=tree(auto)",
        "--detect-floating-vertical-shell=1",
        "--detect-overhang-wall=1",
        "--bridge-no-support=0",
        "--load-settings", f"{machine};{process}",
        "--curr-bed-type", build_plate,
        "--load-filaments", ";".join(str(p) for p in filament_paths),
        "--load-assemble-list", str(assemble_json),
        "--debug", "5",
        "--export-3mf", str(output_path),
    ]

    started = time.time()
    cli_result = run_bambu_cli(
        command,
        expected_outputs=[output_path],
        cwd=str(output_path.parent),
        timeout=600,
    )

    raw_rc = cli_result.raw_exit
    signed_rc = cli_result.signed_exit

    if not cli_result.success:
        raise RigidMultiMaterialError(
            "Bambu rigid multimaterial assembly failed.\n"
            f"returncode_raw={raw_rc}\n"
            f"returncode_signed={signed_rc}\n"
            f"outputs_exist={cli_result.outputs_exist}\n"
            f"stdout_tail={cli_result.stdout[-3000:]}\n"
            f"stderr_tail={cli_result.stderr[-3000:]}"
        )

    xml_repair = mm.repair_bambu_model_settings_xml(output_path)

    from am_print_executor.bambu_project_xy_guard import (
        ProjectXYPlacementError,
        ensure_project_xy_on_bed,
    )

    try:
        xy_placement = ensure_project_xy_on_bed(output_path)
    except ProjectXYPlacementError as exc:
        raise RigidMultiMaterialError(
            "Rigid project XY placement invalid: " + str(exc)
        ) from exc

    inspection = mm.inspect_project_3mf(
        output_path,
        expected_filament_count=job["project_filament_count"],
        expected_bed_type=build_plate,
    )

    expected_region_paths = [
        Path(obj["path"]).resolve()
        for obj in derived_manifest["objects"]
    ]

    gate = geometry_gate(
        expected_region_paths=expected_region_paths,
        project_path=output_path,
    )

    geometry_pass = gate["status"] == "rigid_geometry_pass"
    production_eligible = bool(
        derived_manifest["material_semantics_policy"]["production_eligible"]
    )

    if not geometry_pass:
        final_status = "rigid_geometry_fail"
        next_gate = None
    elif not production_eligible:
        final_status = "rigid_geometry_validation_pass_material_semantics_blocked"
        next_gate = "R1_MATERIAL_SEMANTICS"
    else:
        final_status = "rigid_multimaterial_project_ready"
        next_gate = "R3_PROJECT_INTEGRITY"

    report = {
        "schema_version": "rigid-multimaterial-project-v1",
        "module": "M4",
        "status": final_status,
        "pipeline": "committed_reference_auto_orient_shared_rigid_transform",
        "source_manifest": str(job_manifest),
        "source_model": str(source_model),
        "material_semantics_policy": derived_manifest[
            "material_semantics_policy"
        ],
        "reference_auto_orient": reference,
        "rigid_regions": {
            "shared_transform_row_vector": (
                rigid_info["shared_transform_row_vector"].round(12).tolist()
            ),
            "translation_mm": rigid_info["translation_mm"],
            "regions": rigid_info["regions"],
        },
        "assembly": {
            "derived_manifest": str(derived_manifest_path),
            "assemble_json": str(assemble_json),
            "command": command,
            "individual_orient_present": "--orient" in command,
            "individual_arrange_present": "--arrange" in command,
            "returncode_raw": raw_rc,
            "returncode_signed": signed_rc,
            "elapsed_seconds": round(time.time() - started, 3),
            "xml_repair": xml_repair,
            "xy_placement": xy_placement,
        },
        "project": inspection,
        "geometry_gate": gate,
        "safety": {
            "network_used": False,
            "printer_contacted": False,
            "artifact_uploaded": False,
            "print_started": False,
        },
        "next_gate": next_gate,
    }

    report_path = output_path.with_suffix(output_path.suffix + ".rigid_report.json")
    _write_json(report_path, report)
    report["report_path"] = str(report_path)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rigid multimaterial Bambu project builder: produce one validated "
            "reference orientation for the intact source, apply its rigid transform "
            "to every material part, then assemble without per-part orient/arrange."
        )
    )
    parser.add_argument("--studio", required=True)
    parser.add_argument("--job-manifest", required=True)
    parser.add_argument("--source-task-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--build-plate", required=True)
    parser.add_argument(
        "--allow-synthetic-partition-for-validation",
        action="store_true",
        help=(
            "Geometry-validation only. Does not make a ratio/plane split "
            "production-eligible."
        ),
    )
    args = parser.parse_args()

    result = assemble_rigid_project(
        studio_exe=Path(args.studio),
        job_manifest=Path(args.job_manifest),
        source_task_dir=Path(args.source_task_dir),
        output_path=Path(args.output),
        build_plate=args.build_plate,
        allow_synthetic_partition_for_validation=(
            args.allow_synthetic_partition_for_validation
        ),
    )

    gate = result["geometry_gate"]
    policy = result["material_semantics_policy"]

    print("=== R2V RIGID MULTIMATERIAL AUTO-ORIENT ===")
    print("PIPELINE=committed_reference_auto_orient_shared_rigid_transform")
    print("COMMITTED_REFERENCE_AUTO_ORIENT_COUNT=1")
    print(
        "REFERENCE_AUTO_ORIENT_CLI_ATTEMPT_COUNT=",
        len(result["reference_auto_orient"].get("attempts", [])),
    )
    print(
        "MATERIAL_PART_AUTO_ORIENT_COUNT=",
        1 if result["assembly"]["individual_orient_present"] else 0,
    )
    print(
        "MATERIAL_PART_ARRANGE_COUNT=",
        1 if result["assembly"]["individual_arrange_present"] else 0,
    )
    print("SYNTHETIC_GEOMETRIC_PARTITION=", policy["synthetic_geometric_partition"])
    print("PRODUCTION_ELIGIBLE=", policy["production_eligible"])
    print("VALIDATION_OVERRIDE_USED=", policy["validation_override_used"])
    print()
    print("LEAF_COUNT_PRESERVED=", gate["checks"]["leaf_count_preserved"])
    print(
        "PAIRWISE_CENTROID_DISTANCES_PRESERVED=",
        gate["checks"]["pairwise_centroid_distances_preserved"],
    )
    print(
        "MAX_CENTROID_DISTANCE_ERROR_MM=",
        gate["max_pairwise_centroid_distance_error_mm"],
    )
    print("MATERIAL_VOLUME_PRESERVED=", gate["checks"]["material_volume_preserved"])
    print("GLOBAL_MIN_Z_MM=", gate["global_min_z_mm"])
    print(
        "FLOATING_PHYSICAL_COMPONENTS_ZERO=",
        gate["checks"]["floating_physical_components_zero"],
    )
    print("FLOATING_COMPONENTS=", gate["floating_components"])
    print("FAILED_CHECKS=", gate["failed_checks"])
    print()
    print("NETWORK_USED=False")
    print("PRINTER_CONTACTED=False")
    print("ARTIFACT_UPLOADED=False")
    print("PRINT_STARTED=False")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("REPORT=", result["report_path"])

    return 0 if gate["status"] == "rigid_geometry_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
