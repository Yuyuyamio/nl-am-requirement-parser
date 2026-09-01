from __future__ import annotations

import math
import re
import zipfile

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ExtrusionSegment:
    z: float
    x1: float
    y1: float
    x2: float
    y2: float
    feature: str
    line_width_mm: float | None = None
    layer_height_mm: float | None = None


_COORD_RE = re.compile(
    r"([XYZEFIJKP])([-+]?(?:\d+(?:\.\d*)?|\.\d+))"
)


def _normalise_feature(
    value: str,
) -> str:
    return " ".join(
        value.strip().lower().split()
    )


def _is_support_feature(
    feature: str,
) -> bool:
    f = _normalise_feature(feature)

    return (
        "support" in f
        and "unsupported" not in f
    )


def _is_bridge_feature(
    feature: str,
) -> bool:
    return (
        "bridge"
        in _normalise_feature(feature)
    )


def _is_risk_feature(
    feature: str,
) -> bool:
    """
    Features where lack of lower-layer support is
    relevant to external manufacturability.

    This intentionally avoids sparse infill because
    shifted infill patterns would create false alarms.
    """
    f = _normalise_feature(feature)

    keywords = (
        "outer wall",
        "external perimeter",
        "overhang",
        "bottom surface",
        "bottom solid",
        "solid infill",
    )

    return any(
        key in f
        for key in keywords
    )


def _arc_sweep(
    start_angle: float,
    end_angle: float,
    *,
    clockwise: bool,
    full_circle: bool = False,
    turns: int = 1,
) -> float:

    tau = 2.0 * math.pi

    if full_circle:
        magnitude = (
            tau
            * max(
                1,
                turns,
            )
        )

        return (
            -magnitude
            if clockwise
            else magnitude
        )

    if clockwise:
        magnitude = (
            start_angle
            - end_angle
        ) % tau

        if magnitude <= 1e-12:
            magnitude = tau

        if turns > 1:
            magnitude += (
                tau
                * (
                    turns - 1
                )
            )

        return -magnitude

    magnitude = (
        end_angle
        - start_angle
    ) % tau

    if magnitude <= 1e-12:
        magnitude = tau

    if turns > 1:
        magnitude += (
            tau
            * (
                turns - 1
            )
        )

    return magnitude


def _linearise_xy_arc(
    *,
    start_x: float,
    start_y: float,
    start_z: float,
    end_x: float,
    end_y: float,
    end_z: float,
    values: dict[str, float],
    clockwise: bool,
) -> list[tuple[float, float, float]]:
    """Linearise a G17 I/J extrusion arc for occupancy analysis."""

    if (
        "I" not in values
        and "J" not in values
    ):
        return [
            (
                end_x,
                end_y,
                end_z,
            )
        ]

    center_x = (
        start_x
        + values.get(
            "I",
            0.0,
        )
    )

    center_y = (
        start_y
        + values.get(
            "J",
            0.0,
        )
    )

    radius = math.hypot(
        start_x - center_x,
        start_y - center_y,
    )

    if radius <= 1e-12:
        return [
            (
                end_x,
                end_y,
                end_z,
            )
        ]

    start_angle = math.atan2(
        start_y - center_y,
        start_x - center_x,
    )

    end_angle = math.atan2(
        end_y - center_y,
        end_x - center_x,
    )

    full_circle = (
        math.hypot(
            end_x - start_x,
            end_y - start_y,
        )
        <= 1e-9
    )

    turns = max(
        1,
        int(
            round(
                abs(
                    values.get(
                        "P",
                        1.0,
                    )
                )
            )
        ),
    )

    sweep = _arc_sweep(
        start_angle,
        end_angle,
        clockwise=clockwise,
        full_circle=full_circle,
        turns=turns,
    )

    arc_length = (
        abs(
            sweep
        )
        * radius
    )

    segment_count = max(
        1,
        int(
            math.ceil(
                arc_length / 0.20
            )
        ),
        int(
            math.ceil(
                abs(sweep)
                / math.radians(10.0)
            )
        ),
    )

    points = []

    for index in range(
        1,
        segment_count + 1,
    ):
        ratio = (
            index
            / segment_count
        )

        angle = (
            start_angle
            + sweep * ratio
        )

        point_x = (
            center_x
            + radius
            * math.cos(angle)
        )

        point_y = (
            center_y
            + radius
            * math.sin(angle)
        )

        point_z = (
            start_z
            + (
                end_z
                - start_z
            )
            * ratio
        )

        points.append(
            (
                point_x,
                point_y,
                point_z,
            )
        )

    if points:
        points[-1] = (
            end_x,
            end_y,
            end_z,
        )

    return points


