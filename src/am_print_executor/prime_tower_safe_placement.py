from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import zipfile

from pathlib import Path
from typing import Any

from am_print_executor.multimaterial_path_outside_resolver import (
    clone_project_with_setting_overrides,
)

from am_print_executor.multimaterial_slice_preflight import (
    run_direct_project_slice,
)


class PrimeTowerPlacementError(RuntimeError):
    pass


PROJECT_SETTINGS = (
    "Metadata/project_settings.config"
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception as exc:
        raise PrimeTowerPlacementError(
            f"Cannot parse JSON: {path}"
        ) from exc

    if not isinstance(data, dict):
        raise PrimeTowerPlacementError(
            f"Expected JSON object: {path}"
        )

    return data


def _project_settings(
    project: Path,
) -> dict[str, Any]:

    if not zipfile.is_zipfile(project):
        raise PrimeTowerPlacementError(
            "Project is not a valid 3MF ZIP."
        )

    with zipfile.ZipFile(
        project,
        "r",
    ) as zf:

        if PROJECT_SETTINGS not in zf.namelist():
            raise PrimeTowerPlacementError(
                "project_settings.config missing."
            )

        data = json.loads(
            zf.read(
                PROJECT_SETTINGS
            ).decode(
                "utf-8-sig"
            )
        )

    if not isinstance(data, dict):
        raise PrimeTowerPlacementError(
            "Project settings root is not an object."
        )

    return data


def _float_scalar(
    value: Any,
    *,
    key: str,
) -> float:

    if isinstance(value, list):
        if len(value) != 1:
            raise PrimeTowerPlacementError(
                f"{key} must contain one value."
            )

        value = value[0]

    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PrimeTowerPlacementError(
            f"Cannot parse {key}: {value!r}"
        ) from exc

    if not math.isfinite(result):
        raise PrimeTowerPlacementError(
            f"Non-finite {key}."
        )

    return result


def _setting_shape(
    original: Any,
    value: float,
) -> Any:

    text = f"{value:.3f}".rstrip("0").rstrip(".")

    if isinstance(original, list):
        return [text]

    if isinstance(original, str):
        return text

    return value


_POINT_PATTERN = re.compile(
    r"^\s*"
    r"(-?\d+(?:\.\d+)?)"
    r"\s*[xX,]\s*"
    r"(-?\d+(?:\.\d+)?)"
    r"\s*$"
)


def parse_printable_area(
    value: Any,
) -> list[tuple[float, float]]:

    points: list[tuple[float, float]] = []

    if isinstance(value, list):

        # [[0,0], [256,0], ...]
        if all(
            isinstance(item, (list, tuple))
            and len(item) == 2
            for item in value
        ):
            for item in value:
                points.append(
                    (
                        float(item[0]),
                        float(item[1]),
                    )
                )

        # ["0x0", "256x0", ...]
        elif all(
            isinstance(item, str)
            for item in value
        ):
            for item in value:
                match = _POINT_PATTERN.match(
                    item
                )

                if match is None:
                    raise PrimeTowerPlacementError(
                        "Unsupported printable-area "
                        f"point: {item!r}"
                    )

                points.append(
                    (
                        float(match.group(1)),
                        float(match.group(2)),
                    )
                )

        # [0,0,256,0,...]
        elif (
            len(value) >= 6
            and len(value) % 2 == 0
            and all(
                isinstance(
                    item,
                    (int, float),
                )
                for item in value
            )
        ):
            for index in range(
                0,
                len(value),
                2,
            ):
                points.append(
                    (
                        float(value[index]),
                        float(value[index + 1]),
                    )
                )

    if len(points) < 3:
        raise PrimeTowerPlacementError(
            "Unable to parse a printable-area polygon."
        )

    return points


def discover_printable_area(
    *,
    project_settings: dict[str, Any],
    machine_profile: dict[str, Any] | None,
) -> dict[str, Any]:

    sources = [
        (
            "project_settings.printable_area",
            project_settings.get(
                "printable_area"
            ),
        ),
        (
            "project_settings.bed_shape",
            project_settings.get(
                "bed_shape"
            ),
        ),
    ]

    if machine_profile is not None:
        sources.extend(
            [
                (
                    "machine_profile.printable_area",
                    machine_profile.get(
                        "printable_area"
                    ),
                ),
                (
                    "machine_profile.bed_shape",
                    machine_profile.get(
                        "bed_shape"
                    ),
                ),
            ]
        )

    errors = []

    for name, value in sources:
        if value is None:
            continue

        try:
            points = parse_printable_area(
                value
            )
        except Exception as exc:
            errors.append(
                f"{name}: {exc}"
            )
            continue

        xs = [
            point[0]
            for point in points
        ]

        ys = [
            point[1]
            for point in points
        ]

        return {
            "source":
                name,

            "polygon":
                points,

            "bbox": {
                "min_x": min(xs),
                "max_x": max(xs),
                "min_y": min(ys),
                "max_y": max(ys),
            },
        }

    raise PrimeTowerPlacementError(
        "Printable area unavailable. "
        + "; ".join(errors)
    )


def generate_candidates(
    *,
    bbox: dict[str, float],
    tower_width: float,
    brim_width: float,
    edge_margin_mm: float,
    grid_size: int,
) -> list[tuple[float, float]]:

    if grid_size < 2:
        raise PrimeTowerPlacementError(
            "grid_size must be >= 2."
        )

    if tower_width <= 0:
        raise PrimeTowerPlacementError(
            "prime tower width must be positive."
        )

    safety = (
        max(0.0, brim_width)
        + max(0.0, edge_margin_mm)
    )

    min_x = (
        bbox["min_x"]
        + safety
    )

    # wipe_tower_x is used as the tower translation;
    # account for known tower width on the X axis.
    max_x = (
        bbox["max_x"]
        - tower_width
        - safety
    )

    min_y = (
        bbox["min_y"]
        + safety
    )

    max_y = (
        bbox["max_y"]
        - safety
    )

    if max_x <= min_x or max_y <= min_y:
        raise PrimeTowerPlacementError(
            "Printable area is too small for "
            "candidate generation."
        )

    def linspace(
        start: float,
        stop: float,
        count: int,
    ) -> list[float]:

        if count == 1:
            return [
                (start + stop) / 2.0
            ]

        return [
            start
            + (
                stop - start
            )
            * index
            / (count - 1)
            for index in range(count)
        ]

    xs = linspace(
        min_x,
        max_x,
        grid_size,
    )

    ys = linspace(
        min_y,
        max_y,
        grid_size,
    )

    pairs = [
        (
            round(x, 3),
            round(y, 3),
        )
        for y in ys
        for x in xs
    ]

    # Prefer positions near the bed perimeter rather
    # than the center, because objects are commonly
    # arranged near the center. Bambu Studio remains
    # the authoritative validator for every candidate.
    cx = (
        bbox["min_x"]
        + bbox["max_x"]
    ) / 2.0

    cy = (
        bbox["min_y"]
        + bbox["max_y"]
    ) / 2.0

    pairs.sort(
        key=lambda p: (
            -(
                (p[0] - cx) ** 2
                + (p[1] - cy) ** 2
            ),
            p[1],
            p[0],
        )
    )

    return pairs


def resolve_prime_tower_position(
    *,
    studio_exe: Path,
    source_project: Path,
    machine_profile_path: Path | None,
    work_dir: Path,
    resolved_project: Path,
    resolved_gcode: Path,
    edge_margin_mm: float = 5.0,
    grid_size: int = 4,
) -> dict[str, Any]:

    studio_exe = studio_exe.resolve()
    source_project = source_project.resolve()
    work_dir = work_dir.resolve()
    resolved_project = resolved_project.resolve()
    resolved_gcode = resolved_gcode.resolve()

    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    settings = _project_settings(
        source_project
    )

    machine = (
        _load_json(
            machine_profile_path.resolve()
        )
        if machine_profile_path
        else None
    )

    area = discover_printable_area(
        project_settings=settings,
        machine_profile=machine,
    )

    if str(
        settings.get(
            "enable_prime_tower",
            ""
        )
    ) not in {
        "1",
        "true",
        "True",
    }:
        raise PrimeTowerPlacementError(
            "Source project does not have "
            "prime tower enabled."
        )

    tower_width = _float_scalar(
        settings.get(
            "prime_tower_width"
        ),
        key="prime_tower_width",
    )

    brim_width = _float_scalar(
        settings.get(
            "prime_tower_brim_width",
            0,
        ),
        key="prime_tower_brim_width",
    )

    original_x = settings.get(
        "wipe_tower_x"
    )

    original_y = settings.get(
        "wipe_tower_y"
    )

    if original_x is None or original_y is None:
        raise PrimeTowerPlacementError(
            "wipe_tower_x/y missing."
        )

    candidates = generate_candidates(
        bbox=area["bbox"],
        tower_width=tower_width,
        brim_width=brim_width,
        edge_margin_mm=edge_margin_mm,
        grid_size=grid_size,
    )

    attempts = []
    selected = None

    source_sha = _sha256(
        source_project
    )

    for index, (x, y) in enumerate(
        candidates,
        start=1,
    ):
        variant = (
            work_dir
            / f"candidate_{index:02d}.project.3mf"
        )

        gcode = (
            work_dir
            / f"candidate_{index:02d}.gcode.3mf"
        )

        clone_project_with_setting_overrides(
            source=source_project,
            destination=variant,
            overrides={
                "wipe_tower_x":
                    _setting_shape(
                        original_x,
                        x,
                    ),

                "wipe_tower_y":
                    _setting_shape(
                        original_y,
                        y,
                    ),
            },
        )

        result = run_direct_project_slice(
            studio_exe=studio_exe,
            project_path=variant,
            output_path=gcode,
        )

        from am_print_executor.bambu_cli_exit_codes import (
            normalize_bambu_cli_exit_result,
        )

        normalize_bambu_cli_exit_result(
            result
        )

        attempt = {
            "candidate_index":
                index,

            "wipe_tower_x":
                x,

            "wipe_tower_y":
                y,

            "exit":
                result["exit"],

            "slice_succeeded":
                result[
                    "direct_slice_succeeded"
                ],

            "project":
                str(variant),

            "gcode":
                str(gcode),
        }

        attempts.append(
            attempt
        )

        if result[
            "direct_slice_succeeded"
        ]:
            selected = {
                **attempt,
                "variant_project":
                    variant,
                "variant_gcode":
                    gcode,
            }

            break

    if selected is None:
        return {
            "status":
                "no_safe_prime_tower_candidate",

            "source_project":
                str(source_project),

            "source_sha256":
                source_sha,

            "printable_area":
                area,

            "tower_width_mm":
                tower_width,

            "tower_brim_width_mm":
                brim_width,

            "edge_margin_mm":
                edge_margin_mm,

            "grid_size":
                grid_size,

            "attempts":
                attempts,

            "source_project_modified":
                False,

            "network_used":
                False,

            "printer_command_sent":
                False,
        }

    resolved_project.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    resolved_gcode.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copy2(
        selected["variant_project"],
        resolved_project,
    )

    shutil.copy2(
        selected["variant_gcode"],
        resolved_gcode,
    )

    # Re-read resolved project to prove that prime
    # tower remains enabled.
    final_settings = _project_settings(
        resolved_project
    )

    prime_tower_preserved = str(
        final_settings.get(
            "enable_prime_tower",
            ""
        )
    ) in {
        "1",
        "true",
        "True",
    }

    if not prime_tower_preserved:
        raise PrimeTowerPlacementError(
            "Resolved project unexpectedly "
            "disabled prime tower."
        )

    if _sha256(source_project) != source_sha:
        raise PrimeTowerPlacementError(
            "Source project changed during "
            "safe-placement search."
        )

    return {
        "status":
            "prime_tower_safe_position_resolved",

        "source_project":
            str(source_project),

        "source_sha256":
            source_sha,

        "resolved_project":
            str(resolved_project),

        "resolved_project_sha256":
            _sha256(resolved_project),

        "resolved_gcode":
            str(resolved_gcode),

        "resolved_gcode_sha256":
            _sha256(resolved_gcode),

        "selected": {
            "candidate_index":
                selected[
                    "candidate_index"
                ],

            "wipe_tower_x":
                selected[
                    "wipe_tower_x"
                ],

            "wipe_tower_y":
                selected[
                    "wipe_tower_y"
                ],
        },

        "printable_area":
            area,

        "tower_width_mm":
            tower_width,

        "tower_brim_width_mm":
            brim_width,

        "edge_margin_mm":
            edge_margin_mm,

        "grid_size":
            grid_size,

        "attempts":
            attempts,

        "prime_tower_preserved":
            True,

        "source_project_modified":
            False,

        "network_used":
            False,

        "printer_command_sent":
            False,

        "ams_mapping_used":
            False,
    }


def cli_main(
    argv: list[str] | None = None,
) -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Resolve a Bambu prime-tower position "
            "by real CLI slicing validation."
        )
    )

    parser.add_argument(
        "--studio",
        required=True,
    )

    parser.add_argument(
        "--project",
        required=True,
    )

    parser.add_argument(
        "--machine-profile",
    )

    parser.add_argument(
        "--work-dir",
        required=True,
    )

    parser.add_argument(
        "--resolved-project",
        required=True,
    )

    parser.add_argument(
        "--resolved-gcode",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    parser.add_argument(
        "--edge-margin-mm",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--grid-size",
        type=int,
        default=4,
    )

    args = parser.parse_args(argv)

    try:
        result = resolve_prime_tower_position(
            studio_exe=Path(
                args.studio
            ),

            source_project=Path(
                args.project
            ),

            machine_profile_path=(
                Path(
                    args.machine_profile
                )
                if args.machine_profile
                else None
            ),

            work_dir=Path(
                args.work_dir
            ),

            resolved_project=Path(
                args.resolved_project
            ),

            resolved_gcode=Path(
                args.resolved_gcode
            ),

            edge_margin_mm=
                args.edge_margin_mm,

            grid_size=
                args.grid_size,
        )

        report = Path(
            args.report
        ).resolve()

        report.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        report.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )

        if result["status"] != (
            "prime_tower_safe_position_resolved"
        ):
            return 3

        return 0

    except Exception as exc:
        print(
            json.dumps(
                {
                    "status":
                        "prime_tower_resolution_error",

                    "error":
                        (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
