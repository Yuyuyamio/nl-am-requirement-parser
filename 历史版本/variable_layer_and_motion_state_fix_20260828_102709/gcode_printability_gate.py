from __future__ import annotations

import json
import math
import re
import statistics
import zipfile

from collections import defaultdict, deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from am_print_executor.gcode_support_continuity import (
    ExtrusionSegment,
    parse_extrusion_segments,
)


def _feature_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _is_support(feature: str) -> bool:
    return "support" in _feature_name(feature)


def _is_bridge(feature: str) -> bool:
    return "bridge" in _feature_name(feature)


def _is_bed_adhesion_or_process_move(feature: str) -> bool:
    value = _feature_name(feature)
    return any(
        marker in value
        for marker in (
            "brim",
            "skirt",
            "wipe tower",
            "prime tower",
            "custom",
        )
    )


def _is_model(feature: str) -> bool:
    return not (
        _is_support(feature)
        or _is_bed_adhesion_or_process_move(feature)
    )


def _requires_local_support(feature: str) -> bool:
    """Identify model paths whose individual cells require lower support."""

    value = _feature_name(feature)
    return (
        _is_model(value)
        and not _is_bridge(value)
        and "sparse infill" not in value
    )


def _float_value(settings: Mapping[str, Any], key: str, default: float) -> float:
    value = settings.get(key, default)
    if isinstance(value, list):
        value = value[0] if value else default
    try:
        text = str(value).strip()
        if text.endswith("%"):
            text = text[:-1]
        return float(text)
    except (TypeError, ValueError):
        return float(default)


def _segment_cells(
    segment: ExtrusionSegment,
    *,
    cell_mm: float,
) -> set[tuple[int, int]]:
    dx = segment.x2 - segment.x1
    dy = segment.y2 - segment.y1
    length = math.hypot(dx, dy)
    steps = max(1, int(math.ceil(length / max(cell_mm * 0.4, 0.05))))
    cells: set[tuple[int, int]] = set()
    for index in range(steps + 1):
        ratio = index / steps
        cells.add(
            (
                int(math.floor((segment.x1 + dx * ratio) / cell_mm)),
                int(math.floor((segment.y1 + dy * ratio) / cell_mm)),
            )
        )
    return cells


def _dilate(
    cells: set[tuple[int, int]],
    *,
    radius_mm: float,
    cell_mm: float,
) -> set[tuple[int, int]]:
    if not cells:
        return set()
    radius = max(0, int(math.ceil(radius_mm / cell_mm)))
    if radius == 0:
        return set(cells)
    output: set[tuple[int, int]] = set()
    limit_squared = radius * radius
    for x, y in cells:
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if dx * dx + dy * dy <= limit_squared:
                    output.add((x + dx, y + dy))
    return output


def _components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    remaining = set(cells)
    result: list[set[tuple[int, int]]] = []
    neighbourhood = (
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    )
    while remaining:
        seed = remaining.pop()
        component = {seed}
        queue = deque([seed])
        while queue:
            x, y = queue.popleft()
            for dx, dy in neighbourhood:
                neighbour = (x + dx, y + dy)
                if neighbour not in remaining:
                    continue
                remaining.remove(neighbour)
                component.add(neighbour)
                queue.append(neighbour)
        result.append(component)
    return result


def _component_area(component: set[tuple[int, int]], cell_mm: float) -> float:
    return len(component) * cell_mm * cell_mm


def _component_span(component: set[tuple[int, int]], cell_mm: float) -> float:
    if not component:
        return 0.0
    xs = [point[0] for point in component]
    ys = [point[1] for point in component]
    width = (max(xs) - min(xs) + 1) * cell_mm
    depth = (max(ys) - min(ys) + 1) * cell_mm
    return max(width, depth)


def _component_xy_bounds(
    component: set[tuple[int, int]],
    cell_mm: float,
) -> list[list[float]]:
    xs = [point[0] for point in component]
    ys = [point[1] for point in component]
    return [
        [min(xs) * cell_mm, min(ys) * cell_mm],
        [(max(xs) + 1) * cell_mm, (max(ys) + 1) * cell_mm],
    ]


