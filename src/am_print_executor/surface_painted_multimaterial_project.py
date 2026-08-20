from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import zipfile
import xml.etree.ElementTree as ET

from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from am_print_executor import multimaterial_project as mm
from am_print_executor.rigid_multimaterial_project import (
    parse_3mf_leaves,
)


class SurfacePaintError(RuntimeError):
    pass


_TRIANGLE_TAG_RE = re.compile(
    r"<triangle\b[^>]*/>",
    re.IGNORECASE | re.DOTALL,
)

_PAINT_ATTR_RE = re.compile(
    r"\s+paint_color\s*=\s*(['\"])[^'\"]*\1",
    re.IGNORECASE,
)


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
        raise SurfacePaintError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SurfacePaintError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise SurfacePaintError(f"Expected JSON object: {path}")

    return obj


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def encode_whole_triangle_paint(filament_id: int) -> str:
    """
    BambuStudio whole-triangle MMU paint encoding for the dual-material path.

    Current project contract intentionally supports IDs 1 and 2 only.
    The BambuStudio source CONST_FILAMENTS begins:
        "", "4", "8", ...
    """
    mapping = {
        1: "4",
        2: "8",
    }

    try:
        return mapping[int(filament_id)]
    except (KeyError, ValueError) as exc:
        raise SurfacePaintError(
            "This formal dual-material surface-paint path supports "
            "filament IDs 1 and 2 only."
        ) from exc


def validate_semantic_rule(rule: dict[str, Any]) -> dict[str, Any]:
    if rule.get("semantic_type") != "surface_paint":
        raise SurfacePaintError(
            "semantic_type must be 'surface_paint'."
        )

    if rule.get("rule") != "dorsal_accent_v1":
        raise SurfacePaintError(
            "Only rule='dorsal_accent_v1' is implemented."
        )

    base = int(rule.get("base_filament_id", 1))
    accent = int(rule.get("accent_filament_id", 2))

    if base != 1 or accent != 2:
        raise SurfacePaintError(
            "Current formal dual-colour contract requires base=1, accent=2."
        )

    fraction = float(rule.get("accent_fraction", 0.18))

    if not (0.05 <= fraction <= 0.40):
        raise SurfacePaintError(
            "accent_fraction must be between 0.05 and 0.40."
        )

    score = rule.get("score")
    if not isinstance(score, dict):
        score = {}

    z_weight = float(score.get("world_z_weight", 0.75))
    normal_weight = float(score.get("upward_normal_weight", 0.25))

    if z_weight < 0 or normal_weight < 0:
        raise SurfacePaintError("Paint score weights must be non-negative.")

    total = z_weight + normal_weight
    if total <= 0:
        raise SurfacePaintError("At least one paint score weight must be positive.")

    z_weight /= total
    normal_weight /= total

    return {
        "schema_version": str(rule.get("schema_version") or "1.0.0"),
        "semantic_type": "surface_paint",
        "rule": "dorsal_accent_v1",
        "base_filament_id": 1,
        "accent_filament_id": 2,
        "accent_fraction": fraction,
        "score": {
            "world_z_weight": z_weight,
            "upward_normal_weight": normal_weight,
        },
        "description": (
            str(rule.get("description") or "").strip()
            or "Base colour with an upper/upward-facing surface accent."
        ),
    }


def select_dorsal_accent_triangles(
    *,
    vertices_world: np.ndarray,
    faces: np.ndarray,
    accent_fraction: float,
    world_z_weight: float,
    upward_normal_weight: float,
) -> dict[str, Any]:
    vertices_world = np.asarray(vertices_world, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)

    if len(faces) < 2:
        raise SurfacePaintError("Mesh has too few triangles for surface painting.")

    tri = vertices_world[faces]
    centroids = tri.mean(axis=1)

    z = centroids[:, 2]
    z_min = float(np.min(z))
    z_max = float(np.max(z))
    z_span = z_max - z_min

    if z_span <= 1e-9:
        raise SurfacePaintError("Mesh has no meaningful Z span after Auto Orient.")

    z_norm = (z - z_min) / z_span

    cross = np.cross(
        tri[:, 1] - tri[:, 0],
        tri[:, 2] - tri[:, 0],
    )
    norm = np.linalg.norm(cross, axis=1)
    normal_z = np.zeros(len(faces), dtype=float)

    valid = norm > 1e-12
    normal_z[valid] = cross[valid, 2] / norm[valid]
    upward = np.clip(normal_z, 0.0, 1.0)

    score = (
        float(world_z_weight) * z_norm
        + float(upward_normal_weight) * upward
    )

    target_count = int(round(len(faces) * float(accent_fraction)))
    target_count = max(1, min(len(faces) - 1, target_count))

    # Stable deterministic ranking: score descending, source triangle index ascending.
    indices = np.arange(len(faces), dtype=np.int64)
    order = np.lexsort((indices, -score))
    selected = np.sort(order[:target_count])

    threshold = float(score[order[target_count - 1]])

    return {
        "selected_indices": selected.tolist(),
        "selected_count": int(len(selected)),
        "triangle_count": int(len(faces)),
        "selected_fraction": float(len(selected) / len(faces)),
        "score_threshold": threshold,
        "score_min": float(np.min(score)),
        "score_max": float(np.max(score)),
        "world_centroid_z_min": z_min,
        "world_centroid_z_max": z_max,
    }


