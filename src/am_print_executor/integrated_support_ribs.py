"""Explicit design changes: permanent ribs growing from an existing foundation.

These are deliberately visible structural additions, not fidelity-limited GPR
patches. Callers must opt in, and must slice and inspect the result again.
"""
from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import box

from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.geometry_fidelity_gate import normalized_boolean_copy, mesh_validation
from am_print_executor.local_anchored_growth import section_region


def add_integrated_support_ribs(
    mesh: trimesh.Trimesh, clusters, *, margin_mm: float = .6,
    top_extension_mm: float = 1.0, minimum_width_mm: float = 1.2,
    maximum_added_volume_ratio: float = .2, critical_regions: tuple[dict, ...] = (),
) -> tuple[trimesh.Trimesh, dict]:
    source = normalized_boolean_copy(mesh)
    if not mesh_validation(source)["valid"] or source.body_count != 1:
        raise ValueError("ribs_require_one_valid_connected_solid")
    if not inspect_flat_printing_base(source)["base_flatness_passed"]:
        raise ValueError("ribs_require_stable_planar_foundation")
    if min(margin_mm, top_extension_mm, minimum_width_mm) <= 0:
        raise ValueError("invalid_rib_dimensions")
    z0, zmax = source.bounds[:, 2]
    bottom_z = z0 + .4  # Preserve the actual build-plate face.
    foundation = section_region(source, bottom_z)
    parts, records = [], []
    for cluster in clusters:
        if cluster.xy_bounds_mm is None or not set(cluster.kinds).issubset({
            "unsupported_extrusion_region", "unsupported_layer_island",
        }):
            continue
        bounds = np.asarray(cluster.xy_bounds_mm, dtype=float)
        center = bounds.mean(axis=0)
        half = np.maximum((bounds[1] - bounds[0]) / 2 + margin_mm, minimum_width_mm / 2)
        footprint = box(*(center - half), *(center + half))
        if not foundation.covers(footprint.buffer(.1)):
            raise ValueError("rib_footprint_not_inside_foundation")
        top_z = min(float(cluster.z_max_mm) + top_extension_mm, float(zmax))
        if top_z <= bottom_z:
            raise ValueError("rib_has_no_height")
        rib = trimesh.creation.extrude_polygon(footprint, top_z - bottom_z, engine="earcut")
        rib.apply_translation([0, 0, bottom_z])
        for region in critical_regions:
            protected = np.asarray(region.get("bounds_mm"), dtype=float)
            if protected.shape != (2, 3) or not np.isfinite(protected).all() or np.any(protected[1] <= protected[0]):
                raise ValueError("unlocalized_critical_region")
            if np.all(rib.bounds[1] >= protected[0]) and np.all(rib.bounds[0] <= protected[1]):
                raise ValueError("rib_intersects_critical_region")
        parts.append(rib)
        records.append({"cluster_id": cluster.cluster_id, "bounds_mm": rib.bounds.tolist(),
                        "minimum_width_mm": float(2 * half.min()), "grounded_in_foundation": True})
    if not parts:
        raise ValueError("no_supported_rib_repair_family")
    result = trimesh.boolean.union([source, *parts], engine="manifold", check_volume=True)
    added_ratio = float((result.volume - source.volume) / source.volume)
    if (not mesh_validation(result)["valid"] or result.body_count != 1 or
            not 0 < added_ratio <= maximum_added_volume_ratio or
            not np.allclose(result.bounds, source.bounds, atol=1e-4)):
        raise ValueError("structural_rib_design_limits_exceeded")
    if not inspect_flat_printing_base(result)["base_flatness_passed"]:
        raise ValueError("structural_ribs_damaged_base")
    return result, {"method": "integrated_foundation_support_ribs", "design_change": True,
                    "fidelity_limited_gpr": False, "source_surface_rebuilt": False,
                    "added_volume_ratio": added_ratio, "ribs": records,
                    "requires_new_slice_and_strict_gate": True}
