from __future__ import annotations

import math

from pathlib import Path
from typing import Any

import numpy as np
import trimesh


DEFAULT_LAYER_HEIGHT_MM = 0.2
DEFAULT_CONTACT_TOLERANCE_MM = 0.001
DEFAULT_MAX_PLANE_DEVIATION_MM = 0.15
DEFAULT_MINIMUM_CONTACT_AREA_MM2 = 2.0
DEFAULT_MINIMUM_FOOTPRINT_RATIO = 0.01
DEFAULT_MINIMUM_DOMINANT_PATCH_RATIO = 0.8
DEFAULT_MAXIMUM_PLANE_TILT_DEGREES = 2.0
DEFAULT_MINIMUM_STABILITY_MARGIN_MM = 0.8


class FlatBaseGateError(RuntimeError):
    def __init__(self, message: str, *, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def _coerce_mesh(
    geometry: str | Path | trimesh.Trimesh,
) -> tuple[trimesh.Trimesh, str | None]:
    if isinstance(geometry, trimesh.Trimesh):
        mesh = geometry.copy()
        source = None
    else:
        path = Path(geometry).expanduser().resolve()
        if not path.is_file():
            raise FlatBaseGateError(
                f"Flat-base geometry is missing: {path}",
                report={
                    "status": "block",
                    "base_flatness_passed": False,
                    "blockers": ["geometry_missing"],
                    "geometry": str(path),
                },
            )
        loaded = trimesh.load(path, force="scene", process=True)
        mesh = (
            loaded.to_mesh()
            if isinstance(loaded, trimesh.Scene)
            else loaded
        )
        source = str(path)

    if not isinstance(mesh, trimesh.Trimesh):
        raise FlatBaseGateError(
            "Flat-base geometry did not resolve to one mesh.",
            report={
                "status": "block",
                "base_flatness_passed": False,
                "blockers": ["invalid_geometry"],
                "geometry": source,
            },
        )
    # STL commonly duplicates every triangle vertex. Merge only on the local
    # copy so face adjacency represents a physical patch rather than hundreds
    # of artificial one-face fragments.
    if hasattr(mesh, "merge_vertices"):
        mesh.merge_vertices()
    if hasattr(mesh, "remove_unreferenced_vertices"):
        mesh.remove_unreferenced_vertices()
    if len(mesh.vertices) < 3 or len(mesh.faces) < 1:
        raise FlatBaseGateError(
            "Flat-base geometry is empty.",
            report={
                "status": "block",
                "base_flatness_passed": False,
                "blockers": ["empty_geometry"],
                "geometry": source,
            },
        )
    return mesh, source


def _positive_finite(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def _face_projected_areas(mesh: trimesh.Trimesh) -> np.ndarray:
    triangles = np.asarray(mesh.vertices)[np.asarray(mesh.faces)]
    first = triangles[:, 1, :2] - triangles[:, 0, :2]
    second = triangles[:, 2, :2] - triangles[:, 0, :2]
    return np.abs(
        first[:, 0] * second[:, 1]
        - first[:, 1] * second[:, 0]
    ) * 0.5


def _contact_components(
    mesh: trimesh.Trimesh,
    face_indices: np.ndarray,
) -> list[list[int]]:
    selected = {int(index) for index in face_indices.tolist()}
    neighbours: dict[int, set[int]] = {
        index: set() for index in selected
    }
    for first, second in np.asarray(mesh.face_adjacency, dtype=int):
        a = int(first)
        b = int(second)
        if a in selected and b in selected:
            neighbours[a].add(b)
            neighbours[b].add(a)

    components: list[list[int]] = []
    remaining = set(selected)
    while remaining:
        seed = remaining.pop()
        component = [seed]
        stack = [seed]
        while stack:
            current = stack.pop()
            for neighbour in neighbours[current]:
                if neighbour not in remaining:
                    continue
                remaining.remove(neighbour)
                component.append(neighbour)
                stack.append(neighbour)
        components.append(component)
    return components


def _fit_plane(
    vertices: np.ndarray,
) -> tuple[dict[str, Any] | None, float | None, float | None]:
    if len(vertices) < 3:
        return None, None, None
    design = np.column_stack(
        (vertices[:, 0], vertices[:, 1], np.ones(len(vertices)))
    )
    coefficients, _, _, _ = np.linalg.lstsq(
        design,
        vertices[:, 2],
        rcond=None,
    )
    slope_x, slope_y, intercept = (
        float(value) for value in coefficients
    )
    normal = np.asarray((-slope_x, -slope_y, 1.0), dtype=float)
    normal /= float(np.linalg.norm(normal))
    offset = -intercept * float(normal[2])
    deviations = np.abs(vertices @ normal + offset)
    max_deviation = float(deviations.max())
    tilt = math.degrees(
        math.acos(max(-1.0, min(1.0, float(normal[2]))))
    )
    return (
        {
            "normal": [float(value) for value in normal],
            "offset": float(offset),
            "z_equals": {
                "x_coefficient": slope_x,
                "y_coefficient": slope_y,
                "intercept": intercept,
            },
        },
        max_deviation,
        tilt,
    )


def inspect_flat_printing_base(
    geometry: str | Path | trimesh.Trimesh,
    *,
    contact_tolerance_mm: float = DEFAULT_CONTACT_TOLERANCE_MM,
    maximum_plane_deviation_mm: float = DEFAULT_MAX_PLANE_DEVIATION_MM,
    minimum_contact_area_mm2: float = DEFAULT_MINIMUM_CONTACT_AREA_MM2,
    minimum_footprint_ratio: float = DEFAULT_MINIMUM_FOOTPRINT_RATIO,
    minimum_dominant_patch_ratio: float = (
        DEFAULT_MINIMUM_DOMINANT_PATCH_RATIO
    ),
    maximum_plane_tilt_degrees: float = (
        DEFAULT_MAXIMUM_PLANE_TILT_DEGREES
    ),
    minimum_stability_margin_mm: float = DEFAULT_MINIMUM_STABILITY_MARGIN_MM,
) -> dict[str, Any]:
    """Measure whether the current Z-min surface is a real printing plane.

    Only downward-facing triangle area within 1 micron of the lowest Z
    counts as bed contact. Points and edges therefore cannot impersonate a
    printable base. Contact triangles must also form one dominant connected
    patch whose fitted plane is horizontal and within the FDM tolerance.
    """

    contact_tolerance = _positive_finite(
        contact_tolerance_mm,
        "contact_tolerance_mm",
    )
    plane_tolerance = _positive_finite(
        maximum_plane_deviation_mm,
        "maximum_plane_deviation_mm",
    )
    minimum_area = _positive_finite(
        minimum_contact_area_mm2,
        "minimum_contact_area_mm2",
    )
    footprint_ratio = float(minimum_footprint_ratio)
    dominant_ratio_required = float(minimum_dominant_patch_ratio)
    maximum_tilt = _positive_finite(
        maximum_plane_tilt_degrees,
        "maximum_plane_tilt_degrees",
    )
    stability_margin = _positive_finite(minimum_stability_margin_mm, "minimum_stability_margin_mm")
    if not 0 <= footprint_ratio <= 1:
        raise ValueError("minimum_footprint_ratio must be between 0 and 1")
    if not 0 < dominant_ratio_required <= 1:
        raise ValueError(
            "minimum_dominant_patch_ratio must be greater than 0 and at most 1"
        )

    mesh, source = _coerce_mesh(geometry)
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=int)
    bounds = np.asarray(mesh.bounds, dtype=float)
    extents = np.asarray(mesh.extents, dtype=float)
    minimum_z = float(bounds[0, 2])
    footprint_area = float(extents[0] * extents[1])
    required_area = max(minimum_area, footprint_area * footprint_ratio)

    face_z = vertices[faces, 2]
    projected_areas = _face_projected_areas(mesh)
    contact_mask = (
        np.max(np.abs(face_z - minimum_z), axis=1)
        <= contact_tolerance + 1e-9
    ) & (projected_areas > 1e-9) & (mesh.face_normals[:, 2] < -0.999)
    contact_indices = np.flatnonzero(contact_mask)
    raw_components = _contact_components(mesh, contact_indices)
    component_records: list[dict[str, Any]] = []
    for component in raw_components:
        indices = np.asarray(component, dtype=int)
        area = float(projected_areas[indices].sum())
        if area <= 1e-8:
            continue
        component_records.append(
            {
                "face_indices": component,
                "area_mm2": area,
            }
        )
    component_records.sort(
        key=lambda item: float(item["area_mm2"]),
        reverse=True,
    )

    contact_area = float(
        sum(float(item["area_mm2"]) for item in component_records)
    )
    largest_area = (
        float(component_records[0]["area_mm2"])
        if component_records
        else 0.0
    )
    dominant_ratio = (
        largest_area / contact_area
        if contact_area > 0
        else 0.0
    )
    fitted_plane = None
    max_plane_deviation = None
    plane_tilt = None
    base_height_range = None
    center_of_mass = np.asarray(mesh.center_mass, dtype=float)
    stability = {"center_of_mass_xy_mm": center_of_mass[:2].tolist(),
                 "center_of_mass_inside_support_polygon": False,
                 "stability_margin_mm": 0.0,
                 "required_stability_margin_mm": max(stability_margin, float(extents[2]) * 0.03)}
    if component_records:
        largest_faces = np.asarray(
            component_records[0]["face_indices"],
            dtype=int,
        )
        largest_vertices = vertices[
            np.unique(faces[largest_faces].reshape(-1))
        ]
        fitted_plane, max_plane_deviation, plane_tilt = _fit_plane(
            largest_vertices
        )
        base_height_range = float(
            largest_vertices[:, 2].max()
            - largest_vertices[:, 2].min()
        )
        from shapely.geometry import MultiPoint, Point
        # A ring-shaped base remains stable over its hole: static stability
        # uses the convex support polygon, not filled contact at the COM.
        support_polygon = MultiPoint(largest_vertices[:, :2]).convex_hull
        com_point = Point(center_of_mass[:2])
        inside = bool(np.isfinite(center_of_mass).all() and support_polygon.covers(com_point))
        distance = float(support_polygon.boundary.distance(com_point))
        stability.update(center_of_mass_inside_support_polygon=inside,
                         stability_margin_mm=distance if inside else -distance)

    blockers: list[str] = []
    if not np.isfinite(center_of_mass).all() or not mesh.is_volume:
        blockers.append("stable_base_requires_valid_solid_mass")
    if not stability["center_of_mass_inside_support_polygon"]:
        blockers.append("center_of_mass_outside_support_polygon")
    elif stability["stability_margin_mm"] < stability["required_stability_margin_mm"]:
        blockers.append("insufficient_base_stability_margin")
    if not component_records:
        blockers.append("flat_base_contact_face_required")
    if contact_area + 1e-9 < required_area:
        blockers.append(
            "insufficient_bed_contact_area:"
            f"actual={contact_area:.3f}mm2:required={required_area:.3f}mm2"
        )
    if largest_area + 1e-9 < required_area:
        blockers.append(
            "dominant_contact_patch_too_small:"
            f"actual={largest_area:.3f}mm2:required={required_area:.3f}mm2"
        )
    if component_records and dominant_ratio + 1e-9 < dominant_ratio_required:
        blockers.append(
            "fragmented_bed_contact:"
            f"patches={len(component_records)}:"
            f"dominant_ratio={dominant_ratio:.3f}"
        )
    if (
        max_plane_deviation is None
        or max_plane_deviation > plane_tolerance + 1e-9
    ):
        blockers.append(
            "base_plane_deviation_exceeded:"
            f"actual={max_plane_deviation}:required<={plane_tolerance:.3f}mm"
        )
    if plane_tilt is None or plane_tilt > maximum_tilt + 1e-9:
        blockers.append(
            "base_plane_tilt_exceeded:"
            f"actual={plane_tilt}:required<={maximum_tilt:.3f}deg"
        )

    passed = not blockers
    return {
        "status": "pass" if passed else "block",
        "base_flatness_passed": passed,
        "blockers": blockers,
        "geometry": source,
        "bed_contact_area_mm2": contact_area,
        "required_contact_area_mm2": required_area,
        "largest_contact_patch_area_mm2": largest_area,
        "contact_patch_count": len(component_records),
        "dominant_contact_patch_ratio": dominant_ratio,
        "fitted_base_plane": fitted_plane,
        "max_plane_deviation_mm": max_plane_deviation,
        "base_height_range_mm": base_height_range,
        "base_plane_tilt_degrees": plane_tilt,
        "stability": stability,
        "minimum_z_mm": minimum_z,
        "footprint_area_mm2": footprint_area,
        "final_extents_mm": [float(value) for value in extents],
        "policy": {
            "contact_tolerance_mm": contact_tolerance,
            "maximum_plane_deviation_mm": plane_tolerance,
            "minimum_contact_area_mm2": minimum_area,
            "minimum_footprint_ratio": footprint_ratio,
            "minimum_dominant_patch_ratio": dominant_ratio_required,
            "maximum_plane_tilt_degrees": maximum_tilt,
            "minimum_stability_margin_mm": stability_margin,
        },
    }


def ensure_flat_printing_base(
    mesh: trimesh.Trimesh,
    *,
    layer_height_mm: float = DEFAULT_LAYER_HEIGHT_MM,
    maximum_clip_depth_mm: float | None = None,
) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    """Preserve an existing flat base or minimally clip and cap a curved one."""

    layer_height = _positive_finite(layer_height_mm, "layer_height_mm")
    source, _ = _coerce_mesh(mesh)
    before = inspect_flat_printing_base(source)
    if before["base_flatness_passed"]:
        return source, {
            "status": "preserved",
            "applied": False,
            "clip_depth_mm": 0.0,
            "before": before,
            "after": before,
        }

    height = float(source.extents[2])
    if maximum_clip_depth_mm is None:
        requested_maximum = max(layer_height * 3.0, height * 0.03)
        maximum_depth = min(requested_maximum, height * 0.05, 1.5)
    else:
        maximum_depth = _positive_finite(
            maximum_clip_depth_mm,
            "maximum_clip_depth_mm",
        )
    step = max(0.05, layer_height * 0.5)
    minimum_z = float(source.bounds[0, 2])
    last_report = before
    depth = step
    while depth <= maximum_depth + 1e-9:
        try:
            candidate = trimesh.intersections.slice_mesh_plane(
                source,
                plane_normal=(0.0, 0.0, 1.0),
                plane_origin=(0.0, 0.0, minimum_z + depth),
                cap=True,
            )
        except Exception:
            depth += step
            continue
        if not isinstance(candidate, trimesh.Trimesh) or len(candidate.faces) < 1:
            depth += step
            continue
        candidate.apply_translation(
            (0.0, 0.0, -float(candidate.bounds[0, 2]))
        )
        last_report = inspect_flat_printing_base(candidate)
        if last_report["base_flatness_passed"]:
            return candidate, {
                "status": "planarized",
                "applied": True,
                "method": "minimal_z_clip_with_planar_cap",
                "clip_depth_mm": float(depth),
                "maximum_clip_depth_mm": float(maximum_depth),
                "source_watertight": bool(source.is_watertight),
                "result_watertight": bool(candidate.is_watertight),
                "before": before,
                "after": last_report,
            }
        depth += step

    raise FlatBaseGateError(
        "FLAT_BASE_GATE = BLOCK: a safe dominant planar patch could not be "
        "created within the permitted local clip depth.",
        report={
            "status": "block",
            "base_flatness_passed": False,
            "blockers": list(last_report.get("blockers", [])),
            "maximum_clip_depth_mm": float(maximum_depth),
            "before": before,
            "last_attempt": last_report,
        },
    )
