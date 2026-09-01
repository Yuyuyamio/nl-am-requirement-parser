"""Local additive ramps with a fully embedded bottom and bounded growth slope."""
from __future__ import annotations
from typing import Any
import numpy as np
import trimesh
from shapely.geometry import Point, Polygon, box
from shapely.ops import nearest_points


def section_region(mesh: trimesh.Trimesh, z: float):
    section = mesh.section(plane_normal=[0, 0, 1], plane_origin=[0, 0, z])
    region = Polygon()
    if section is None:
        return region
    for loop in section.discrete:
        if len(loop) >= 4 and np.linalg.norm(loop[0] - loop[-1]) < 1e-5:
            ring = Polygon(loop[:, :2])
            if ring.is_valid:
                # Even/odd nesting preserves holes while reading sections.
                region = region.symmetric_difference(ring)
    return region


def build_anchored_growth_envelopes(
    source: trimesh.Trimesh, clusters, *, margin_mm: float = .3,
    layer_height_mm: float = .2, line_width_mm: float = .42,
    slope_xy_per_z: float = .9, maximum_depth_mm: float = 4.0,
    protected_base_clearance_mm: float = .6,
) -> tuple[list[trimesh.Trimesh], list[dict[str, Any]]]:
    """Cover every layer in each cluster, not just its first observation.

    The bottom square must be wholly contained in source sections at both
    embedding planes. Each corresponding corner grows no faster than the
    configured XY/Z slope. Merely touching an anchor is insufficient.
    """
    meshes, records = [], []
    cache = {}
    minimum_z = float(source.bounds[0, 2])
    protected_z = minimum_z + protected_base_clearance_mm
    half_anchor = line_width_mm * .5

    def region(z):
        key = round(z, 6)
        if key not in cache:
            cache[key] = section_region(source, key)
        return cache[key]

    for cluster in clusters:
        record = {"cluster_id": cluster.cluster_id, "status": "blocked"}
        records.append(record)
        if cluster.xy_bounds_mm is None or not set(cluster.kinds).issubset({"unsupported_extrusion_region", "unsupported_layer_island"}):
            record["reason"] = "requires_other_repair_family"
            continue
        bounds = np.asarray(cluster.xy_bounds_mm)
        center = bounds.mean(axis=0)
        half = (bounds[1] - bounds[0]) / 2 + margin_mm
        top_z = min(cluster.z_max_mm + layer_height_mm, float(source.bounds[1, 2]))
        signs = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]])
        top_xy = center + signs * half
        for depth in np.arange(layer_height_mm, maximum_depth_mm + 1e-8, layer_height_mm):
            anchor_z = cluster.z_min_mm - depth
            bottom_z = anchor_z - layer_height_mm * .5
            if bottom_z <= protected_z:
                break
            lower, upper = region(bottom_z), region(anchor_z)
            available = lower.intersection(upper).buffer(-half_anchor * 1.42 - .02)
            if available.is_empty:
                continue
            anchor = nearest_points(available, Point(center))[0]
            anchor_xy = np.array(anchor.coords[0])
            bottom_xy = anchor_xy + signs * half_anchor
            footprint = Polygon(bottom_xy)
            if not lower.covers(footprint) or not upper.covers(footprint):
                continue
            corner_shift = float(np.linalg.norm(top_xy - bottom_xy, axis=1).max())
            actual_depth = top_z - bottom_z
            if corner_shift > slope_xy_per_z * actual_depth:
                continue
            vertices = np.vstack([np.column_stack([bottom_xy, np.full(4, bottom_z)]),
                                  np.column_stack([top_xy, np.full(4, top_z)])])
            envelope = trimesh.convex.convex_hull(vertices)
            meshes.append(envelope)
            record.update(status="candidate", top_z_mm=top_z, bottom_z_mm=bottom_z,
                          anchor_z_mm=anchor_z, anchor_xy_mm=anchor_xy.tolist(),
                          top_xy_bounds_mm=[(center-half).tolist(), (center+half).tolist()],
                          maximum_corner_shift_mm=corner_shift, vertical_depth_mm=actual_depth,
                          slope_xy_per_z=corner_shift/actual_depth,
                          fully_embedded_anchor=True, covered_z_max_mm=cluster.z_max_mm)
            break
        if record["status"] != "candidate":
            record["reason"] = "no_fully_embedded_slope_safe_anchor"
    return meshes, records
