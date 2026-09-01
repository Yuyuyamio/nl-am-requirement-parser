"""Small geometry-integrity helpers; no printability repair decisions."""
from __future__ import annotations

from typing import Any

import numpy as np
import trimesh


def exact_welded_copy(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Weld exactly coincident vertices without moving or repairing surfaces."""
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError("not_a_triangle_mesh")
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces)
    if (
        vertices.ndim != 2
        or vertices.shape[1:] != (3,)
        or len(vertices) < 4
        or not np.isfinite(vertices).all()
        or faces.ndim != 2
        or faces.shape[1:] != (3,)
        or len(faces) < 4
        or faces.min() < 0
        or faces.max() >= len(vertices)
    ):
        raise ValueError("empty_or_invalid_mesh_arrays")
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    result = trimesh.Trimesh(
        vertices=unique,
        faces=inverse[faces],
        process=False,
    )
    result.remove_unreferenced_vertices()
    return result


def inspect_mesh_integrity(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """Check that a mesh is finite, non-degenerate, watertight and positive."""
    result: dict[str, Any] = {"valid": False, "blockers": []}
    if (
        not isinstance(mesh, trimesh.Trimesh)
        or len(mesh.vertices) < 4
        or len(mesh.faces) < 4
    ):
        result["blockers"].append("empty_or_invalid_mesh")
        return result
    vertices = np.asarray(mesh.vertices, dtype=float)
    bounds = np.asarray(mesh.bounds, dtype=float)
    extents = np.asarray(mesh.extents, dtype=float)
    if (
        not np.isfinite(vertices).all()
        or bounds.shape != (2, 3)
        or extents.shape != (3,)
        or not np.isfinite(bounds).all()
        or not np.isfinite(extents).all()
        or np.any(extents <= 0)
    ):
        result["blockers"].append("nonfinite_or_degenerate_geometry")
        return result
    volume = float(mesh.volume)
    result.update(
        watertight=bool(mesh.is_watertight),
        winding_consistent=bool(mesh.is_winding_consistent),
        component_count=int(mesh.body_count),
        volume_mm3=volume if np.isfinite(volume) else None,
        bounds_mm=bounds.tolist(),
        extents_mm=extents.tolist(),
    )
    if not result["watertight"]:
        result["blockers"].append("mesh_not_watertight")
    if not result["winding_consistent"]:
        result["blockers"].append("mesh_winding_inconsistent")
    if not np.isfinite(volume) or volume <= 1e-9:
        result["blockers"].append("mesh_not_positive_volume")
    result["valid"] = not result["blockers"]
    return result