def _point_to_segment_distance_xy(
    *,
    point_x: float,
    point_y: float,
    segment: ExtrusionSegment,
) -> float:
    """Continuous XY distance from a point to an extrusion centreline."""

    dx = float(segment.x2) - float(segment.x1)
    dy = float(segment.y2) - float(segment.y1)
    denominator = dx * dx + dy * dy

    if denominator <= 1e-15:
        return math.hypot(
            point_x - float(segment.x1),
            point_y - float(segment.y1),
        )

    ratio = (
        (
            (point_x - float(segment.x1)) * dx
            + (point_y - float(segment.y1)) * dy
        )
        / denominator
    )
    ratio = max(0.0, min(1.0, ratio))

    closest_x = float(segment.x1) + ratio * dx
    closest_y = float(segment.y1) + ratio * dy

    return math.hypot(
        point_x - closest_x,
        point_y - closest_y,
    )


def _continuous_bridge_has_two_model_anchors(
    component: set[tuple[int, int]],
    bridge_segments: list[ExtrusionSegment],
    previous_model_segments: list[ExtrusionSegment],
    *,
    cell_mm: float,
    support_radius_mm: float,
    component_span_mm: float,
    line_width_mm: float,
) -> bool:
    """Confirm a raster-ambiguous short bridge using continuous geometry.

    This is deliberately conservative.

    The normal raster test remains authoritative. This fallback is consulted
    only for a bridge that is already short enough to satisfy the configured
    bridge-span policy but whose raster contact count is below two.

    To prevent a tiny connector on one supported side from legitimising an
    otherwise one-sided bridge, at least one major bridge extrusion must span
    at least half of the raster component and both of that extrusion's real
    endpoints must lie within the same self-support radius used by the layer
    connectivity gate.

    Only reachable MODEL extrusion from prior layers is considered here.
    Support toolpaths are intentionally not used by this fallback.
    """

    if not component:
        return False

    if not bridge_segments:
        return False

    if not previous_model_segments:
        return False

    required_probe_length = max(
        float(line_width_mm),
        float(component_span_mm) * 0.50,
    )

    for segment in bridge_segments:
        segment_cells = _segment_cells(
            segment,
            cell_mm=cell_mm,
        )

        if not component.intersection(
            segment_cells
        ):
            continue

        segment_length = math.hypot(
            float(segment.x2) - float(segment.x1),
            float(segment.y2) - float(segment.y1),
        )

        if (
            segment_length + 1e-9
            < required_probe_length
        ):
            continue

        start_distance = min(
            _point_to_segment_distance_xy(
                point_x=float(segment.x1),
                point_y=float(segment.y1),
                segment=previous,
            )
            for previous
            in previous_model_segments
        )

        end_distance = min(
            _point_to_segment_distance_xy(
                point_x=float(segment.x2),
                point_y=float(segment.y2),
                segment=previous,
            )
            for previous
            in previous_model_segments
        )

        if (
            start_distance
            <= support_radius_mm + 1e-9
            and end_distance
            <= support_radius_mm + 1e-9
        ):
            return True

    return False


