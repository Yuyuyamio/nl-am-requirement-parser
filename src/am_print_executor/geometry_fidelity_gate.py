"""Read-only geometry checks for local additive printability repair.

No repair operation lives in this gate. Volume loss is measured using an
intersection, never by producing a subtractively modified candidate.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from am_print_executor.flat_base_gate import inspect_flat_printing_base

if TYPE_CHECKING:
    from am_print_executor.general_printability_geometry_repair import RepairBudget


MAXIMUM_REMOVED_VOLUME_RATIO = 0.0005
SURFACE_SAMPLE_EDGE_MM = 0.25
MAXIMUM_SURFACE_CELLS = 100_000
MAXIMUM_ADAPTIVE_SURFACE_SAMPLE_EDGE_MM = 1.0
NUMERICAL_DISTANCE_MM = 1e-5


def normalized_boolean_copy(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Weld only exactly coincident STL vertices, without moving any surface.

    Unlike process/validate or a repair tool, this does not remove faces, fill
    holes, reverse winding, or merge nearby but distinct geometry.
    """
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError("not_a_triangle_mesh")
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces)
    if (vertices.ndim != 2 or vertices.shape[1:] != (3,)
            or len(vertices) < 4 or not np.isfinite(vertices).all()
            or faces.ndim != 2 or faces.shape[1:] != (3,) or len(faces) < 4
            or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("empty_or_invalid_mesh_arrays")
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    result = trimesh.Trimesh(vertices=unique, faces=inverse[faces], process=False)
    result.remove_unreferenced_vertices()
    return result


def mesh_validation(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """Check shape before indexing bounds, including empty boolean output."""
    result: dict[str, Any] = {"valid": False, "blockers": []}
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) < 4 or len(mesh.faces) < 4:
        result["blockers"].append("empty_or_invalid_mesh")
        return result
    vertices = np.asarray(mesh.vertices, dtype=float)
    bounds = np.asarray(mesh.bounds, dtype=float)
    extents = np.asarray(mesh.extents, dtype=float)
    if (not np.isfinite(vertices).all() or bounds.shape != (2, 3)
            or extents.shape != (3,) or not np.isfinite(bounds).all()
            or not np.isfinite(extents).all() or np.any(extents <= 0)):
        result["blockers"].append("nonfinite_or_degenerate_geometry")
        return result
    volume = float(mesh.volume)
    result.update(watertight=bool(mesh.is_watertight),
                  winding_consistent=bool(mesh.is_winding_consistent),
                  component_count=int(mesh.body_count), volume_mm3=volume if np.isfinite(volume) else None,
                  bounds_mm=bounds.tolist(), extents_mm=extents.tolist())
    if not result["watertight"]:
        result["blockers"].append("mesh_not_watertight")
    if not result["winding_consistent"]:
        result["blockers"].append("mesh_winding_inconsistent")
    if not np.isfinite(volume) or volume <= 1e-9:
        result["blockers"].append("mesh_not_positive_volume")
    result["valid"] = not result["blockers"]
    return result


def _intersection_volume(*meshes: trimesh.Trimesh) -> float:
    # Keep the manifold status: trimesh's adapter does not check it and an
    # invalid manifold must not masquerade as a valid empty intersection.
    import manifold3d

    operands = []
    for mesh in meshes:
        operand = manifold3d.Manifold(manifold3d.Mesh(
            np.asarray(mesh.vertices, dtype=np.float32),
            np.asarray(mesh.faces, dtype=np.uint32)))
        if operand.status() != manifold3d.Error.NoError:
            raise ValueError(f"intersection_input:{operand.status()}")
        operands.append(operand)
    common = operands[0]
    for operand in operands[1:]:
        common = common ^ operand
        if common.status() != manifold3d.Error.NoError:
            raise ValueError(f"intersection_result:{common.status()}")
    volume = float(common.volume())
    if not np.isfinite(volume) or volume < -1e-8:
        raise ValueError("invalid_intersection_volume")
    return max(0.0, volume)


def _box(bounds: np.ndarray) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=bounds[1] - bounds[0])
    mesh.apply_translation(bounds.mean(axis=0))
    return mesh


def _triangle_keys(mesh: trimesh.Trimesh) -> set[tuple]:
    return {tuple(sorted(tuple(v) for v in triangle)) for triangle in mesh.triangles}


