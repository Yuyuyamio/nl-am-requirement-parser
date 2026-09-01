"""Construct an explicit planar foundation without rebuilding model surfaces."""
from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import MultiPoint


def add_printable_foundation(
    mesh: trimesh.Trimesh, *, footprint_fraction: float = 1.0,
    thickness_mm: float | None = None, margin_mm: float = 0.8,
    critical_regions: tuple[dict, ...] = (),
) -> tuple[trimesh.Trimesh, dict]:
    """Union a rounded convex foundation at the current minimum Z.

    This deliberately changes the design's base. It is not a fidelity-limited
    local GPR patch, and must be enabled by the calling design policy. Existing
    source geometry and its height are preserved. Unknown critical regions
    fail closed rather than being silently filled.
    """
    from am_print_executor.geometry_fidelity_gate import normalized_boolean_copy, mesh_validation

    source = normalized_boolean_copy(mesh)
    if not mesh_validation(source)["valid"] or source.body_count != 1:
        raise ValueError("foundation_requires_one_valid_connected_solid")
    if not 0 < footprint_fraction <= 1:
        raise ValueError("footprint_fraction must be in (0, 1]")
    height = float(source.extents[2])
    z0 = float(source.bounds[0, 2])
    thickness = float(thickness_mm if thickness_mm is not None else np.clip(height * .04, .8, 1.5))
    if not 0 < thickness < height or margin_mm < 0:
        raise ValueError("invalid foundation dimensions")
    vertices = source.vertices[source.vertices[:, 2] <= z0 + height * footprint_fraction]
    xy = np.vstack([vertices[:, :2], source.center_mass[:2]])
    footprint = MultiPoint(xy).convex_hull.buffer(float(margin_mm), quad_segs=8)
    foundation = trimesh.creation.extrude_polygon(footprint, height=thickness, engine="earcut")
    foundation.apply_translation((0, 0, z0))
    for region in critical_regions:
        bounds = np.asarray(region.get("bounds_mm"), dtype=float)
        if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or np.any(bounds[1] <= bounds[0]):
            raise ValueError("unlocalized_critical_region")
        if np.all(foundation.bounds[1] >= bounds[0]) and np.all(foundation.bounds[0] <= bounds[1]):
            raise ValueError("foundation_intersects_critical_region")
    result = trimesh.boolean.union([source, foundation], engine="manifold", check_volume=True)
    validation = mesh_validation(result)
    if not validation["valid"] or result.body_count != 1:
        raise ValueError("foundation_union_invalid")
    return result, {"method": "additive_planar_foundation", "thickness_mm": thickness,
                    "footprint_fraction": footprint_fraction, "margin_mm": margin_mm,
                    "foundation_area_mm2": float(footprint.area),
                    "added_volume_mm3": float(result.volume - source.volume),
                    "source_height_mm": height, "result_height_mm": float(result.extents[2]),
                    "design_change": "integrated_planar_base", "source_surface_rebuilt": False}
