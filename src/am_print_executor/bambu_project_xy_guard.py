from __future__ import annotations

import json
import os
import re
import zipfile

from pathlib import Path
from typing import Any


class ProjectXYPlacementError(RuntimeError):
    pass


def _float(value: Any) -> float:
    try:
        return float(value)
    except Exception as exc:
        raise ProjectXYPlacementError(
            "Invalid printable-area value: "
            + repr(value)
        ) from exc


def _point(value: Any) -> tuple[float, float]:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
    ):
        return (
            _float(value[0]),
            _float(value[1]),
        )

    if isinstance(value, str):
        text = value.strip()

        for separator in ("x", "X", ",", ";"):
            if separator in text:
                parts = text.split(separator, 1)

                return (
                    _float(parts[0]),
                    _float(parts[1]),
                )

        parts = text.split()

        if len(parts) == 2:
            return (
                _float(parts[0]),
                _float(parts[1]),
            )

    raise ProjectXYPlacementError(
        "Unsupported printable-area point: "
        + repr(value)
    )


def read_printable_bbox(
    project_path: Path,
) -> dict[str, float]:

    with zipfile.ZipFile(
        project_path,
        "r",
    ) as zf:
        settings = json.loads(
            zf.read(
                "Metadata/project_settings.config"
            ).decode("utf-8-sig")
        )

    area = settings.get("printable_area")

    if not isinstance(area, list) or not area:
        raise ProjectXYPlacementError(
            "project_settings.printable_area "
            "is missing."
        )

    flat_numeric = (
        len(area) >= 6
        and len(area) % 2 == 0
        and all(
            isinstance(v, (int, float))
            for v in area
        )
    )

    if flat_numeric:
        points = [
            (
                float(area[i]),
                float(area[i + 1]),
            )
            for i in range(0, len(area), 2)
        ]
    else:
        points = [
            _point(value)
            for value in area
        ]

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
    }


def _inside(
    bounds: list,
    bed: dict[str, float],
    margin_mm: float,
) -> bool:

    return (
        float(bounds[0][0])
        >= bed["min_x"] + margin_mm
        and float(bounds[1][0])
        <= bed["max_x"] - margin_mm
        and float(bounds[0][1])
        >= bed["min_y"] + margin_mm
        and float(bounds[1][1])
        <= bed["max_y"] - margin_mm
    )


def _translate_root_build_items(
    source: Path,
    destination: Path,
    dx: float,
    dy: float,
) -> int:

    model_name = "3D/3dmodel.model"

    with zipfile.ZipFile(source, "r") as zin:
        raw = zin.read(model_name)
        text = raw.decode("utf-8")

        pattern = re.compile(
            r'(<item\b[^>]*\btransform=")'
            r'([^"]+)'
            r'(")',
            re.IGNORECASE,
        )

        count = 0

        def replace(match):
            nonlocal count

            values = (
                match.group(2)
                .strip()
                .split()
            )

            if len(values) != 12:
                raise ProjectXYPlacementError(
                    "Unexpected root build-item "
                    "transform: "
                    + match.group(2)
                )

            values[9] = format(
                float(values[9]) + dx,
                ".12g",
            )

            values[10] = format(
                float(values[10]) + dy,
                ".12g",
            )

            count += 1

            return (
                match.group(1)
                + " ".join(values)
                + match.group(3)
            )

        patched = pattern.sub(
            replace,
            text,
        )

        if count < 1:
            raise ProjectXYPlacementError(
                "No root build-item transform found."
            )

        with zipfile.ZipFile(
            destination,
            "w",
        ) as zout:

            for info in zin.infolist():
                data = zin.read(info.filename)

                if info.filename == model_name:
                    data = patched.encode("utf-8")

                zout.writestr(info, data)

    return count


def ensure_project_xy_on_bed(
    project_path: Path,
    *,
    margin_mm: float = 1.0,
) -> dict[str, Any]:

    project_path = (
        Path(project_path)
        .expanduser()
        .resolve()
    )

    from am_print_executor.printability_gate import (
        inspect_geometry,
    )

    before = inspect_geometry(
        project_path
    )

    bounds = before.get("bounds_mm")

    if not bounds:
        raise ProjectXYPlacementError(
            "Geometry bounds unavailable."
        )

    bed = read_printable_bbox(
        project_path
    )

    width = (
        float(bounds[1][0])
        - float(bounds[0][0])
    )

    depth = (
        float(bounds[1][1])
        - float(bounds[0][1])
    )

    usable_width = (
        bed["max_x"]
        - bed["min_x"]
        - 2 * margin_mm
    )

    usable_depth = (
        bed["max_y"]
        - bed["min_y"]
        - 2 * margin_mm
    )

    if (
        width > usable_width
        or depth > usable_depth
    ):
        raise ProjectXYPlacementError(
            "Object is larger than printable XY "
            "area."
        )

    if _inside(
        bounds,
        bed,
        margin_mm,
    ):
        return {
            "status":
                "already_inside_printable_area",
            "changed":
                False,
            "translation_mm":
                [0.0, 0.0],
            "bounds_before_mm":
                bounds,
            "bounds_after_mm":
                bounds,
            "printable_bbox":
                bed,
        }

    object_cx = (
        float(bounds[0][0])
        + float(bounds[1][0])
    ) / 2.0

    object_cy = (
        float(bounds[0][1])
        + float(bounds[1][1])
    ) / 2.0

    bed_cx = (
        bed["min_x"]
        + bed["max_x"]
    ) / 2.0

    bed_cy = (
        bed["min_y"]
        + bed["max_y"]
    ) / 2.0

    dx = bed_cx - object_cx
    dy = bed_cy - object_cy

    temporary = project_path.with_name(
        project_path.stem
        + ".xy_guard_tmp.3mf"
    )

    try:
        _translate_root_build_items(
            project_path,
            temporary,
            dx,
            dy,
        )

        after = inspect_geometry(
            temporary
        )

        after_bounds = after.get(
            "bounds_mm"
        )

        if not _inside(
            after_bounds,
            bed,
            margin_mm,
        ):
            raise ProjectXYPlacementError(
                "Automatic XY correction failed. "
                + repr(after_bounds)
            )

        os.replace(
            temporary,
            project_path,
        )

    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    return {
        "status":
            "recentered_to_printable_area",
        "changed":
            True,
        "translation_mm":
            [dx, dy],
        "bounds_before_mm":
            bounds,
        "bounds_after_mm":
            after_bounds,
        "printable_bbox":
            bed,
    }
