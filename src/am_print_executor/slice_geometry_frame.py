"""Map machine-space blocker locations back into committed model geometry."""
from __future__ import annotations
import copy
import json
import re
import zipfile
from pathlib import Path
from typing import Any
import numpy as np
import trimesh
from scipy.spatial import cKDTree


def gate_report_in_geometry_frame(report: dict[str, Any], artifact: Path) -> dict[str, Any]:
    """Undo configured nozzle XY offsets, without changing Gate decisions.

    Current local repair supports one physical extruder offset. Multiple
    different offsets require per-tool observations and are rejected.
    """
    if report.get("coordinate_mapping", {}).get("applied"):
        raise ValueError("gate_report_already_mapped")
    with zipfile.ZipFile(artifact) as archive:
        settings = json.loads(archive.read("Metadata/project_settings.config"))
    raw = settings.get("extruder_offset")
    if raw is None:
        raise ValueError("missing_extruder_offset_coordinate_contract")
    raw = [raw] if isinstance(raw, str) else raw
    offsets = []
    for item in raw:
        match = re.fullmatch(r"\s*([-+\d.eE]+)\s*x\s*([-+\d.eE]+)\s*", str(item))
        if not match:
            raise ValueError("invalid_extruder_offset")
        offsets.append((float(match[1]), float(match[2])))
    if not offsets or len(set(offsets)) != 1 or not np.isfinite(offsets).all():
        raise ValueError("per_tool_coordinate_mapping_required")
    dx, dy = offsets[0]
    result = copy.deepcopy(report)
    for layer in result.get("dangerous_layers", []):
        for issue in layer.get("issues", []):
            bounds = issue.get("xy_bounds_mm")
            if bounds is not None:
                array = np.asarray(bounds, dtype=float)
                if array.shape != (2, 2) or not np.isfinite(array).all():
                    raise ValueError("invalid_blocker_bounds")
                issue["xy_bounds_mm"] = (array + [dx, dy]).tolist()
    result["coordinate_mapping"] = {"applied": True, "from": "machine_nozzle_commands",
                                     "to": "committed_3mf_world_geometry", "translation_xy_mm": [dx, dy],
                                     "source": "Metadata/project_settings.config:extruder_offset"}
    return result


def placed_mesh_from_project(artifact: Path) -> trimesh.Trimesh:
    from am_print_executor.rigid_multimaterial_project import parse_3mf_leaves
    from am_print_executor.mesh_integrity import exact_welded_copy
    leaves = parse_3mf_leaves(artifact)
    if not leaves:
        raise ValueError("project_has_no_geometry")
    mesh = trimesh.util.concatenate([trimesh.Trimesh(vertices=leaf["vertices_world"], faces=leaf["faces"], process=False) for leaf in leaves])
    return exact_welded_copy(mesh)


def verify_source_matches_slice(source: trimesh.Trimesh, artifact: Path, tolerance_mm: float = .001) -> dict:
    placed = placed_mesh_from_project(artifact)
    forward = cKDTree(placed.vertices).query(source.vertices)[0]
    reverse = cKDTree(source.vertices).query(placed.vertices)[0]
    error = float(max(forward.max(), reverse.max()))
    if error > tolerance_mm or len(source.faces) != len(placed.faces):
        raise ValueError("repair_source_does_not_match_committed_slice_geometry")
    return {"status": "pass", "maximum_vertex_distance_mm": error, "tolerance_mm": tolerance_mm}