def parse_extrusion_segments(
    text: str,
) -> list[ExtrusionSegment]:

    x = 0.0
    y = 0.0
    z = 0.0
    e = 0.0

    xyz_absolute = True
    e_absolute = True

    plane = "G17"
    feature = "unknown"
    line_width_mm = None
    layer_height_mm = None

    result: list[ExtrusionSegment] = []

    for raw in text.splitlines():

        line = raw.strip()

        if not line:
            continue

        if line.startswith(";"):
            dimension = re.match(r";\s*(LINE_WIDTH|LAYER_HEIGHT)\s*:\s*([\d.]+)", line, re.I)
            if dimension:
                if dimension.group(1).upper() == "LINE_WIDTH":
                    line_width_mm = float(dimension.group(2))
                else:
                    layer_height_mm = float(dimension.group(2))
            match = re.match(
                r";\s*(?:FEATURE|TYPE)\s*:\s*(.+)",
                line,
                flags=re.I,
            )

            if match:
                feature = _normalise_feature(
                    match.group(1)
                )

            continue

        command = line.split(
            ";",
            1,
        )[0].strip()

        if not command:
            continue

        opcode = command.split(
            None,
            1,
        )[0].upper()

        if opcode == "G90":
            xyz_absolute = True
            continue

        if opcode == "G91":
            xyz_absolute = False
            continue

        if opcode == "M82":
            e_absolute = True
            continue

        if opcode == "M83":
            e_absolute = False
            continue

        if opcode in (
            "G17",
            "G18",
            "G19",
        ):
            plane = opcode
            continue

        if opcode == "G92":
            values = {
                key: float(value)
                for key, value
                in _COORD_RE.findall(
                    command
                )
            }

            if "X" in values:
                x = values["X"]

            if "Y" in values:
                y = values["Y"]

            if "Z" in values:
                z = values["Z"]

            if "E" in values:
                e = values["E"]

            continue

        if opcode not in (
            "G0",
            "G00",
            "G1",
            "G01",
            "G2",
            "G02",
            "G3",
            "G03",
        ):
            continue

        values = {
            key: float(value)
            for key, value
            in _COORD_RE.findall(
                command
            )
        }

        old_x = x
        old_y = y
        old_z = z

        if "X" in values:
            x = (
                values["X"]
                if xyz_absolute
                else x + values["X"]
            )

        if "Y" in values:
            y = (
                values["Y"]
                if xyz_absolute
                else y + values["Y"]
            )

        if "Z" in values:
            z = (
                values["Z"]
                if xyz_absolute
                else z + values["Z"]
            )

        if "E" in values:
            new_e = (
                values["E"]
                if e_absolute
                else e + values["E"]
            )

            delta_e = (
                new_e - e
                if e_absolute
                else values["E"]
            )

            e = new_e

        if "E" not in values:
            delta_e = 0.0

        # All physical G0/G1/G2/G3 motion has now advanced
        # the machine state. Travel/lift without positive E
        # creates no extrusion geometry.
        if delta_e <= 1e-7:
            continue

        is_arc = opcode in (
            "G2",
            "G02",
            "G3",
            "G03",
        )

        if (
            is_arc
            and plane == "G17"
        ):
            points = _linearise_xy_arc(
                start_x=old_x,
                start_y=old_y,
                start_z=old_z,
                end_x=x,
                end_y=y,
                end_z=z,
                values=values,
                clockwise=opcode
                in (
                    "G2",
                    "G02",
                ),
            )

            previous_x = old_x
            previous_y = old_y

            for (
                point_x,
                point_y,
                point_z,
            ) in points:

                distance = math.hypot(
                    point_x - previous_x,
                    point_y - previous_y,
                )

                if distance > 1e-6:
                    result.append(
                        ExtrusionSegment(
                            z=round(
                                point_z,
                                4,
                            ),
                            x1=previous_x,
                            y1=previous_y,
                            x2=point_x,
                            y2=point_y,
                            feature=feature,
                            line_width_mm=line_width_mm,
                            layer_height_mm=layer_height_mm,
                        )
                    )

                previous_x = point_x
                previous_y = point_y

            continue

        distance = math.hypot(
            x - old_x,
            y - old_y,
        )

        if distance > 1e-6:
            result.append(
                ExtrusionSegment(
                    z=round(
                        z,
                        4,
                    ),
                    x1=old_x,
                    y1=old_y,
                    x2=x,
                    y2=y,
                    feature=feature,
                    line_width_mm=line_width_mm,
                    layer_height_mm=layer_height_mm,
                )
            )

    return result