def _continuous_bridge_free_span_report(
    component: set[tuple[int, int]],
    bridge_segments: list[ExtrusionSegment],
    previous_model_segments: list[ExtrusionSegment],
    *,
    cell_mm: float,
    support_radius_mm: float,
    component_span_mm: float,
    line_width_mm: float,
    sample_step_mm: float = 0.02,
) -> dict[str, Any]:
    """Measure the real continuous unsupported span of a bridge component.

    A slicer may emit one large internal bridge region over sparse infill.
    The bridge component bounding box is therefore not necessarily a free-air
    bridge span. The physical quantity relevant to the configured bridge
    limit is the longest continuous part of an actual bridge extrusion that
    has no reachable prior-layer model material within the same local support
    radius used elsewhere by this gate.

    This function is deliberately conservative:
    - only already-reachable prior MODEL extrusion may anchor the bridge;
    - major bridge lines must be anchored at both real endpoints;
    - short connector moves do not legitimise a large one-sided bridge;
    - the existing maximum-safe-bridge-span policy remains unchanged.
    """

    if not component:
        return {
            "available": False,
            "max_continuous_free_span_mm": None,
            "major_segment_count": 0,
            "two_ended_major_segment_count": 0,
            "all_major_segments_two_ended": False,
        }

    relevant: list[ExtrusionSegment] = []

    for segment in bridge_segments:
        segment_cells = _segment_cells(
            segment,
            cell_mm=cell_mm,
        )

        if component.intersection(
            segment_cells
        ):
            relevant.append(
                segment
            )

    if not relevant:
        return {
            "available": False,
            "max_continuous_free_span_mm": None,
            "major_segment_count": 0,
            "two_ended_major_segment_count": 0,
            "all_major_segments_two_ended": False,
        }

    # Major bridge lines represent actual spanning strokes.
    # Small perimeter/connector fragments are intentionally excluded
    # from the two-ended-anchor requirement.
    major_threshold = max(
        float(line_width_mm) * 2.0,
        float(component_span_mm) * 0.25,
    )

    maximum_free_span = 0.0
    major_count = 0
    two_ended_major_count = 0

    def nearest_distance(
        point_x: float,
        point_y: float,
    ) -> float:

        if not previous_model_segments:
            return float("inf")

        return min(
            _point_to_segment_distance_xy(
                point_x=point_x,
                point_y=point_y,
                segment=previous,
            )
            for previous
            in previous_model_segments
        )

    for segment in relevant:
        dx = (
            float(segment.x2)
            - float(segment.x1)
        )

        dy = (
            float(segment.y2)
            - float(segment.y1)
        )

        length = math.hypot(
            dx,
            dy,
        )

        if length <= 1e-12:
            continue

        steps = max(
            1,
            int(
                math.ceil(
                    length
                    / max(
                        float(sample_step_mm),
                        0.005,
                    )
                )
            ),
        )

        actual_step = (
            length
            / steps
        )

        longest_free_run = 0
        current_free_run = 0

        for index in range(
            steps + 1
        ):
            ratio = (
                index
                / steps
            )

            x_value = (
                float(segment.x1)
                + dx * ratio
            )

            y_value = (
                float(segment.y1)
                + dy * ratio
            )

            supported = (
                nearest_distance(
                    x_value,
                    y_value,
                )
                <= support_radius_mm
                + 1e-9
            )

            if supported:
                current_free_run = 0

            if not supported:
                current_free_run += 1
                longest_free_run = max(
                    longest_free_run,
                    current_free_run,
                )

        # Multiplying by point count rather than count-1 is intentionally
        # slightly conservative by at most one sampling interval.
        free_span = (
            longest_free_run
            * actual_step
        )

        maximum_free_span = max(
            maximum_free_span,
            free_span,
        )

        if (
            length + 1e-9
            >= major_threshold
        ):
            major_count += 1

            start_supported = (
                nearest_distance(
                    float(segment.x1),
                    float(segment.y1),
                )
                <= support_radius_mm
                + 1e-9
            )

            end_supported = (
                nearest_distance(
                    float(segment.x2),
                    float(segment.y2),
                )
                <= support_radius_mm
                + 1e-9
            )

            if (
                start_supported
                and end_supported
            ):
                two_ended_major_count += 1

    return {
        "available": True,
        "max_continuous_free_span_mm": float(
            maximum_free_span
        ),
        "major_segment_count": int(
            major_count
        ),
        "two_ended_major_segment_count": int(
            two_ended_major_count
        ),
        "all_major_segments_two_ended": bool(
            major_count > 0
            and
            major_count
            == two_ended_major_count
        ),
    }


def _recent_cells(
    history: Mapping[float, set[tuple[int, int]]],
    *,
    below_z: float,
    maximum_gap_mm: float,
) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for z_value, cells in history.items():
        gap = below_z - z_value
        if 0.0 < gap <= maximum_gap_mm + 1e-6:
            result.update(cells)
    return result


def _component_features(
    component: set[tuple[int, int]],
    feature_cells: Mapping[str, set[tuple[int, int]]],
) -> list[str]:
    return sorted(
        feature
        for feature, cells in feature_cells.items()
        if component.intersection(cells)
    )