def _creationflags() -> int:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW
    return 0


def _signed_return_code(raw: int) -> int:
    return mm._signed_return_code(int(raw))


def run_intact_auto_orient(
    *,
    studio_exe: Path,
    source_model: Path,
    output_path: Path,
    machine: Path,
    process: Path,
    filament_paths: list[Path],
    build_plate: str,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        output_path.unlink()
    except FileNotFoundError:
        pass

    command = [
        str(studio_exe),
        "--orient", "1",
        "--arrange", "1",
        "--ensure-on-bed",
        "--load-settings", f"{machine};{process}",
        "--curr-bed-type", build_plate,
        "--load-filaments", ";".join(str(path) for path in filament_paths),
        "--debug", "5",
        "--export-3mf", str(output_path),
        str(source_model),
    ]

    started = time.time()

    proc = subprocess.run(
        command,
        cwd=str(output_path.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        creationflags=_creationflags(),
    )

    raw_rc = int(proc.returncode)
    signed_rc = _signed_return_code(raw_rc)

    if signed_rc != 0:
        raise SurfacePaintError(
            "Bambu intact-source Auto Orient failed.\n"
            f"returncode_raw={raw_rc}\n"
            f"returncode_signed={signed_rc}\n"
            f"stdout_tail={proc.stdout[-3000:]}\n"
            f"stderr_tail={proc.stderr[-3000:]}"
        )

    if not output_path.is_file():
        raise SurfacePaintError(
            "Bambu Auto Orient returned success but exact 3MF output is missing."
        )

    xml_repair = mm.repair_bambu_model_settings_xml(output_path)

    from am_print_executor.bambu_project_xy_guard import (
        ProjectXYPlacementError,
        ensure_project_xy_on_bed,
    )

    try:
        xy = ensure_project_xy_on_bed(output_path)
    except ProjectXYPlacementError as exc:
        raise SurfacePaintError(
            "Intact Auto Orient produced invalid bed placement: " + str(exc)
        ) from exc

    leaves = parse_3mf_leaves(output_path)
    if len(leaves) != 1:
        raise SurfacePaintError(
            f"Intact source unexpectedly became {len(leaves)} physical mesh leaves."
        )

    return {
        "command": command,
        "returncode_raw": raw_rc,
        "returncode_signed": signed_rc,
        "elapsed_seconds": round(time.time() - started, 3),
        "sha256": _sha256(output_path),
        "xml_repair": xml_repair,
        "xy_placement": xy,
        "leaf_count": 1,
    }


def _find_leaf_xml_object(
    *,
    root: ET.Element,
    object_id: str,
) -> tuple[ET.Element, list[ET.Element], np.ndarray, np.ndarray]:
    target = None

    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "object" and str(el.attrib.get("id")) == str(object_id):
            target = el
            break

    if target is None:
        raise SurfacePaintError(f"Leaf object id {object_id} not found in model XML.")

    mesh_node = next(
        (c for c in target if c.tag.rsplit("}", 1)[-1] == "mesh"),
        None,
    )
    if mesh_node is None:
        raise SurfacePaintError("Leaf object contains no mesh node.")

    vertices_node = next(
        (c for c in mesh_node if c.tag.rsplit("}", 1)[-1] == "vertices"),
        None,
    )
    triangles_node = next(
        (c for c in mesh_node if c.tag.rsplit("}", 1)[-1] == "triangles"),
        None,
    )

    if vertices_node is None or triangles_node is None:
        raise SurfacePaintError("Leaf mesh is incomplete.")

    vertices = []
    for v in vertices_node:
        if v.tag.rsplit("}", 1)[-1] == "vertex":
            vertices.append(
                [
                    float(v.attrib["x"]),
                    float(v.attrib["y"]),
                    float(v.attrib["z"]),
                ]
            )

    triangle_elements = [
        tri
        for tri in triangles_node
        if tri.tag.rsplit("}", 1)[-1] == "triangle"
    ]

    faces = [
        [
            int(tri.attrib["v1"]),
            int(tri.attrib["v2"]),
            int(tri.attrib["v3"]),
        ]
        for tri in triangle_elements
    ]

    return (
        target,
        triangle_elements,
        np.asarray(vertices, dtype=float),
        np.asarray(faces, dtype=np.int64),
    )


def apply_surface_paint(
    *,
    source_project: Path,
    output_project: Path,
    semantic_rule: dict[str, Any],
) -> dict[str, Any]:
    leaves = parse_3mf_leaves(source_project)

    if len(leaves) != 1:
        raise SurfacePaintError(
            f"Surface-paint contract requires one physical leaf; got {len(leaves)}."
        )

    leaf = leaves[0]
    part_name = str(leaf["part"])
    object_id = str(leaf["object_id"])
    matrix = np.asarray(leaf["matrix"], dtype=float)

    with zipfile.ZipFile(source_project, "r") as zin:
        if part_name not in zin.namelist():
            raise SurfacePaintError(f"Leaf model part missing from ZIP: {part_name}")

        raw = zin.read(part_name)
        text = raw.decode("utf-8")
        root = ET.fromstring(raw)

        (
            _object,
            triangle_elements,
            vertices_local,
            faces,
        ) = _find_leaf_xml_object(
            root=root,
            object_id=object_id,
        )

        if any("paint_color" in tri.attrib for tri in triangle_elements):
            raise SurfacePaintError(
                "Reference project already contains paint_color; refusing to overwrite."
            )

        h = np.hstack(
            [
                vertices_local,
                np.ones((len(vertices_local), 1), dtype=float),
            ]
        )
        vertices_world = (h @ matrix)[:, :3]

        selection = select_dorsal_accent_triangles(
            vertices_world=vertices_world,
            faces=faces,
            accent_fraction=semantic_rule["accent_fraction"],
            world_z_weight=semantic_rule["score"]["world_z_weight"],
            upward_normal_weight=semantic_rule["score"]["upward_normal_weight"],
        )

        selected = set(int(x) for x in selection["selected_indices"])
        paint_code = encode_whole_triangle_paint(
            semantic_rule["accent_filament_id"]
        )

        matches = list(_TRIANGLE_TAG_RE.finditer(text))

        if len(matches) != len(triangle_elements):
            raise SurfacePaintError(
                "Triangle XML count mismatch; refusing a non-auditable patch. "
                f"xml_regex={len(matches)} parsed={len(triangle_elements)}"
            )

        pieces = []
        last = 0

        for index, match in enumerate(matches):
            pieces.append(text[last:match.start()])
            tag = match.group(0)

            if _PAINT_ATTR_RE.search(tag):
                raise SurfacePaintError(
                    "Unexpected pre-existing paint_color in raw XML."
                )

            if index in selected:
                tag = tag[:-2] + f' paint_color="{paint_code}"/>'

            pieces.append(tag)
            last = match.end()

        pieces.append(text[last:])
        patched = "".join(pieces).encode("utf-8")

        output_project.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(
            output_project,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zout:
            for info in zin.infolist():
                data = patched if info.filename == part_name else zin.read(info.filename)
                zout.writestr(info, data)

    return {
        "part_name": part_name,
        "object_id": object_id,
        "paint_code": paint_code,
        "triangle_count": selection["triangle_count"],
        "painted_triangle_count": selection["selected_count"],
        "painted_fraction": selection["selected_fraction"],
        "selection": selection,
    }


def inspect_paint(project: Path) -> dict[str, Any]:
    leaves = parse_3mf_leaves(project)

    if len(leaves) != 1:
        raise SurfacePaintError(f"Painted project leaf_count={len(leaves)}, expected 1.")

    leaf = leaves[0]
    part_name = str(leaf["part"])

    with zipfile.ZipFile(project, "r") as zf:
        raw = zf.read(part_name).decode("utf-8")

    triangle_tags = list(_TRIANGLE_TAG_RE.finditer(raw))
    painted = []

    for idx, match in enumerate(triangle_tags):
        attr = re.search(
            r'\bpaint_color\s*=\s*"([^"]+)"',
            match.group(0),
            flags=re.IGNORECASE,
        )
        if attr:
            painted.append((idx, attr.group(1)))

    return {
        "leaf_count": len(leaves),
        "triangle_count": len(triangle_tags),
        "painted_triangle_count": len(painted),
        "paint_codes": sorted({code for _, code in painted}),
    }


def geometry_integrity(
    *,
    source_stl: Path,
    painted_project: Path,
    bed_tolerance_mm: float = 0.05,
) -> dict[str, Any]:
    source = trimesh.load_mesh(source_stl, process=True)
    if not isinstance(source, trimesh.Trimesh):
        raise SurfacePaintError("Source STL could not be loaded as one mesh.")

    leaves = parse_3mf_leaves(painted_project)
    if len(leaves) != 1:
        return {
            "status": "geometry_fail",
            "checks": {
                "single_physical_mesh": False,
            },
            "failed_checks": ["single_physical_mesh"],
        }

    leaf = leaves[0]
    actual = trimesh.Trimesh(
        vertices=leaf["vertices_world"],
        faces=leaf["faces"],
        process=False,
    )

    source_volume = abs(float(source.volume))
    actual_volume = abs(float(actual.volume))

    volume_error_ratio = (
        abs(actual_volume - source_volume) / source_volume
        if source_volume > 1e-12
        else math.inf
    )

    min_z = float(np.min(np.asarray(actual.vertices)[:, 2]))

    checks = {
        "single_physical_mesh": True,
        "source_volume_preserved": volume_error_ratio <= 1e-5,
        "model_on_bed": abs(min_z) <= bed_tolerance_mm,
        "no_physical_material_partition": True,
    }

    return {
        "status": (
            "geometry_pass"
            if all(checks.values())
            else "geometry_fail"
        ),
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "source_volume_mm3": source_volume,
        "project_volume_mm3": actual_volume,
        "volume_error_ratio": volume_error_ratio,
        "global_min_z_mm": min_z,
        "leaf_count": 1,
    }


def build_surface_painted_project(
    *,
    studio_exe: Path,
    legacy_manifest: Path,
    semantic_rule_path: Path,
    source_task_dir: Path,
    output_path: Path,
    build_plate: str,
) -> dict[str, Any]:
    legacy = _load_json(legacy_manifest)
    rule = validate_semantic_rule(_load_json(semantic_rule_path))

    source_row = legacy.get("source_model")
    if not isinstance(source_row, dict):
        raise SurfacePaintError("Legacy manifest has no source_model provenance.")

    source_stl = Path(str(source_row.get("path") or "")).expanduser().resolve()
    if not source_stl.is_file():
        raise SurfacePaintError(f"Original intact source STL missing: {source_stl}")

    profiles = legacy.get("filament_profiles")
    if not isinstance(profiles, list) or len(profiles) != 2:
        raise SurfacePaintError(
            "Formal dual-colour paint path requires exactly two filament profiles."
        )

    filament_paths = []
    for row in profiles:
        if not isinstance(row, dict):
            raise SurfacePaintError("Invalid filament profile row.")
        path = Path(str(row.get("path") or "")).expanduser().resolve()
        if not path.is_file():
            raise SurfacePaintError(f"Filament profile missing: {path}")
        filament_paths.append(path)

    studio_exe = Path(studio_exe).expanduser().resolve()
    source_task_dir = Path(source_task_dir).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()

    machine = mm.discover_resolved_profile(source_task_dir, "machine")
    process = mm.discover_resolved_profile(source_task_dir, "process")

    work = output_path.parent / (output_path.stem + ".surface_paint_work")
    work.mkdir(parents=True, exist_ok=True)

    reference_project = work / "intact_auto_orient_reference.project.3mf"

    orientation = run_intact_auto_orient(
        studio_exe=studio_exe,
        source_model=source_stl,
        output_path=reference_project,
        machine=machine,
        process=process,
        filament_paths=filament_paths,
        build_plate=build_plate,
    )

    if output_path.exists():
        output_path.unlink()

    paint = apply_surface_paint(
        source_project=reference_project,
        output_project=output_path,
        semantic_rule=rule,
    )

    inspection = mm.inspect_project_3mf(
        output_path,
        expected_filament_count=2,
        expected_bed_type=build_plate,
    )

    paint_inspection = inspect_paint(output_path)

    geometry = geometry_integrity(
        source_stl=source_stl,
        painted_project=output_path,
    )

    paint_checks = {
        "single_physical_mesh":
            paint_inspection["leaf_count"] == 1,

        "two_filaments_present":
            inspection["project_filament_count"] == 2,

        "accent_triangles_present":
            paint_inspection["painted_triangle_count"] > 0,

        "accent_not_entire_model":
            paint_inspection["painted_triangle_count"]
            < paint_inspection["triangle_count"],

        "accent_uses_filament_2_code":
            paint_inspection["paint_codes"] == ["8"],

        "paint_count_roundtrip_preserved":
            paint_inspection["painted_triangle_count"]
            == paint["painted_triangle_count"],
    }

    failed_paint = [
        key for key, value in paint_checks.items() if not value
    ]

    gate_pass = (
        geometry["status"] == "geometry_pass"
        and not failed_paint
    )

    status = (
        "surface_painted_multimaterial_project_ready"
        if gate_pass
        else "surface_painted_multimaterial_project_fail"
    )

    report = {
        "schema_version": "surface-painted-multimaterial-v1",
        "module": "M4",
        "stage": "R1_MATERIAL_SEMANTICS_R2_AUTO_ORIENT",
        "status": status,
        "source_model": {
            "path": str(source_stl),
            "sha256": _sha256(source_stl),
        },
        "legacy_partition_artifacts_used": False,
        "legacy_generated_regions_used": False,
        "semantic_rule": rule,
        "auto_orient": orientation,
        "paint": paint,
        "paint_inspection": paint_inspection,
        "project": inspection,
        "geometry_gate": geometry,
        "paint_checks": paint_checks,
        "failed_paint_checks": failed_paint,
        "safety": {
            "network_used": False,
            "printer_contacted": False,
            "artifact_uploaded": False,
            "print_started": False,
        },
        "next_gate": "R2S_SLICE_PAINT_PROOF" if gate_pass else None,
    }

    report_path = output_path.with_suffix(
        output_path.suffix + ".surface_paint_report.json"
    )
    _write_json(report_path, report)
    report["report_path"] = str(report_path)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create one intact Auto-Oriented Bambu 3MF and apply explicit "
            "per-triangle dual-colour surface-paint semantics without cutting "
            "the source into material volumes."
        )
    )
    parser.add_argument("--studio", required=True)
    parser.add_argument("--legacy-manifest", required=True)
    parser.add_argument("--semantic-rule", required=True)
    parser.add_argument("--source-task-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--build-plate", required=True)
    args = parser.parse_args()

    result = build_surface_painted_project(
        studio_exe=Path(args.studio),
        legacy_manifest=Path(args.legacy_manifest),
        semantic_rule_path=Path(args.semantic_rule),
        source_task_dir=Path(args.source_task_dir),
        output_path=Path(args.output),
        build_plate=args.build_plate,
    )

    geometry = result["geometry_gate"]
    paint = result["paint_inspection"]

    print("=== R1S/R2S SURFACE-PAINTED MULTIMATERIAL PROJECT ===")
    print("PHYSICAL_MESH_COUNT=", geometry.get("leaf_count"))
    print("LEGACY_GENERATED_REGIONS_USED=False")
    print("LEGACY_Z60_PARTITION_USED=False")
    print("AUTO_ORIENT_INTACT_SOURCE_COUNT=1")
    print("POST_PAINT_AUTO_ORIENT_COUNT=0")
    print()
    print("SEMANTIC_RULE=", result["semantic_rule"]["rule"])
    print("BASE_FILAMENT_ID=1")
    print("ACCENT_FILAMENT_ID=2")
    print("ACCENT_PAINT_CODE=8")
    print("TOTAL_TRIANGLES=", paint["triangle_count"])
    print("PAINTED_TRIANGLES=", paint["painted_triangle_count"])
    print(
        "PAINTED_FRACTION=",
        (
            paint["painted_triangle_count"] / paint["triangle_count"]
            if paint["triangle_count"]
            else 0.0
        ),
    )
    print("PAINT_CODES=", paint["paint_codes"])
    print()
    for key, value in geometry["checks"].items():
        print(key.upper(), "=", value)
    for key, value in result["paint_checks"].items():
        print(key.upper(), "=", value)
    print("FAILED_GEOMETRY_CHECKS=", geometry["failed_checks"])
    print("FAILED_PAINT_CHECKS=", result["failed_paint_checks"])
    print()
    print("NETWORK_USED=False")
    print("PRINTER_CONTACTED=False")
    print("ARTIFACT_UPLOADED=False")
    print("PRINT_STARTED=False")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("REPORT=", result["report_path"])

    return 0 if result["status"] == "surface_painted_multimaterial_project_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