def _segment_cells(
    segment: ExtrusionSegment,
    *,
    cell_mm: float,
) -> set[tuple[int, int]]:

    dx = segment.x2 - segment.x1
    dy = segment.y2 - segment.y1

    length = math.hypot(dx, dy)

    samples = max(
        1,
        int(
            math.ceil(
                length
                / max(
                    cell_mm * 0.5,
                    0.05,
                )
            )
        ),
    )

    cells = set()

    for i in range(
        samples + 1
    ):
        t = i / samples

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
    radius_cells: int,
) -> set[tuple[int, int]]:

    if radius_cells <= 0:
        return set(cells)

    result = set()

    for x, y in cells:
        for dx in range(
            -radius_cells,
            radius_cells + 1,
        ):
            for dy in range(
                -radius_cells,
                radius_cells + 1,
            ):
                if (
                    dx * dx
                    + dy * dy
                    <= radius_cells
                    * radius_cells
                ):
                    result.add(
                        (
                            x + dx,
                            y + dy,
                        )
                    )

    return result


def _components(
    cells: set[tuple[int, int]],
) -> list[set[tuple[int, int]]]:

    remaining = set(cells)
    result = []

    neighbours = (
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

            for dx, dy in neighbours:

                neighbour = (
                    x + dx,
                    y + dy,
                )

                if neighbour not in remaining:
                    continue

                remaining.remove(
                    neighbour
                )

                component.add(
                    neighbour
                )

                q.append(
                    neighbour
                )

        result.append(
            component
        )

    return result


def inspect_support_continuity(
    segments: Iterable[ExtrusionSegment],
    *,
    cell_mm: float = 0.5,
    horizontal_allowance_mm: float = 0.75,
    below_layer_window: int = 3,
    minimum_danger_area_mm2: float = 4.0,
    max_unsupported_ratio: float = 0.20,
    max_bridge_segment_mm: float = 15.0,
) -> dict[str, Any]:

    by_z: dict[
        float,
        list[ExtrusionSegment]
    ] = defaultdict(list)

    for segment in segments:
        by_z[segment.z].append(
            segment
        )

    layers = sorted(
        by_z.keys()
    )

    blockers: list[str] = []

    layer_reports = []

    occupancy_by_layer: dict[
        float,
        set[tuple[int, int]]
    ] = {}

    for z in layers:

        occupancy = set()

        for segment in by_z[z]:
            occupancy.update(
                _segment_cells(
                    segment,
                    cell_mm=cell_mm,
                )
            )

        occupancy_by_layer[z] = (
            occupancy
        )

    radius_cells = max(
        1,
        int(
            math.ceil(
                horizontal_allowance_mm
                / cell_mm
            )
        ),
    )

    for layer_index, z in enumerate(
        layers
    ):

        # First physical layer obviously has no
        # previous-layer support requirement.
        if layer_index == 0:
            continue

        previous_z = layers[
            max(
                0,
                layer_index
                - below_layer_window
            ):
            layer_index
        ]

        lower_occupancy = set()

        for lower_z in previous_z:
            lower_occupancy.update(
                occupancy_by_layer[
                    lower_z
                ]
            )

        supported_zone = _dilate(
            lower_occupancy,
            radius_cells,
        )

        risky_cells = set()
        bridge_segments = []

        for segment in by_z[z]:

            if _is_support_feature(
                segment.feature
            ):
                continue

            if _is_bridge_feature(
                segment.feature
            ):
                bridge_segments.append(
                    segment
                )
                continue

            if not _is_risk_feature(
                segment.feature
            ):
                continue

            risky_cells.update(
                _segment_cells(
                    segment,
                    cell_mm=cell_mm,
                )
            )

        unsupported = (
            risky_cells
            - supported_zone
        )

        unsupported_ratio = (
            len(unsupported)
            / max(
                len(risky_cells),
                1,
            )
        )

        components = _components(
            unsupported
        )

        largest_cells = max(
            (
                len(component)
                for component
                in components
            ),
            default=0,
        )

        largest_area = (
            largest_cells
            * cell_mm
            * cell_mm
        )

        dangerous = bool(
            risky_cells
            and (
                largest_area
                >= minimum_danger_area_mm2
            )
            and (
                unsupported_ratio
                >= max_unsupported_ratio
            )
        )

        excessive_bridge = False
        longest_bridge = 0.0

        for segment in bridge_segments:
            length = math.hypot(
                segment.x2
                - segment.x1,
                segment.y2
                - segment.y1,
            )

            longest_bridge = max(
                longest_bridge,
                length,
            )

            if (
                length
                > max_bridge_segment_mm
            ):
                excessive_bridge = True

        if dangerous:
            blockers.append(
                "unsupported_region:"
                f"z={z:.4f}:"
                f"area={largest_area:.2f}mm2:"
                f"ratio={unsupported_ratio:.3f}"
            )

        if excessive_bridge:
            blockers.append(
                "excessive_bridge:"
                f"z={z:.4f}:"
                f"length={longest_bridge:.2f}mm"
            )

        if (
            dangerous
            or excessive_bridge
            or unsupported
            or bridge_segments
        ):
            layer_reports.append({
                "z_mm":
                    z,

                "risky_cell_count":
                    len(risky_cells),

                "unsupported_cell_count":
                    len(unsupported),

                "unsupported_ratio":
                    unsupported_ratio,

                "largest_unsupported_area_mm2":
                    largest_area,

                "bridge_segment_count":
                    len(bridge_segments),

                "longest_bridge_segment_mm":
                    longest_bridge,

                "dangerous_unsupported_region":
                    dangerous,

                "excessive_bridge":
                    excessive_bridge,
            })

    return {
        "status":
            "pass"
            if not blockers
            else "blocked",

        "blockers":
            blockers,

        "layer_count":
            len(layers),

        "segment_count":
            sum(
                len(v)
                for v in by_z.values()
            ),

        "policy": {
            "cell_mm":
                cell_mm,

            "horizontal_allowance_mm":
                horizontal_allowance_mm,

            "below_layer_window":
                below_layer_window,

            "minimum_danger_area_mm2":
                minimum_danger_area_mm2,

            "max_unsupported_ratio":
                max_unsupported_ratio,

            "max_bridge_segment_mm":
                max_bridge_segment_mm,
        },

        "layers_with_evidence":
            layer_reports,
    }


def inspect_gcode3mf_support_continuity(
    path: Path,
    **kwargs: Any,
) -> dict[str, Any]:

    path = Path(path).resolve()

    if not path.is_file():
        return {
            "status": "blocked",
            "blockers": [
                "gcode3mf_missing"
            ],
        }

    if not zipfile.is_zipfile(path):
        return {
            "status": "blocked",
            "blockers": [
                "gcode3mf_invalid_zip"
            ],
        }

    with zipfile.ZipFile(
        path,
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
                    "expected_exactly_one_plate"
                ],
            }

        text = z.read(
            gcodes[0]
        ).decode(
            "utf-8",
            errors="replace",
        )

    segments = (
        parse_extrusion_segments(
            text
        )
    )

    if not segments:
        return {
            "status": "blocked",
            "blockers": [
                "no_extrusion_segments_parsed"
            ],
        }

    result = inspect_support_continuity(
        segments,
        **kwargs,
    )

    result["gcode_entry"] = (
        gcodes[0]
    )

    return result