def inspect_mesh_topology(
    geometry_path: Path,
    *,
    build_plate_tolerance_mm: float = 0.25,
    minimum_build_plate_contact_mm2: float = 2.0,
) -> dict[str, Any]:
    """Conservative pre-slice topology check for the exact source geometry."""

    geometry_path = Path(geometry_path).resolve()
    blockers: list[str] = []
    if not geometry_path.is_file():
        return {
            "status": "blocked",
            "blockers": ["geometry_missing"],
            "geometry": str(geometry_path),
        }

    try:
        import trimesh

        loaded = trimesh.load(geometry_path, force="mesh", process=True)
        if isinstance(loaded, trimesh.Scene):
            meshes = [
                item
                for item in loaded.geometry.values()
                if isinstance(item, trimesh.Trimesh) and len(item.faces)
            ]
            if not meshes:
                raise ValueError("geometry contains no triangle mesh")
            mesh = trimesh.util.concatenate(meshes)
        elif isinstance(loaded, trimesh.Trimesh):
            mesh = loaded
        else:
            raise ValueError("geometry is not a triangle mesh")
    except Exception as exc:
        return {
            "status": "blocked",
            "blockers": [f"geometry_parse_failed:{type(exc).__name__}"],
            "geometry": str(geometry_path),
        }

    if len(mesh.faces) == 0:
        blockers.append("geometry_has_no_triangles")
    if not bool(mesh.is_watertight):
        blockers.append("mesh_not_watertight")

    bounds = mesh.bounds
    plate_z = float(bounds[0, 2])
    if abs(plate_z) > build_plate_tolerance_mm:
        blockers.append(f"geometry_is_not_on_build_plate:z={plate_z:.3f}")

    components = list(mesh.split(only_watertight=False))
    component_reports = []
    floating_components = 0
    for index, component in enumerate(components):
        minimum_z = float(component.bounds[0, 2])
        floating = minimum_z > plate_z + build_plate_tolerance_mm
        if floating:
            floating_components += 1
            blockers.append(
                "floating_mesh_component:"
                f"index={index}:min_z={minimum_z:.3f}"
            )
        component_reports.append(
            {
                "index": index,
                "face_count": int(len(component.faces)),
                "minimum_z_mm": minimum_z,
                "maximum_z_mm": float(component.bounds[1, 2]),
                "watertight": bool(component.is_watertight),
                "floating": floating,
            }
        )

    contact_area = 0.0
    if len(mesh.faces):
        triangles = mesh.triangles
        face_maximum_z = triangles[:, :, 2].max(axis=1)
        contact_mask = face_maximum_z <= plate_z + build_plate_tolerance_mm
        contact_area = float(mesh.area_faces[contact_mask].sum())
    if contact_area < minimum_build_plate_contact_mm2:
        blockers.append(
            "insufficient_mesh_build_plate_contact:"
            f"area={contact_area:.3f}mm2"
        )

    return {
        "status": "pass" if not blockers else "blocked",
        "blockers": blockers,
        "geometry": str(geometry_path),
        "face_count": int(len(mesh.faces)),
        "component_count": len(components),
        "floating_component_count": floating_components,
        "watertight": bool(mesh.is_watertight),
        "bounds_mm": bounds.tolist(),
        "build_plate_contact_area_mm2": contact_area,
        "components": component_reports[:100],
        "policy": {
            "build_plate_tolerance_mm": build_plate_tolerance_mm,
            "minimum_build_plate_contact_mm2": minimum_build_plate_contact_mm2,
        },
    }


