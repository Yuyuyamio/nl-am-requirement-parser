from __future__ import annotations

import json
import math
import re
import statistics
import zipfile

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from am_print_executor.gcode_support_continuity import (
    ExtrusionSegment,
    parse_extrusion_segments,
)


def _feature_name(value: str) -> str:
    return " ".join(
        value.strip().lower().split()
    )


def _is_support(feature: str) -> bool:
    return "support" in _feature_name(feature)


def _is_bridge(feature: str) -> bool:
    return "bridge" in _feature_name(feature)


def _is_risky_model(feature: str) -> bool:
    f = _feature_name(feature)

    if _is_support(f):
        return False

    if _is_bridge(f):
        return False

    # Sparse infill moves between layers by design and is not
    # suitable for local support-continuity validation.
    if "sparse infill" in f:
        return False

    # Shell / underside features are authoritative for
    # detecting floating geometry.
    keywords = (
        "outer wall",
        "external perimeter",
        "inner wall",
        "overhang",
        "bottom",
        "solid infill",
        "solid surface",
        "gap infill",
    )

    return any(
        key in f
        for key in keywords
    )


def _float_value(
    settings: dict[str, Any],
    key: str,
    default: float,
) -> float:

    value = settings.get(
        key,
        default,
    )

    if isinstance(value, list):
        if not value:
            return float(default)
        value = value[0]

    try:
        text = str(value).strip()

        if text.endswith("%"):
            text = text[:-1]

        return float(text)

    except Exception:
        return float(default)


