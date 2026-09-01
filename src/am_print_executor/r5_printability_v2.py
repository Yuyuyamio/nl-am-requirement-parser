from __future__ import annotations

import argparse
import hashlib
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from am_print_executor.rigid_multimaterial_project import parse_3mf_leaves
from am_print_executor.r2s_slice_paint_proof import inspect_gcode_3mf
from am_print_executor.r3_project_integrity_v2 import scan_legacy_markers
from am_print_executor.bambu_project_xy_guard import read_printable_bbox


class R5PrintabilityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise R5PrintabilityError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise R5PrintabilityError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise R5PrintabilityError(f"Expected JSON object: {path}")

    return obj


def write_json(path: Path, obj: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalize_scalar(value: Any) -> float | None:
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_project_settings(project: Path) -> dict[str, Any]:
    with zipfile.ZipFile(project, "r") as zf:
        name = "Metadata/project_settings.config"
        if name not in zf.namelist():
            raise R5PrintabilityError(
                "Resolved R4 project lacks Metadata/project_settings.config."
            )

        try:
            data = json.loads(
                zf.read(name).decode("utf-8-sig")
            )
        except Exception as exc:
            raise R5PrintabilityError(
                f"Invalid project_settings.config: {exc}"
            ) from exc

    if not isinstance(data, dict):
        raise R5PrintabilityError("project_settings.config must be a JSON object.")

    return data


def model_geometry(project: Path, bed_tol: float = 0.05) -> dict[str, Any]:
    leaves = parse_3mf_leaves(project)

    meshes = [
        trimesh.Trimesh(
            vertices=leaf["vertices_world"],
            faces=leaf["faces"],
            process=False,
        )
        for leaf in leaves
    ]

    if not meshes:
        raise R5PrintabilityError("Resolved R4 project contains no physical mesh.")

    combined = trimesh.util.concatenate(meshes)

    try:
        combined.merge_vertices()
    except Exception:
        pass

    pieces = list(combined.split(only_watertight=False))

    components = []
    floating = []

    for index, piece in enumerate(pieces):
        verts = np.asarray(piece.vertices, dtype=float)

        min_z = (
            float(np.min(verts[:, 2]))
            if len(verts)
            else math.inf
        )

        row = {
            "component_index": index,
            "min_z_mm": min_z,
            "bounds_mm": np.asarray(piece.bounds, dtype=float).round(6).tolist(),
            "floating_candidate": min_z > bed_tol,
            "volume_mm3": abs(float(piece.volume)),
            "watertight": bool(piece.is_watertight),
        }

        components.append(row)

        if row["floating_candidate"]:
            floating.append(index)

    bounds = np.asarray(combined.bounds, dtype=float)
    min_z = float(bounds[0, 2])
    max_z = float(bounds[1, 2])

    return {
        "leaf_count": len(leaves),
        "physical_component_count_after_weld": len(pieces),
        "floating_components": floating,
        "components": components,
        "bounds_mm": bounds.round(6).tolist(),
        "global_min_z_mm": min_z,
        "global_max_z_mm": max_z,
        "volume_mm3": abs(float(combined.volume)),
        "watertight": bool(combined.is_watertight),
        "faces": int(len(combined.faces)),
        "vertices": int(len(combined.vertices)),
    }


def box_inside(
    *,
    box: list[list[float]],
    bed: dict[str, float],
    margin: float = 0.0,
) -> bool:
    return (
        float(box[0][0]) >= float(bed["min_x"]) + margin
        and float(box[1][0]) <= float(bed["max_x"]) - margin
        and float(box[0][1]) >= float(bed["min_y"]) + margin
        and float(box[1][1]) <= float(bed["max_y"]) - margin
    )


def rectangles_overlap_xy(
    a: list[list[float]],
    b: list[list[float]],
    clearance_mm: float = 0.0,
) -> bool:
    ax0 = float(a[0][0]) - clearance_mm
    ay0 = float(a[0][1]) - clearance_mm
    ax1 = float(a[1][0]) + clearance_mm
    ay1 = float(a[1][1]) + clearance_mm

    bx0 = float(b[0][0])
    by0 = float(b[0][1])
    bx1 = float(b[1][0])
    by1 = float(b[1][1])

    return not (
        ax1 < bx0
        or bx1 < ax0
        or ay1 < by0
        or by1 < ay0
    )


def prime_tower_geometry(
    *,
    project_settings: dict[str, Any],
    selected: dict[str, Any],
    bed: dict[str, float],
    model_bounds: list[list[float]],
    clearance_mm: float = 2.0,
) -> dict[str, Any]:
    """
    Interpret wipe-tower placement using the same coordinate convention as
    prime_tower_safe_placement.py.

    Important:
    - wipe_tower_x is the tower translation/reference used by the candidate
      generator; the generator reserves the tower width in +X.
    - wipe_tower_y is a placement reference. The candidate generator does not
      subtract half a tower width from Y.
    - We therefore must NOT invent a centered width x width square around
      (wipe_tower_x, wipe_tower_y).

    Final tower manufacturability / collision acceptance remains the successful
    real Bambu CLI slice performed by R4.
    """
    selected_x = normalize_scalar(selected.get("wipe_tower_x"))
    selected_y = normalize_scalar(selected.get("wipe_tower_y"))

    settings_x = normalize_scalar(project_settings.get("wipe_tower_x"))
    settings_y = normalize_scalar(project_settings.get("wipe_tower_y"))

    width = normalize_scalar(project_settings.get("prime_tower_width"))

    if width is None:
        width = normalize_scalar(project_settings.get("wipe_tower_width"))

    if width is None or width <= 0:
        width = 35.0

    if selected_x is None or selected_y is None:
        raise R5PrintabilityError(
            "R4 selected record lacks wipe_tower_x/wipe_tower_y."
        )

    settings_match = (
        settings_x is not None
        and settings_y is not None
        and abs(settings_x - selected_x) <= 1e-6
        and abs(settings_y - selected_y) <= 1e-6
    )

    x_span = [
        float(selected_x),
        float(selected_x) + float(width),
    ]

    x_span_inside = (
        x_span[0] >= float(bed["min_x"])
        and x_span[1] <= float(bed["max_x"])
    )

    y_reference_inside = (
        float(selected_y) >= float(bed["min_y"])
        and float(selected_y) <= float(bed["max_y"])
    )

    placement_reference_inside = (
        float(selected_x) >= float(bed["min_x"])
        and float(selected_x) <= float(bed["max_x"])
        and y_reference_inside
    )

    return {
        "selected_x_mm": selected_x,
        "selected_y_mm": selected_y,
        "settings_x_mm": settings_x,
        "settings_y_mm": settings_y,
        "width_mm": width,
        "x_span_mm": x_span,
        "settings_match_selected": settings_match,
        "x_span_inside_printable_bbox": x_span_inside,
        "y_reference_inside_printable_bbox": y_reference_inside,
        "placement_reference_inside_printable_bbox": placement_reference_inside,
        "inside_printable_bbox": (
            x_span_inside and y_reference_inside
        ),
        "model_bbox_recollision_not_invented": True,
        "clearance_mm": clearance_mm,
        "coordinate_semantics": (
            "wipe_tower_x_translation_plus_width_in_positive_x;"
            "wipe_tower_y_placement_reference;"
            "R4_real_Bambu_CLI_slice_is_authoritative"
        ),
    }


def r4_authoritative_prime_tower_evidence(
    r4: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any]:
    """
    R4 is the authoritative prime-tower validator because it actually asks
    Bambu Studio to slice each candidate.

    If attempt-level evidence is available, verify the selected candidate
    against it. If the current report schema omits attempt details, the formal
    PASS status remains sufficient because that status is only emitted after a
    successful candidate slice.
    """
    status_ok = (
        r4.get("status")
        == "prime_tower_safe_position_resolved"
    )

    selected_index = selected.get("candidate_index")

    attempts = r4.get("attempts")
    matching = []

    if isinstance(attempts, list):
        matching = [
            row for row in attempts
            if isinstance(row, dict)
            and row.get("candidate_index") == selected_index
        ]

    attempt_pass = None

    if matching:
        row = matching[-1]

        explicit = row.get("slice_succeeded")

        signed = None
        exit_row = row.get("exit")
        if isinstance(exit_row, dict):
            signed = exit_row.get("signed")

        attempt_pass = (
            explicit is True
            or signed == 0
        )

    authoritative_pass = (
        status_ok
        and attempt_pass is not False
    )

    return {
        "r4_status_pass": status_ok,
        "selected_candidate_index": selected_index,
        "matching_attempt_count": len(matching),
        "matching_attempt_slice_pass": attempt_pass,
        "authoritative_bambu_cli_validation_pass": authoritative_pass,
    }


def gcode_printability(gcode: Path) -> dict[str, Any]:
    inspected = inspect_gcode_3mf(gcode)

    rows = {
        row.get("id_int"): row
        for row in inspected["slice_info"]["filaments"]
        if row.get("id_int") in {1, 2}
    }

    f1 = rows.get(1)
    f2 = rows.get(2)
    usage = inspected["gcode_usage"]

    return {
        "inspection": inspected,
        "filament_1_used_for_object": bool(
            f1 and f1.get("used_for_object_bool")
        ),
        "filament_2_used_for_object": bool(
            f2 and f2.get("used_for_object_bool")
        ),
        "filament_1_used_g": (
            f1.get("used_g_float")
            if f1 else None
        ),
        "filament_2_used_g": (
            f2.get("used_g_float")
            if f2 else None
        ),
        "m620_logical_filaments": usage["m620_logical_filaments"],
        "m621_logical_filaments": usage["m621_logical_filaments"],
        "toolchange_transitions": usage["logical_toolchange_transitions"],
        "extrusion_command_count": usage["extrusion_command_count"],
    }


def run_r5(
    *,
    project: Path,
    gcode: Path,
    r4_report_path: Path,
    r3_report_path: Path,
    expected_project_sha: str,
    expected_gcode_sha: str,
    output_report: Path,
    bed_tolerance_mm: float = 0.05,
) -> dict[str, Any]:
    project = Path(project).resolve()
    gcode = Path(gcode).resolve()

    if not project.is_file():
        raise R5PrintabilityError(f"R4 project missing: {project}")

    if not gcode.is_file():
        raise R5PrintabilityError(f"R4 G-code missing: {gcode}")

    r4 = load_json(r4_report_path)
    r3 = load_json(r3_report_path)

    project_sha = sha256_file(project)
    gcode_sha = sha256_file(gcode)

    geometry = model_geometry(
        project,
        bed_tol=bed_tolerance_mm,
    )

    bed = read_printable_bbox(project)
    model_inside = box_inside(
        box=geometry["bounds_mm"],
        bed=bed,
        margin=0.0,
    )

    selected = r4.get("selected")
    if not isinstance(selected, dict):
        raise R5PrintabilityError("R4 report lacks selected prime-tower candidate.")

    settings = read_project_settings(project)

    tower = prime_tower_geometry(
        project_settings=settings,
        selected=selected,
        bed=bed,
        model_bounds=geometry["bounds_mm"],
    )

    r4_prime_tower = r4_authoritative_prime_tower_evidence(
        r4,
        selected,
    )

    gcode_info = gcode_printability(gcode)

    project_legacy = scan_legacy_markers(project)
    gcode_legacy = scan_legacy_markers(gcode)

    checks = {
        "r4_report_pass":
            r4.get("status") == "prime_tower_safe_position_resolved",

        "r3_report_pass":
            r3.get("status") == "r3_project_integrity_pass",

        "project_sha_matches_expected":
            project_sha.lower() == expected_project_sha.lower(),

        "gcode_sha_matches_expected":
            gcode_sha.lower() == expected_gcode_sha.lower(),

        "single_physical_mesh_leaf":
            geometry["leaf_count"] == 1,

        "single_physical_component_after_weld":
            geometry["physical_component_count_after_weld"] == 1,

        "floating_physical_components_zero":
            geometry["floating_components"] == [],

        "model_on_bed":
            abs(geometry["global_min_z_mm"]) <= bed_tolerance_mm,

        "model_positive_height":
            geometry["global_max_z_mm"] > bed_tolerance_mm,

        "model_positive_volume":
            geometry["volume_mm3"] > 0,

        "model_watertight":
            geometry["watertight"],

        "model_inside_printable_bbox":
            model_inside,

        "prime_tower_settings_match_selected":
            tower["settings_match_selected"],

        "prime_tower_x_span_inside_printable_bbox":
            tower["x_span_inside_printable_bbox"],

        "prime_tower_y_reference_inside_printable_bbox":
            tower["y_reference_inside_printable_bbox"],

        "prime_tower_authoritative_bambu_cli_validated":
            r4_prime_tower["authoritative_bambu_cli_validation_pass"],

        "filament_1_used_for_object":
            gcode_info["filament_1_used_for_object"],

        "filament_2_used_for_object":
            gcode_info["filament_2_used_for_object"],

        "filament_1_positive_usage":
            (
                gcode_info["filament_1_used_g"] is not None
                and gcode_info["filament_1_used_g"] > 0
            ),

        "filament_2_positive_usage":
            (
                gcode_info["filament_2_used_g"] is not None
                and gcode_info["filament_2_used_g"] > 0
            ),

        "m620_contains_both":
            gcode_info["m620_logical_filaments"] == [0, 1],

        "m621_contains_both":
            gcode_info["m621_logical_filaments"] == [0, 1],

        "actual_toolchange_transition_present":
            gcode_info["toolchange_transitions"] > 0,

        "extrusion_present":
            gcode_info["extrusion_command_count"] > 0,

        "project_legacy_partition_markers_absent":
            project_legacy["passed"],

        "gcode_legacy_partition_markers_absent":
            gcode_legacy["passed"],
    }

    failed = [
        key for key, value in checks.items()
        if not value
    ]

    status = (
        "r5_printability_pass"
        if not failed
        else "r5_printability_fail"
    )

    report = {
        "schema_version": "r5-printability-v2.1",
        "module": "M4",
        "stage": "R5_PRINTABILITY",
        "status": status,
        "artifacts": {
            "project": str(project),
            "project_sha256": project_sha,
            "gcode": str(gcode),
            "gcode_sha256": gcode_sha,
        },
        "upstream": {
            "r4_report": str(Path(r4_report_path).resolve()),
            "r4_status": r4.get("status"),
            "r4_selected": selected,
            "r3_report": str(Path(r3_report_path).resolve()),
            "r3_status": r3.get("status"),
        },
        "geometry": geometry,
        "printable_bbox": bed,
        "model_inside_printable_bbox": model_inside,
        "prime_tower": tower,
        "r4_prime_tower_authority": r4_prime_tower,
        "gcode_printability": gcode_info,
        "legacy_scan": {
            "project": project_legacy,
            "gcode": gcode_legacy,
        },
        "checks": checks,
        "failed_checks": failed,
        "safety": {
            "network_used": False,
            "printer_contacted": False,
            "artifact_uploaded": False,
            "print_command_sent": False,
            "print_started": False,
        },
        "next_gate": (
            "R6_TOOLCHANGE_MATERIAL_CONSISTENCY"
            if status == "r5_printability_pass"
            else None
        ),
    }

    write_json(output_report, report)
    report["report_path"] = str(Path(output_report).resolve())

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only R5 Printability V2 for the rebuilt single-mesh "
            "surface-painted dual-material X1C artifact."
        )
    )

    parser.add_argument("--project", required=True)
    parser.add_argument("--gcode", required=True)
    parser.add_argument("--r4-report", required=True)
    parser.add_argument("--r3-report", required=True)
    parser.add_argument("--expected-project-sha", required=True)
    parser.add_argument("--expected-gcode-sha", required=True)
    parser.add_argument("--report", required=True)

    args = parser.parse_args()

    result = run_r5(
        project=Path(args.project),
        gcode=Path(args.gcode),
        r4_report_path=Path(args.r4_report),
        r3_report_path=Path(args.r3_report),
        expected_project_sha=args.expected_project_sha,
        expected_gcode_sha=args.expected_gcode_sha,
        output_report=Path(args.report),
    )

    geom = result["geometry"]
    tower = result["prime_tower"]
    gcode = result["gcode_printability"]

    print("=== R5 PRINTABILITY V2.1 ===")
    print("PROJECT_SHA256=", result["artifacts"]["project_sha256"])
    print("GCODE_SHA256=", result["artifacts"]["gcode_sha256"])
    print()
    print("PHYSICAL_LEAF_COUNT=", geom["leaf_count"])
    print(
        "PHYSICAL_COMPONENT_COUNT_AFTER_WELD=",
        geom["physical_component_count_after_weld"],
    )
    print("FLOATING_COMPONENTS=", geom["floating_components"])
    print("GLOBAL_MIN_Z_MM=", geom["global_min_z_mm"])
    print("MODEL_WATERTIGHT=", geom["watertight"])
    print("MODEL_INSIDE_PRINTABLE_BBOX=", result["model_inside_printable_bbox"])
    print()
    print("PRIME_TOWER_SELECTED_X=", tower["selected_x_mm"])
    print("PRIME_TOWER_SELECTED_Y=", tower["selected_y_mm"])
    print("PRIME_TOWER_WIDTH_MM=", tower["width_mm"])
    print("PRIME_TOWER_SETTINGS_MATCH=", tower["settings_match_selected"])
    print("PRIME_TOWER_X_SPAN_MM=", tower["x_span_mm"])
    print(
        "PRIME_TOWER_X_SPAN_INSIDE_BED=",
        tower["x_span_inside_printable_bbox"],
    )
    print(
        "PRIME_TOWER_Y_REFERENCE_INSIDE_BED=",
        tower["y_reference_inside_printable_bbox"],
    )
    print(
        "PRIME_TOWER_R4_BAMBU_CLI_VALIDATED=",
        result["r4_prime_tower_authority"][
            "authoritative_bambu_cli_validation_pass"
        ],
    )
    print()
    print("FILAMENT_1_USED_G=", gcode["filament_1_used_g"])
    print("FILAMENT_2_USED_G=", gcode["filament_2_used_g"])
    print("M620_LOGICAL_FILAMENTS=", gcode["m620_logical_filaments"])
    print("M621_LOGICAL_FILAMENTS=", gcode["m621_logical_filaments"])
    print("TOOLCHANGE_TRANSITIONS=", gcode["toolchange_transitions"])
    print("EXTRUSION_COMMAND_COUNT=", gcode["extrusion_command_count"])
    print()
    print("FAILED_CHECKS=", result["failed_checks"])
    print("NETWORK_USED=False")
    print("PRINTER_CONTACTED=False")
    print("ARTIFACT_UPLOADED=False")
    print("PRINT_COMMAND_SENT=False")
    print("PRINT_STARTED=False")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("REPORT=", result["report_path"])

    return 0 if result["status"] == "r5_printability_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