def _changed_triangles(mesh: trimesh.Trimesh, reference: trimesh.Trimesh) -> np.ndarray:
    keys = _triangle_keys(reference)
    return np.asarray([t for t in mesh.triangles
                       if tuple(sorted(tuple(v) for v in t)) not in keys], dtype=float).reshape((-1, 3, 3))


def _exclude_covered_coplanar_triangles(triangles: np.ndarray, reference: trimesh.Trimesh):
    """Certify complete surface coverage before subdividing Boolean seams.

    A Boolean can retriangulate a large, unchanged face. Vertex equality (or
    testing only the three corners) cannot prove that its interior is unchanged.
    Instead project nearby coplanar reference faces into each triangle's plane
    and require their union to cover the WHOLE triangle, including its interior.
    Plane and projection tolerances each contribute at most NUMERICAL_DISTANCE_MM
    to the Euclidean distance bound. Uncovered triangles retain the original
    deterministic quadrature and all its limits.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    reference_triangles = reference.triangles
    lower, upper = reference_triangles.min(axis=1), reference_triangles.max(axis=1)
    epsilon = NUMERICAL_DISTANCE_MM
    remaining, certified_area, certified_count = [], 0.0, 0
    for triangle in triangles:
        nearby = np.all(upper >= triangle.min(axis=0) - epsilon, axis=1) & np.all(
            lower <= triangle.max(axis=0) + epsilon, axis=1)
        faces = reference_triangles[nearby]
        edge = triangle[1] - triangle[0]
        normal = np.cross(edge, triangle[2] - triangle[0])
        length = np.linalg.norm(normal)
        if not len(faces) or length <= 1e-15:
            remaining.append(triangle)
            continue
        normal /= length
        coplanar = np.abs((faces - triangle[0]) @ normal).max(axis=1) <= epsilon
        faces = faces[coplanar]
        if not len(faces):
            remaining.append(triangle)
            continue
        u = edge / np.linalg.norm(edge)
        basis = np.stack((u, np.cross(normal, u)), axis=1)
        projected = Polygon((triangle - triangle[0]) @ basis)
        polygons = [Polygon(points) for points in (faces - triangle[0]) @ basis]
        coverage = unary_union([p for p in polygons if p.is_valid and p.area > 0])
        if coverage.buffer(epsilon).covers(projected):
            certified_count += 1
            certified_area += length * .5
        else:
            remaining.append(triangle)
    return np.asarray(remaining, dtype=float).reshape((-1, 3, 3)), {
        "triangle_count": certified_count, "area_mm2": certified_area,
        "maximum_distance_bound_mm": float(np.sqrt(2) * epsilon) if certified_count else 0.0,
    }


def _surface_cells(triangles: np.ndarray, *, sample_edge_mm: float | None = None) -> np.ndarray:
    """Longest-edge bisection of measurement triangles, never the mesh.

    Splitting all three edges of skinny coplanar triangles creates huge numbers
    of tiny cells after a boolean merely retriangulates a flat base. Bisecting
    the longest edge retains the same maximum edge, area weights, distance
    bounds and cell budget without this avoidable blow-up.
    """
    sample_edge_mm = float(SURFACE_SAMPLE_EDGE_MM if sample_edge_mm is None else sample_edge_mm)
    if not np.isfinite(sample_edge_mm) or sample_edge_mm <= 0:
        raise ValueError("invalid_surface_sample_edge")
    finished = []
    count = 0
    pending = triangles
    while len(pending):
        edge = np.linalg.norm(pending - np.roll(pending, 1, axis=1), axis=2).max(axis=1)
        small = pending[edge <= sample_edge_mm]
        finished.append(small)
        count += len(small)
        large = pending[edge > sample_edge_mm]
        if count + len(large) * 2 > MAXIMUM_SURFACE_CELLS:
            raise ValueError("surface_measurement_budget_exceeded")
        longest = np.linalg.norm(large - np.roll(large, -1, axis=1), axis=2).argmax(axis=1)
        rows = np.arange(len(large))
        a, b, c = large[rows,longest], large[rows,(longest+1)%3], large[rows,(longest+2)%3]
        middle = (a+b)/2
        pending = np.concatenate([np.stack((a,middle,c),axis=1),np.stack((middle,b,c),axis=1)])
    return np.concatenate(finished) if finished else np.empty((0, 3, 3))


def _surface_cells_with_bounded_resolution(
    triangles: np.ndarray, *, maximum_sample_edge_mm: float,
) -> tuple[np.ndarray, float]:
    """Coarsen deterministically when skinny facets exceed the cell cap.

    The distance calculation retains conservative per-cell upper bounds.  The
    caller limits the maximum edge so an omitted partially changed cell is
    still provably below the P95 displacement policy.
    """
    maximum_sample_edge_mm = max(float(SURFACE_SAMPLE_EDGE_MM), float(maximum_sample_edge_mm))
    sample_edge_mm = float(SURFACE_SAMPLE_EDGE_MM)
    while True:
        try:
            return _surface_cells(triangles, sample_edge_mm=sample_edge_mm), sample_edge_mm
        except ValueError as exc:
            if str(exc) != "surface_measurement_budget_exceeded" or sample_edge_mm >= maximum_sample_edge_mm:
                raise
            sample_edge_mm = float(min(maximum_sample_edge_mm, sample_edge_mm * np.sqrt(2.0)))


def _nearest_surface_distance(mesh: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """Exact point/triangle distance with AABB pruning; no optional rtree."""
    triangles = mesh.triangles
    lower, upper = triangles.min(axis=1), triangles.max(axis=1)
    vertex_upper, _ = cKDTree(mesh.vertices).query(points)
    result = []
    for point, limit in zip(points, vertex_upper):
        delta = np.maximum(np.maximum(lower - point, point - upper), 0.0)
        indices = np.flatnonzero(np.einsum("ij,ij->i", delta, delta) <= (limit + 1e-9) ** 2)
        closest = trimesh.triangles.closest_point(triangles[indices], np.broadcast_to(point, (len(indices), 3)))
        result.append(float(np.linalg.norm(closest - point, axis=1).min()))
    return np.asarray(result)


def _surface_displacement(
    source: trimesh.Trimesh, candidate: trimesh.Trimesh, *,
    maximum_surface_displacement_p95_mm: float = 1.5,
) -> dict[str, Any]:
    distances, areas, bounds, changed_bounds = [], [], [], []
    sample_count = 0
    coplanar_certificates = []
    sampling_resolutions = []
    maximum_p95 = float(maximum_surface_displacement_p95_mm)
    if not np.isfinite(maximum_p95) or maximum_p95 <= 0:
        raise ValueError("invalid_surface_displacement_p95_budget")
    # For any triangle with longest edge L, its centroid radius is at most
    # 2L/3. A cell omitted as only partially changed is therefore bounded by
    # 4L/3 + epsilon. Keep that strictly inside the P95 policy.
    maximum_sample_edge = min(
        MAXIMUM_ADAPTIVE_SURFACE_SAMPLE_EDGE_MM,
        max(SURFACE_SAMPLE_EDGE_MM, .75 * (maximum_p95 - NUMERICAL_DISTANCE_MM)),
    )
    for direction, mesh, reference in (
        ("candidate_to_source", candidate, source),
        ("source_to_candidate", source, candidate),
    ):
        changed_triangles, certificate = _exclude_covered_coplanar_triangles(
            _changed_triangles(mesh, reference), reference)
        coplanar_certificates.append(certificate)
        bounds.append(certificate["maximum_distance_bound_mm"])
        cells, sample_edge = _surface_cells_with_bounded_resolution(
            changed_triangles, maximum_sample_edge_mm=maximum_sample_edge)
        sampling_resolutions.append({"direction": direction, "sample_edge_mm": float(sample_edge),
                                     "adapted": bool(sample_edge > SURFACE_SAMPLE_EDGE_MM)})
        if not len(cells):
            continue
        centers = cells.mean(axis=1)
        distance = _nearest_surface_distance(reference, centers)
        radius = np.linalg.norm(cells - centers[:, None, :], axis=2).max(axis=1)
        area = trimesh.triangles.area(cells)
        changed = distance > radius + NUMERICAL_DISTANCE_MM
        # Keep only cells entirely off the reference surface. Excluded cells
        # have distance <= 2*radius everywhere. The bounded adaptive edge above
        # keeps that below the active P95 limit, so omitting them is conservative.
        # The remaining unchanged surface cannot dilute the repair percentile.
        distances.extend(distance[changed].tolist())
        areas.extend(area[changed].tolist())
        changed_bounds.extend((distance + radius)[changed].tolist())
        bounds.extend((distance + radius).tolist())
        sample_count += len(cells)
    if not distances:
        p95 = p95_upper = measured_max = 0.0
    else:
        order = np.argsort(distances)
        cumulative = np.cumsum(np.asarray(areas)[order])
        index = min(len(order) - 1, int(np.searchsorted(cumulative, cumulative[-1] * 0.95)))
        p95 = float(np.asarray(distances)[order[index]])
        upper_order = np.argsort(changed_bounds)
        upper_area = np.cumsum(np.asarray(areas)[upper_order])
        upper_index = min(len(upper_order) - 1, int(np.searchsorted(upper_area, upper_area[-1] * 0.95)))
        p95_upper = float(np.asarray(changed_bounds)[upper_order[upper_index]])
        measured_max = float(max(distances))
    return {"method": "bidirectional_changed_surface_area_weighted_deterministic_cells",
            "sample_edge_mm": float(max((item["sample_edge_mm"] for item in sampling_resolutions),
                                        default=SURFACE_SAMPLE_EDGE_MM)),
            "sampling_resolutions": sampling_resolutions, "sample_count": sample_count,
            "coplanar_coverage_certificates": coplanar_certificates,
            "changed_sample_count": len(distances), "p95_mm": p95,
            "p95_upper_bound_mm": p95_upper,
            "sampled_max_mm": measured_max,
            "maximum_upper_bound_mm": float(max(bounds, default=0.0))}


def inspect_geometry_fidelity(
    *, source: trimesh.Trimesh, candidate: trimesh.Trimesh,
    repair_volume: trimesh.Trimesh, budget: RepairBudget,
    protected_base_clearance_z_mm: float,
    critical_regions: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Fail closed on invalid geometry, unsafe deltas or unknown protections.

    Critical regions use world-space ``bounds_mm: [[x0,y0,z0],[x1,y1,z1]]``.
    Unlocalized semantic protection cannot safely be interpreted as empty.
    """
    checks: dict[str, bool] = {}
    report: dict[str, Any] = {"schema_version": "gpr-v1.3", "status": "blocked",
                              "checks": checks, "blockers": [], "policy": budget.to_dict()}
    report["policy"]["maximum_removed_volume_ratio"] = MAXIMUM_REMOVED_VOLUME_RATIO

    def finish():
        checks.update({key: bool(value) for key, value in checks.items()})
        report["blockers"] = [key for key, passed in checks.items() if not passed]
        report["status"] = "pass" if checks and not report["blockers"] else "blocked"
        return report

    try:
        source = normalized_boolean_copy(source)
        candidate = normalized_boolean_copy(candidate)
        repair_volume = normalized_boolean_copy(repair_volume)
    except (ValueError, TypeError, IndexError) as exc:
        checks["valid_mesh_arrays"] = False
        report["error"] = str(exc)
        return finish()
    for name, mesh in (("source", source), ("candidate", candidate), ("repair_volume", repair_volume)):
        report[name] = mesh_validation(mesh)
        checks[f"{name}_valid"] = report[name]["valid"]
    if not all(checks.values()):
        return finish()
    checks["single_connected_source"] = report["source"]["component_count"] == 1
    checks["component_count_preserved"] = report["candidate"]["component_count"] == report["source"]["component_count"]
    source_volume = float(source.volume)
    numerical_volume = max(1e-7, source_volume * 1e-7)
    try:
        common = _intersection_volume(source, candidate)
        added = max(0.0, float(candidate.volume) - common)
        removed = max(0.0, source_volume - common)
        report["volume"] = {"source_mm3": source_volume, "candidate_mm3": float(candidate.volume),
                            "intersection_mm3": common, "added_mm3": added, "removed_mm3": removed,
                            "added_ratio": added / source_volume, "removed_ratio": removed / source_volume}
        checks["actual_addition"] = added > numerical_volume
        checks["added_volume_within_budget"] = added / source_volume <= budget.maximum_modified_volume_ratio + 1e-12
        checks["removed_volume_within_budget"] = removed / source_volume <= MAXIMUM_REMOVED_VOLUME_RATIO + 1e-12
        # Added material must actually come from the declared repair envelope.
        envelope_in_candidate = _intersection_volume(candidate, repair_volume)
        shared_in_envelope = _intersection_volume(source, candidate, repair_volume)
        checks["addition_confined_to_envelope"] = abs(added - (envelope_in_candidate - shared_in_envelope)) <= numerical_volume
        checks["volumetric_source_anchor"] = _intersection_volume(source, repair_volume) > numerical_volume

        relative = np.abs(candidate.extents - source.extents) / source.extents
        report["bbox_dimension_change_ratio"] = relative.tolist()
        report["height_change_ratio"] = float(relative[2])
        checks["bbox_change_within_budget"] = bool(np.all(relative <= budget.maximum_bbox_dimension_change_ratio + 1e-12))
        checks["height_change_within_budget"] = relative[2] <= budget.maximum_height_change_ratio + 1e-12

        # Re-run the existing flat base gate, and compare the whole protected
        # slab using common volume so equal-volume rearrangement cannot pass.
        report["source_flat_base"] = inspect_flat_printing_base(source)
        report["candidate_flat_base"] = inspect_flat_printing_base(candidate)
        checks["source_flat_base_pass"] = report["source_flat_base"]["status"] == "pass"
        checks["candidate_flat_base_pass"] = report["candidate_flat_base"]["status"] == "pass"
        clearance = float(protected_base_clearance_z_mm)
        checks["protected_base_clearance_valid"] = bool(np.isfinite(clearance) and
                                                       clearance >= source.bounds[0, 2] + 0.60 - 1e-9)
        checks["envelope_above_protected_base"] = repair_volume.bounds[0, 2] >= clearance
        if not checks["protected_base_clearance_valid"]:
            return finish()
        slab_bounds = np.array([np.minimum(source.bounds[0], candidate.bounds[0]) - 1.0,
                                np.maximum(source.bounds[1], candidate.bounds[1]) + 1.0])
        slab_bounds[1, 2] = clearance
        slab = _box(slab_bounds)
        before = _intersection_volume(source, slab)
        after = _intersection_volume(candidate, slab)
        shared = _intersection_volume(source, candidate, slab)
        slab_delta = max(0.0, before + after - 2 * shared)
        report["protected_base_changed_volume_mm3"] = slab_delta
        checks["protected_base_unchanged"] = slab_delta <= numerical_volume
        checks["build_plate_position_preserved"] = abs(candidate.bounds[0, 2] - source.bounds[0, 2]) <= NUMERICAL_DISTANCE_MM

        checks["critical_regions_preserved"] = True
        report["critical_regions"] = []
        for region in critical_regions:
            bounds = np.asarray(region.get("bounds_mm"), dtype=float)
            valid = bounds.shape == (2, 3) and np.isfinite(bounds).all() and np.all(bounds[1] > bounds[0])
            if not valid:
                checks["critical_regions_preserved"] = False
                report["critical_regions"].append({"status": "blocked", "reason": "unlocalized_critical_region"})
                continue
            # Conservative: touching a declared protected box also blocks.
            intersects = bool(np.all(repair_volume.bounds[1] >= bounds[0]) and
                              np.all(repair_volume.bounds[0] <= bounds[1]))
            unchanged = True
            region_mesh = _box(bounds)
            a, b = _intersection_volume(source, region_mesh), _intersection_volume(candidate, region_mesh)
            ab = _intersection_volume(source, candidate, region_mesh)
            unchanged = max(0.0, a + b - 2 * ab) <= numerical_volume
            preserved = not intersects and unchanged
            report["critical_regions"].append({"bounds_mm": bounds.tolist(), "envelope_intersects": intersects,
                                                "geometry_unchanged": unchanged, "preserved": preserved})
            checks["critical_regions_preserved"] &= preserved

        # Expensive distance measurements are needed only for otherwise safe
        # candidates; rejected candidates never reach export.
        if all(checks.values()):
            displacement = _surface_displacement(
                source, candidate,
                maximum_surface_displacement_p95_mm=budget.maximum_surface_displacement_p95_mm)
            report["surface_displacement"] = displacement
            checks["surface_p95_within_budget"] = displacement["p95_upper_bound_mm"] <= budget.maximum_surface_displacement_p95_mm
            checks["surface_maximum_within_budget"] = displacement["maximum_upper_bound_mm"] <= budget.maximum_surface_displacement_mm
    except Exception as exc:
        checks["geometry_measurement_completed"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    # Convert numpy scalar comparisons for stable JSON serialization.
    checks.update({key: bool(value) for key, value in checks.items()})
    return finish()