def _segment_cells(
    segment: ExtrusionSegment,
    *,
    cell_mm: float,
) -> set[tuple[int, int]]:

    dx = (
        segment.x2
        - segment.x1
    )

    dy = (
        segment.y2
        - segment.y1
    )

    length = math.hypot(
        dx,
        dy,
    )

    steps = max(
        1,
        int(
            math.ceil(
                length
                / max(
                    cell_mm * 0.4,
                    0.05,
                )
            )
        ),
    )

    cells = set()

    for index in range(
        steps + 1
    ):
        t = index / steps

        x = (
            segment.x1
            + dx * t
        )

        y = (
            segment.y1
            + dy * t
        )

        cells.add(
            (
                int(
                    math.floor(
                        x / cell_mm
                    )
                ),
                int(
                    math.floor(
                        y / cell_mm
                    )
                ),
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

    radius = max(
        0,
        int(
            math.ceil(
                radius_mm
                / cell_mm
            )
        ),
    )

    if radius == 0:
        return set(cells)

    out = set()

    limit_sq = radius * radius

    for x, y in cells:
        for dx in range(
            -radius,
            radius + 1,
        ):
            for dy in range(
                -radius,
                radius + 1,
            ):
                if (
                    dx * dx
                    + dy * dy
                    <= limit_sq
                ):
                    out.add(
                        (
                            x + dx,
                            y + dy,
                        )
                    )

    return out


def _components(
    cells: set[tuple[int, int]],
) -> list[set[tuple[int, int]]]:

    remaining = set(cells)

    result = []

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

        q = deque([seed])

        while q:

            x, y = q.popleft()

            for dx, dy in neighbourhood:

                n = (
                    x + dx,
                    y + dy,
                )

                if n not in remaining:
                    continue

                remaining.remove(n)

                component.add(n)

                q.append(n)

        result.append(component)

    return result


def _component_area(
    component: set[tuple[int, int]],
    cell_mm: float,
) -> float:

    return (
        len(component)
        * cell_mm
        * cell_mm
    )


def _component_span(
    component: set[tuple[int, int]],
    cell_mm: float,
) -> float:

    if not component:
        return 0.0

    xs = [
        p[0]
        for p in component
    ]

    ys = [
        p[1]
        for p in component
    ]

    width = (
        max(xs)
        - min(xs)
        + 1
    ) * cell_mm

    depth = (
        max(ys)
        - min(ys)
        + 1
    ) * cell_mm

    return max(
        width,
        depth,
    )


def inspect_final_gcode_printability(
    artifact: Path,
    *,
    cell_mm: float = 0.40,
    minimum_bad_component_mm2: float = 1.0,
    maximum_safe_bridge_span_mm: float = 8.0,
) -> dict[str, Any]:

    artifact = Path(
        artifact
    ).resolve()

    blockers: list[str] = []

    if not artifact.is_file():
        return {
            "status": "blocked",
            "blockers": [
                "artifact_missing"
            ],
        }

    if not zipfile.is_zipfile(
        artifact
    ):
        return {
            "status": "blocked",
            "blockers": [
                "artifact_not_zip"
            ],
        }

    with zipfile.ZipFile(
        artifact,
        "r",
    ) as z:

        names = z.namelist()

        gcodes = sorted(
            n
            for n in names
            if re.fullmatch(
                r"Metadata/plate_\d+\.gcode",
                n,
            )
        )

        if len(gcodes) != 1:
            return {
                "status": "blocked",
                "blockers": [
                    "expected_one_plate"
                ],
            }

        settings = {}

        settings_name = (
            "Metadata/project_settings.config"
        )

        if settings_name in names:
            try:
                settings = json.loads(
                    z.read(
                        settings_name
                    ).decode(
                        "utf-8-sig"
                    )
                )
            except Exception as exc:
                return {
                    "status": "blocked",
                    "blockers": [
                        "project_settings_invalid:"
                        + str(exc)
                    ],
                }

        gcode_text = z.read(
            gcodes[0]
        ).decode(
            "utf-8",
            errors="replace",
        )

    segments = (
        parse_extrusion_segments(
            gcode_text
        )
    )

    if not segments:
        return {
            "status": "blocked",
            "blockers": [
                "no_extrusion_segments"
            ],
        }

    line_width = _float_value(
        settings,
        "line_width",
        0.42,
    )

    configured_layer_height = (
        _float_value(
            settings,
            "layer_height",
            0.20,
        )
    )

    support_top_gap = (
        _float_value(
            settings,
            "support_top_z_distance",
            configured_layer_height,
        )
    )

    support_xy_distance = (
        _float_value(
            settings,
            "support_object_xy_distance",
            0.35,
        )
    )

    by_z: dict[
        float,
        list[ExtrusionSegment]
    ] = defaultdict(list)

    for segment in segments:
        by_z[
            round(
                float(segment.z),
                4,
            )
        ].append(segment)

    layers = sorted(
        by_z.keys()
    )

    layer_diffs = [
        b - a
        for a, b in zip(
            layers,
            layers[1:],
        )
        if (
            b - a
        ) > 0.01
        and (
            b - a
        ) < 1.0
    ]

    measured_layer_height = (
        statistics.median(
            layer_diffs
        )
        if layer_diffs
        else configured_layer_height
    )

    # A model layer may overhang the immediately preceding
    # model layer by a small amount. This is actual FDM
    # self-support, not "support material".
    self_support_xy_mm = max(
        line_width * 0.65,
        measured_layer_height * 1.10,
    )

    # Support interface lines are deliberately not a solid
    # geometric projection, therefore allow roughly one line
    # width around actual support paths.
    support_contact_xy_mm = max(
        line_width,
        support_xy_distance
        + line_width * 0.50,
    )

    # IMPORTANT:
    # support_top_z_distance is the intentional removable gap.
    # One physical layer of tolerance is allowed because model
    # and support Z values represent extrusion layers, not two
    # coincident mathematical surfaces.
    maximum_support_vertical_gap = (
        support_top_gap
        + measured_layer_height * 1.25
        + 0.05
    )

    model_cells: dict[
        float,
        set[tuple[int, int]]
    ] = {}

    support_cells: dict[
        float,
        set[tuple[int, int]]
    ] = {}

    bridge_cells: dict[
        float,
        set[tuple[int, int]]
    ] = {}

    risk_cells: dict[
        float,
        set[tuple[int, int]]
    ] = {}

    support_xy_length = 0.0

    for z_value in layers:

        model = set()
        support = set()
        bridge = set()
        risk = set()

        for segment in by_z[
            z_value
        ]:

            cells = _segment_cells(
                segment,
                cell_mm=cell_mm,
            )

            if _is_support(
                segment.feature
            ):
                support.update(cells)

                support_xy_length += (
                    math.hypot(
                        segment.x2
                        - segment.x1,
                        segment.y2
                        - segment.y1,
                    )
                )

                continue

            model.update(cells)

            if _is_bridge(
                segment.feature
            ):
                bridge.update(cells)

            elif _is_risky_model(
                segment.feature
            ):
                risk.update(cells)

        model_cells[z_value] = model
        support_cells[z_value] = support
        bridge_cells[z_value] = bridge
        risk_cells[z_value] = risk

    model_layers = [
        z
        for z in layers
        if model_cells[z]
    ]

    evidence = []

    total_bad_area = 0.0
    worst_bad_area = 0.0
    longest_bad_bridge = 0.0

    for model_index, z_value in enumerate(
        model_layers
    ):

        # The first actual model layer rests on the bed.
        if model_index == 0:
            continue

        previous_model_z = (
            model_layers[
                model_index - 1
            ]
        )

        previous_model_zone = _dilate(
            model_cells[
                previous_model_z
            ],
            radius_mm=
                self_support_xy_mm,
            cell_mm=cell_mm,
        )

        # Collect only support that is physically close enough
        # below THIS model layer.
        nearby_support = set()

        support_z_used = []

        for support_z in layers:

            if support_z >= z_value:
                break

            vertical_gap = (
                z_value
                - support_z
            )

            if (
                vertical_gap
                > maximum_support_vertical_gap
            ):
                continue

            if not support_cells[
                support_z
            ]:
                continue

            nearby_support.update(
                support_cells[
                    support_z
                ]
            )

            support_z_used.append(
                support_z
            )

        support_zone = _dilate(
            nearby_support,
            radius_mm=
                support_contact_xy_mm,
            cell_mm=cell_mm,
        )

        accepted_zone = (
            previous_model_zone
            | support_zone
        )

        unsupported_model = (
            risk_cells[
                z_value
            ]
            - accepted_zone
        )

        model_components = (
            _components(
                unsupported_model
            )
        )

        bad_model_components = []

        for component in (
            model_components
        ):
            area = _component_area(
                component,
                cell_mm,
            )

            if (
                area
                >= minimum_bad_component_mm2
            ):
                bad_model_components.append(
                    (
                        component,
                        area,
                    )
                )

                total_bad_area += area

                worst_bad_area = max(
                    worst_bad_area,
                    area,
                )

        # Bridge cells are allowed to span a SHORT distance.
        # Anything longer must have actual support below it.
        unsupported_bridge = (
            bridge_cells[
                z_value
            ]
            - support_zone
            - previous_model_zone
        )

        bridge_components = (
            _components(
                unsupported_bridge
            )
        )

        bad_bridge_components = []

        for component in bridge_components:

            span = _component_span(
                component,
                cell_mm,
            )

            area = _component_area(
                component,
                cell_mm,
            )

            if (
                span
                > maximum_safe_bridge_span_mm
                and area
                >= minimum_bad_component_mm2
            ):
                bad_bridge_components.append(
                    (
                        component,
                        span,
                        area,
                    )
                )

                longest_bad_bridge = max(
                    longest_bad_bridge,
                    span,
                )

        if (
            bad_model_components
            or bad_bridge_components
        ):

            largest_model_area = max(
                (
                    area
                    for _component, area
                    in bad_model_components
                ),
                default=0.0,
            )

            largest_bridge_span = max(
                (
                    span
                    for _component, span, _area
                    in bad_bridge_components
                ),
                default=0.0,
            )

            record = {
                "z_mm":
                    z_value,

                "previous_model_z_mm":
                    previous_model_z,

                "support_z_used":
                    support_z_used,

                "largest_unsupported_model_area_mm2":
                    largest_model_area,

                "largest_unsupported_bridge_span_mm":
                    largest_bridge_span,

                "bad_model_component_count":
                    len(
                        bad_model_components
                    ),

                "bad_bridge_component_count":
                    len(
                        bad_bridge_components
                    ),
            }

            evidence.append(record)

            if largest_model_area > 0:
                blockers.append(
                    "unsupported_model_region:"
                    f"z={z_value:.3f}:"
                    f"area={largest_model_area:.2f}mm2"
                )

            if largest_bridge_span > 0:
                blockers.append(
                    "unsafe_bridge:"
                    f"z={z_value:.3f}:"
                    f"span={largest_bridge_span:.2f}mm"
                )

    result = {
        "status":
            "pass"
            if not blockers
            else "blocked",

        "blockers":
            blockers,

        "artifact":
            str(artifact),

        "gcode_entry":
            gcodes[0],

        "layer_count":
            len(layers),

        "model_layer_count":
            len(model_layers),

        "segment_count":
            len(segments),

        "support_xy_length_mm":
            support_xy_length,

        "total_bad_area_mm2":
            total_bad_area,

        "worst_bad_area_mm2":
            worst_bad_area,

        "longest_bad_bridge_mm":
            longest_bad_bridge,

        "dangerous_layer_count":
            len(evidence),

        "dangerous_layers":
            evidence[:50],

        "policy": {
            "cell_mm":
                cell_mm,

            "line_width_mm":
                line_width,

            "configured_layer_height_mm":
                configured_layer_height,

            "measured_layer_height_mm":
                measured_layer_height,

            "self_support_xy_mm":
                self_support_xy_mm,

            "support_top_z_distance_mm":
                support_top_gap,

            "maximum_support_vertical_gap_mm":
                maximum_support_vertical_gap,

            "support_contact_xy_mm":
                support_contact_xy_mm,

            "minimum_bad_component_mm2":
                minimum_bad_component_mm2,

            "maximum_safe_bridge_span_mm":
                maximum_safe_bridge_span_mm,
        },
    }

    return result
