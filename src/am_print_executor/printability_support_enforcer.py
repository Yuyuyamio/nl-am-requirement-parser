from __future__ import annotations

import json
import math

from pathlib import Path
from typing import Any

from am_print_executor.local_support_enforcer import (
    write_support_enforcer_project,
)


_SUPPORTED_KINDS = {
    "unsupported_extrusion_region",
    "unsupported_layer_island",
    "unsafe_bridge",
    "unanchored_support_toolpath",
}


def _bounds4(value: Any) -> list[float] | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(
            isinstance(row, list)
            and len(row) == 2
            for row in value
        )
    ):
        result = [
            float(value[0][0]),
            float(value[0][1]),
            float(value[1][0]),
            float(value[1][1]),
        ]
    elif (
        isinstance(value, list)
        and len(value) == 4
    ):
        result = [float(x) for x in value]
    else:
        return None

    if not all(math.isfinite(x) for x in result):
        return None

    x1, y1, x2, y2 = result

    if x2 <= x1 or y2 <= y1:
        return None

    return result


def build_support_enforcer_tracks(
    report: dict[str, Any],
) -> dict[str, Any]:
    """
    Convert the current strict Printability Gate feedback into
    the legacy/local support-enforcer track schema.

    This is model-agnostic:
    no model names, semantic parts, or fixed coordinates.
    """

    dangerous_layers = report.get(
        "dangerous_layers"
    )

    if not isinstance(
        dangerous_layers,
        list,
    ):
        dangerous_layers = (
            report.get(
                "feedback_to_m2",
                {},
            ).get(
                "dangerous_layers",
                [],
            )
        )

    policy = report.get(
        "policy",
        {},
    )

    layer_height = float(
        policy.get(
            "measured_layer_height_mm",
            policy.get(
                "configured_layer_height_mm",
                0.2,
            ),
        )
    )

    tracks: list[dict[str, Any]] = []

    for layer in dangerous_layers:
        if not isinstance(layer, dict):
            continue

        try:
            z = float(layer["z_mm"])
        except (KeyError, TypeError, ValueError):
            continue

        issues = layer.get(
            "issues",
            [],
        )

        if not isinstance(issues, list):
            continue

        for issue in issues:
            if not isinstance(issue, dict):
                continue

            kind = str(
                issue.get(
                    "kind",
                    "",
                )
            )

            if kind not in _SUPPORTED_KINDS:
                continue

            bounds = _bounds4(
                issue.get(
                    "xy_bounds_mm"
                )
            )

            if bounds is None:
                continue

            try:
                area = float(
                    issue.get(
                        "area_mm2",
                        0.0,
                    )
                )
            except (TypeError, ValueError):
                area = 0.0

            class_counts = {
                "NO_LOCAL_SUPPORT": 1,
            }

            if kind == "unanchored_support_toolpath":
                class_counts = {
                    "SUPPORT_TOO_FAR": 1,
                }

            track = {
                "source_kind": kind,
                "z_start": z,
                "z_end": z,
                "bbox_mm": bounds,
                "max_single_layer_area_mm2": max(
                    0.0,
                    area,
                ),
                "class_counts": class_counts,
            }

            if "span_mm" in issue:
                track["span_mm"] = issue[
                    "span_mm"
                ]

            if "anchored_contact_count" in issue:
                track[
                    "anchored_contact_count"
                ] = issue[
                    "anchored_contact_count"
                ]

            tracks.append(track)

    return {
        "schema_version":
            "printability_gate_to_support_enforcer_v1",

        "policy": {
            "measured_layer_height_mm":
                layer_height,

            "source_gate_semantics":
                policy.get(
                    "gate_semantics"
                ),
        },

        "tracks": tracks,
    }


def write_support_enforcer_from_printability_report(
    *,
    model_path: str | Path,
    printability_report: dict[str, Any],
    output_path: str | Path,
    xy_margin_mm: float = 0.8,
) -> dict[str, Any]:
    output_path = Path(
        output_path
    ).resolve()

    tracks_report = (
        build_support_enforcer_tracks(
            printability_report
        )
    )

    if not tracks_report["tracks"]:
        raise ValueError(
            "no actionable Printability Gate "
            "issues for local support enforcer"
        )

    tracks_path = output_path.with_name(
        output_path.stem
        + ".tracks.json"
    )

    tracks_path.write_text(
        json.dumps(
            tracks_report,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Important:
    # minimum_area_mm2 = 0.0 because the current strict
    # Printability Gate explicitly provides NO small-area
    # exemption.
    result = write_support_enforcer_project(
        model_path=model_path,
        defect_tracks_path=tracks_path,
        output_path=output_path,
        xy_margin_mm=xy_margin_mm,
        minimum_area_mm2=0.0,
    )

    result["tracks_report"] = str(
        tracks_path
    )

    result["source_issue_count"] = len(
        tracks_report["tracks"]
    )

    return result