def inspect_final_gcode_printability(
    artifact: Path,
    *,
    geometry_path: Path | None = None,
    cell_mm: float = 0.40,
    minimum_bad_component_mm2: float = 1.0,
    maximum_safe_bridge_span_mm: float = 8.0,
    minimum_build_plate_contact_mm2: float = 2.0,
) -> dict[str, Any]:
    """Validate physical FDM growth from the final sliced toolpath.

    A PASS means each model extrusion component can be reached from the build
    plate through prior model material, a vertically continuous support path,
    or a short bridge with two anchored sides. Unsupported regions are never
    discarded because their area is small.
    """

    artifact = Path(artifact).resolve()
    blockers: list[str] = []
    if not artifact.is_file():
        return {"status": "blocked", "blockers": ["artifact_missing"]}
    if artifact.stat().st_size <= 0:
        return {"status": "blocked", "blockers": ["artifact_empty"]}
    if not zipfile.is_zipfile(artifact):
        return {"status": "blocked", "blockers": ["artifact_not_zip"]}

    with zipfile.ZipFile(artifact, "r") as archive:
        names = archive.namelist()
        gcodes = sorted(
            name
            for name in names
            if re.fullmatch(r"Metadata/plate_\d+\.gcode", name)
        )
        if len(gcodes) != 1:
            return {"status": "blocked", "blockers": ["expected_one_plate"]}
        settings: dict[str, Any] = {}
        settings_name = "Metadata/project_settings.config"
        if settings_name in names:
            try:
                settings = json.loads(
                    archive.read(settings_name).decode("utf-8-sig")
                )
            except Exception as exc:
                return {
                    "status": "blocked",
                    "blockers": [f"project_settings_invalid:{type(exc).__name__}"],
                }
        gcode_text = archive.read(gcodes[0]).decode("utf-8", errors="replace")

    segments = parse_extrusion_segments(gcode_text)
    if not segments:
        return {"status": "blocked", "blockers": ["no_extrusion_segments"]}

    if geometry_path is None:
        topology = {
            "status": "blocked",
            "blockers": ["geometry_path_required"],
        }
    else:
        topology = inspect_mesh_topology(
            geometry_path,
            minimum_build_plate_contact_mm2=minimum_build_plate_contact_mm2,
        )
    blockers.extend(topology["blockers"])

    line_width = _float_value(settings, "line_width", 0.42)
    configured_layer_height = _float_value(settings, "layer_height", 0.20)
    support_top_gap = _float_value(
        settings,
        "support_top_z_distance",
        configured_layer_height,
    )
    support_bottom_gap = _float_value(
        settings,
        "support_bottom_z_distance",
        configured_layer_height,
    )
    support_xy_distance = _float_value(
        settings,
        "support_object_xy_distance",
        0.35,
    )

    by_z: dict[float, list[ExtrusionSegment]] = defaultdict(list)
    for segment in segments:
        by_z[round(float(segment.z), 4)].append(segment)
    layers = sorted(by_z)
    layer_differences = [
        upper - lower
        for lower, upper in zip(layers, layers[1:])
        if 0.01 < upper - lower < 1.0
    ]
    measured_layer_height = (
        statistics.median(layer_differences)
        if layer_differences
        else configured_layer_height
    )

    self_support_xy_mm = max(
        line_width * 0.65,
        measured_layer_height * 1.10,
    )
    support_growth_xy_mm = max(
        line_width * 1.5,
        measured_layer_height * 2.0,
    )
    support_contact_xy_mm = max(
        line_width,
        support_xy_distance + line_width * 0.50,
    )
    maximum_model_vertical_gap = measured_layer_height * 1.75 + 0.01
    maximum_support_step = measured_layer_height * 2.25 + 0.05
    maximum_support_vertical_gap = (
        support_top_gap + measured_layer_height * 1.25 + 0.05
    )
    maximum_support_origin_gap = (
        support_bottom_gap + measured_layer_height * 1.25 + 0.05
    )

    model_cells: dict[float, set[tuple[int, int]]] = {}
    support_cells: dict[float, set[tuple[int, int]]] = {}
    bridge_cells: dict[float, set[tuple[int, int]]] = {}
    locally_checked_cells: dict[float, set[tuple[int, int]]] = {}
    checked_feature_cells: dict[
        float, dict[str, set[tuple[int, int]]]
    ] = {}
    support_xy_length = 0.0
    feature_segment_counts: dict[str, int] = defaultdict(int)

    for z_value in layers:
        model: set[tuple[int, int]] = set()
        support: set[tuple[int, int]] = set()
        bridge: set[tuple[int, int]] = set()
        checked: set[tuple[int, int]] = set()
        features: dict[str, set[tuple[int, int]]] = defaultdict(set)
        for segment in by_z[z_value]:
            feature = _feature_name(segment.feature)
            feature_segment_counts[feature] += 1
            cells = _segment_cells(segment, cell_mm=cell_mm)
            if _is_support(feature):
                support.update(cells)
                support_xy_length += math.hypot(
                    segment.x2 - segment.x1,
                    segment.y2 - segment.y1,
                )
                continue
            if not _is_model(feature):
                continue
            model.update(cells)
            if _is_bridge(feature):
                bridge.update(cells)
            elif _requires_local_support(feature):
                checked.update(cells)
                features[feature].update(cells)
        model_cells[z_value] = model
        support_cells[z_value] = support
        bridge_cells[z_value] = bridge
        locally_checked_cells[z_value] = checked
        checked_feature_cells[z_value] = dict(features)

    model_layers = [z for z in layers if model_cells[z]]
    if not model_layers:
        blockers.append("no_model_extrusion")
        bed_model_z = 0.0
        build_plate_contact_area = 0.0
    else:
        bed_model_z = model_layers[0]
        build_plate_contact_area = _component_area(
            model_cells[bed_model_z],
            cell_mm,
        )
        if build_plate_contact_area < minimum_build_plate_contact_mm2:
            blockers.append(
                "insufficient_toolpath_build_plate_contact:"
                f"area={build_plate_contact_area:.3f}mm2"
            )

    first_extrusion_z = layers[0]
    reachable_model: dict[float, set[tuple[int, int]]] = {}
    reachable_support: dict[float, set[tuple[int, int]]] = {}
    dangerous_layers: list[dict[str, Any]] = []
    unsupported_area_total = 0.0
    worst_unsupported_area = 0.0
    longest_bad_bridge = 0.0
    unsupported_island_count = 0
    unanchored_support_count = 0
    continuous_bridge_anchor_rescue_count = 0
    continuous_bridge_free_span_rescue_count = 0

    for z_value in layers:
        previous_support = _recent_cells(
            reachable_support,
            below_z=z_value,
            maximum_gap_mm=maximum_support_step,
        )
        support_origin_model = _recent_cells(
            reachable_model,
            below_z=z_value,
            maximum_gap_mm=maximum_support_origin_gap,
        )
        support_anchor_zone = _dilate(
            previous_support | support_origin_model,
            radius_mm=support_growth_xy_mm,
            cell_mm=cell_mm,
        )
        anchored_support: set[tuple[int, int]] = set()
        layer_unanchored_support: list[set[tuple[int, int]]] = []
        for component in _components(support_cells[z_value]):
            on_plate = z_value <= first_extrusion_z + measured_layer_height * 0.25
            if on_plate or component.intersection(support_anchor_zone):
                anchored_support.update(component)
            else:
                layer_unanchored_support.append(component)
        reachable_support[z_value] = anchored_support

        previous_model = _recent_cells(
            reachable_model,
            below_z=z_value,
            maximum_gap_mm=maximum_model_vertical_gap,
        )
        model_support_zone = _dilate(
            previous_model,
            radius_mm=self_support_xy_mm,
            cell_mm=cell_mm,
        )
        nearby_support = _recent_cells(
            reachable_support,
            below_z=z_value,
            maximum_gap_mm=maximum_support_vertical_gap,
        )
        physical_support_zone = _dilate(
            nearby_support,
            radius_mm=support_contact_xy_mm,
            cell_mm=cell_mm,
        )
        accepted_zone = model_support_zone | physical_support_zone
        model_on_plate = (
            bool(model_layers)
            and z_value <= bed_model_z + measured_layer_height * 0.25
        )
        if model_on_plate:
            accepted_zone.update(model_cells[z_value])

        anchored_model: set[tuple[int, int]] = set()
        layer_islands: list[set[tuple[int, int]]] = []
        for component in _components(model_cells[z_value]):
            if model_on_plate or component.intersection(accepted_zone):
                anchored_model.update(component)
            else:
                layer_islands.append(component)
        reachable_model[z_value] = anchored_model

        unsupported_local = (
            locally_checked_cells[z_value]
            - accepted_zone
            - bridge_cells[z_value]
        )
        local_components = _components(unsupported_local)

        # Continuous prior-layer MODEL paths are retained only for a
        # conservative second opinion when rasterisation makes a short
        # bridge appear to have fewer than two anchors.
        previous_model_segments_continuous: list[ExtrusionSegment] = []

        for previous_z, previous_reachable in reachable_model.items():
            gap = z_value - previous_z

            if not (
                0.0
                < gap
                <= maximum_model_vertical_gap + 1e-6
            ):
                continue

            if not previous_reachable:
                continue

            for segment in by_z.get(previous_z, []):
                feature = _feature_name(segment.feature)

                if not _is_model(feature):
                    continue

                segment_cells = _segment_cells(
                    segment,
                    cell_mm=cell_mm,
                )

                if segment_cells.intersection(
                    previous_reachable
                ):
                    previous_model_segments_continuous.append(
                        segment
                    )

        current_bridge_segments = [
            segment
            for segment in by_z[z_value]
            if _is_bridge(
                _feature_name(
                    segment.feature
                )
            )
        ]

        bad_bridges: list[
            tuple[
                set[tuple[int, int]],
                float,
                int,
            ]
        ] = []

        for component in _components(
            bridge_cells[z_value]
        ):
            unsupported_bridge = (
                component
                - accepted_zone
            )

            if not unsupported_bridge:
                continue

            component_span = (
                _component_span(
                    component,
                    cell_mm,
                )
            )

            contact_count = len(
                _components(
                    component
                    & accepted_zone
                )
            )

            bridge_is_safe = False
            reported_bad_span = (
                component_span
            )

            # Existing rule remains first:
            # a conventionally short bridge with two raster anchors passes.
            if (
                component_span
                <= maximum_safe_bridge_span_mm
                and contact_count >= 2
            ):
                bridge_is_safe = True

            # Fix #1:
            # raster alias may merge/drop one anchor on an otherwise
            # genuinely short, two-ended bridge.
            if (
                component_span
                <= maximum_safe_bridge_span_mm
                and contact_count < 2
            ):
                bridge_is_safe = (
                    _continuous_bridge_has_two_model_anchors(
                        component,
                        current_bridge_segments,
                        previous_model_segments_continuous,
                        cell_mm=cell_mm,
                        support_radius_mm=self_support_xy_mm,
                        component_span_mm=component_span,
                        line_width_mm=line_width,
                    )
                )

                if bridge_is_safe:
                    continuous_bridge_anchor_rescue_count += 1

            # Fix #2:
            # For a large slicer "bridge" region, the bounding-box span
            # may include many sparse-infill-supported subspans. Measure
            # the longest actual consecutive unsupported run instead.
            #
            # The 8mm policy itself is NOT relaxed.
            if (
                component_span
                > maximum_safe_bridge_span_mm
            ):
                free_span_report = (
                    _continuous_bridge_free_span_report(
                        component,
                        current_bridge_segments,
                        previous_model_segments_continuous,
                        cell_mm=cell_mm,
                        support_radius_mm=self_support_xy_mm,
                        component_span_mm=component_span,
                        line_width_mm=line_width,
                    )
                )

                effective_span = (
                    free_span_report.get(
                        "max_continuous_free_span_mm"
                    )
                )

                if effective_span is not None:
                    reported_bad_span = float(
                        effective_span
                    )

                bridge_is_safe = bool(
                    free_span_report.get(
                        "available"
                    )
                    and
                    free_span_report.get(
                        "all_major_segments_two_ended"
                    )
                    and
                    effective_span is not None
                    and
                    float(effective_span)
                    <= maximum_safe_bridge_span_mm
                    + 1e-9
                )

                if bridge_is_safe:
                    continuous_bridge_free_span_rescue_count += 1

            if not bridge_is_safe:
                bad_bridges.append(
                    (
                        unsupported_bridge,
                        reported_bad_span,
                        contact_count,
                    )
                )

                longest_bad_bridge = max(
                    longest_bad_bridge,
                    reported_bad_span,
                )

        layer_records: list[dict[str, Any]] = []
        for component in layer_islands:
            area = _component_area(component, cell_mm)
            unsupported_island_count += 1
            unsupported_area_total += area
            worst_unsupported_area = max(worst_unsupported_area, area)
            features = _component_features(
                component,
                checked_feature_cells[z_value],
            )
            blockers.append(
                "unsupported_layer_island:"
                f"z={z_value:.3f}:area={area:.3f}mm2"
            )
            layer_records.append(
                {
                    "kind": "unsupported_layer_island",
                    "area_mm2": area,
                    "xy_bounds_mm": _component_xy_bounds(component, cell_mm),
                    "features": features,
                }
            )

        for component in local_components:
            area = _component_area(component, cell_mm)
            unsupported_area_total += area
            worst_unsupported_area = max(worst_unsupported_area, area)
            features = _component_features(
                component,
                checked_feature_cells[z_value],
            )
            blockers.append(
                "unsupported_extrusion_region:"
                f"z={z_value:.3f}:area={area:.3f}mm2"
            )
            layer_records.append(
                {
                    "kind": "unsupported_extrusion_region",
                    "area_mm2": area,
                    "xy_bounds_mm": _component_xy_bounds(component, cell_mm),
                    "features": features,
                }
            )

        for component, span, contact_count in bad_bridges:
            area = _component_area(component, cell_mm)
            blockers.append(
                "unsafe_bridge:"
                f"z={z_value:.3f}:span={span:.3f}mm:"
                f"contacts={contact_count}"
            )
            layer_records.append(
                {
                    "kind": "unsafe_bridge",
                    "area_mm2": area,
                    "span_mm": span,
                    "xy_bounds_mm": _component_xy_bounds(component, cell_mm),
                    "anchored_contact_count": contact_count,
                    "features": ["bridge"],
                }
            )

        for component in layer_unanchored_support:
            area = _component_area(component, cell_mm)
            unanchored_support_count += 1
            blockers.append(
                "unanchored_support_toolpath:"
                f"z={z_value:.3f}:area={area:.3f}mm2"
            )
            layer_records.append(
                {
                    "kind": "unanchored_support_toolpath",
                    "area_mm2": area,
                    "xy_bounds_mm": _component_xy_bounds(component, cell_mm),
                    "features": ["support"],
                }
            )

        if layer_records:
            dangerous_layers.append(
                {
                    "z_mm": z_value,
                    "previous_model_cell_count": len(previous_model),
                    "reachable_support_cell_count": len(nearby_support),
                    "issues": layer_records[:50],
                }
            )

    unique_blockers = list(dict.fromkeys(blockers))
    status = "pass" if not unique_blockers else "blocked"
    result = {
        "status": status,
        "blockers": unique_blockers[:500],
        "artifact": str(artifact),
        "geometry": str(Path(geometry_path).resolve()) if geometry_path else None,
        "gcode_entry": gcodes[0],
        "layer_count": len(layers),
        "model_layer_count": len(model_layers),
        "segment_count": len(segments),
        "feature_segment_counts": dict(sorted(feature_segment_counts.items())),
        "support_xy_length_mm": support_xy_length,
        "build_plate_contact_area_mm2": build_plate_contact_area,
        "unsupported_layer_island_count": unsupported_island_count,
        "unanchored_support_component_count": unanchored_support_count,
        "total_bad_area_mm2": unsupported_area_total,
        "worst_bad_area_mm2": worst_unsupported_area,
        "longest_bad_bridge_mm": longest_bad_bridge,
        "continuous_bridge_anchor_rescue_count": (
            continuous_bridge_anchor_rescue_count
        ),
        "continuous_bridge_free_span_rescue_count": (
            continuous_bridge_free_span_rescue_count
        ),
        "dangerous_layer_count": len(dangerous_layers),
        "dangerous_layers": dangerous_layers[:100],
        "mesh_topology": topology,
        "policy": {
            "gate_semantics": "layer_support_connectivity_v2",
            "cell_mm": cell_mm,
            "line_width_mm": line_width,
            "configured_layer_height_mm": configured_layer_height,
            "measured_layer_height_mm": measured_layer_height,
            "self_support_xy_mm": self_support_xy_mm,
            "support_growth_xy_mm": support_growth_xy_mm,
            "support_top_z_distance_mm": support_top_gap,
            "support_bottom_z_distance_mm": support_bottom_gap,
            "maximum_support_vertical_gap_mm": maximum_support_vertical_gap,
            "support_contact_xy_mm": support_contact_xy_mm,
            "maximum_safe_bridge_span_mm": maximum_safe_bridge_span_mm,
            "minimum_build_plate_contact_mm2": minimum_build_plate_contact_mm2,
            "unsupported_component_area_exemption_mm2": 0.0,
            "legacy_minimum_bad_component_mm2_ignored": minimum_bad_component_mm2,
        },
    }
    if status == "blocked":
        result["next_module"] = "M2_REPAIR"
        result["resolution"] = "needs_geometry_regeneration"
        result["feedback_to_m2"] = {
            "reason": (
                "Final sliced extrusion contains geometry or support that "
                "cannot be reached from the build plate layer by layer."
            ),
            "required_changes": [
                "connect floating or isolated geometry to printable material",
                "reduce unsupported cantilevers or add reachable support",
                "ensure every support path is itself connected to the build plate",
            ],
            "dangerous_layers": dangerous_layers[:25],
        }
    else:
        result["next_module"] = "M4"
        result["resolution"] = "physical_printability_verified"
    return result
