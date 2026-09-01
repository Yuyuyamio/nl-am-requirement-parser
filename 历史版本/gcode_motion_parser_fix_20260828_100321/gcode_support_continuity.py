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


_COORD_RE = re.compile(
    r"([XYZEF])([-+]?(?:\d+(?:\.\d*)?|\.\d+))"
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


def parse_extrusion_segments(
    text: str,
) -> list[ExtrusionSegment]:

    x = 0.0
    y = 0.0
    z = 0.0
    e = 0.0

    xyz_absolute = True
    e_absolute = True

    feature = "unknown"

    result: list[ExtrusionSegment] = []

    for raw in text.splitlines():

        line = raw.strip()

        if not line:
            continue

        if line.startswith(";"):
            m = re.match(
                r";\s*(?:FEATURE|TYPE)\s*:\s*(.+)",
                line,
                flags=re.I,
            )

            if m:
                feature = _normalise_feature(
                    m.group(1)
                )

            continue

        command = line.split(
            ";",
            1,
        )[0].strip()

        if command == "G90":
            xyz_absolute = True
            continue

        if command == "G91":
            xyz_absolute = False
            continue

        if command == "M82":
            e_absolute = True
            continue

        if command == "M83":
            e_absolute = False
            continue

        if command.startswith("G92"):
            values = {
                k: float(v)
                for k, v in _COORD_RE.findall(
                    command
                )
            }

            if "E" in values:
                e = values["E"]

            if "X" in values:
                x = values["X"]

            if "Y" in values:
                y = values["Y"]

            if "Z" in values:
                z = values["Z"]

            continue

        if not (
            command.startswith("G0 ")
            or command.startswith("G1 ")
        ):
            continue

        values = {
            k: float(v)
            for k, v in _COORD_RE.findall(
                command
            )
        }

        old_x = x
        old_y = y
        old_z = z
        old_e = e

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

        else:
            delta_e = 0.0

        distance = math.hypot(
            x - old_x,
            y - old_y,
        )

        if (
            delta_e > 1e-7
            and distance > 1e-6
        ):
            result.append(
                ExtrusionSegment(
                    z=round(z, 4),
                    x1=old_x,
                    y1=old_y,
                    x2=x,
                    y2=y,
                    feature=feature,
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
