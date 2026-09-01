from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile

from pathlib import Path
from typing import Any

from am_print_executor.rigid_multimaterial_project import parse_3mf_leaves
from am_print_executor.surface_painted_multimaterial_project import inspect_paint
from am_print_executor.r2s_slice_paint_proof import inspect_gcode_3mf


class R3IntegrityError(RuntimeError):
    pass


LEGACY_MARKERS = (
    "region_01.stl",
    "region_02.stl",
    '"cut_ratio"',
    '"cut_ratios"',
    "R1_AXIS_PLANE_PARTITION_DETECTED",
)


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
        raise R3IntegrityError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise R3IntegrityError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise R3IntegrityError(f"Expected JSON object: {path}")

    return obj


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def inspect_zip_integrity(path: Path, *, kind: str) -> dict[str, Any]:
    path = Path(path).resolve()

    if not path.is_file():
        raise R3IntegrityError(f"{kind} missing: {path}")

    if not zipfile.is_zipfile(path):
        raise R3IntegrityError(f"{kind} is not a valid ZIP/3MF container: {path}")

    with zipfile.ZipFile(path, "r") as zf:
        bad = zf.testzip()
        names = zf.namelist()

        if bad is not None:
            raise R3IntegrityError(
                f"{kind} ZIP CRC/integrity failure at member: {bad}"
            )

        duplicate_names = sorted(
            {
                name
                for name in names
                if names.count(name) > 1
            }
        )

        zero_size_members = sorted(
            info.filename
            for info in zf.infolist()
            if info.file_size == 0
            and info.filename.lower().endswith(
                (".model", ".gcode", ".config", ".json", ".xml")
            )
        )

    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "zip_ok": True,
        "member_count": len(names),
        "duplicate_members": duplicate_names,
        "zero_size_critical_members": zero_size_members,
        "members": names,
    }


def require_members(
    info: dict[str, Any],
    *,
    required: list[str],
) -> dict[str, Any]:
    names = set(info["members"])
    missing = [
        name
        for name in required
        if name not in names
    ]

    return {
        "required": required,
        "missing": missing,
        "passed": not missing,
    }


def scan_legacy_markers(path: Path) -> dict[str, Any]:
    """
    Scan only text-like members. This is lineage contamination detection,
    not a generic binary substring grep.
    """
    hits: list[dict[str, str]] = []

    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            lower = info.filename.lower()

            if not lower.endswith(
                (
                    ".model",
                    ".config",
                    ".json",
                    ".xml",
                    ".rels",
                    ".gcode",
                    ".txt",
                )
            ):
                continue

            raw = zf.read(info.filename)

            try:
                text = raw.decode("utf-8", errors="ignore")
            except Exception:
                continue

            lower_text = text.lower()

            for marker in LEGACY_MARKERS:
                if marker.lower() in lower_text:
                    hits.append(
                        {
                            "member": info.filename,
                            "marker": marker,
                        }
                    )

    return {
        "hits": hits,
        "passed": not hits,
    }


def project_integrity(
    project: Path,
    *,
    expected_painted_triangles: int,
) -> dict[str, Any]:
    zip_info = inspect_zip_integrity(
        project,
        kind="Project 3MF",
    )

    member_gate = require_members(
        zip_info,
        required=[
            "Metadata/project_settings.config",
            "Metadata/model_settings.config",
        ],
    )

    model_members = [
        name
        for name in zip_info["members"]
        if name.lower().endswith(".model")
    ]

    leaves = parse_3mf_leaves(project)
    paint = inspect_paint(project)
    legacy = scan_legacy_markers(project)

    checks = {
        "zip_integrity":
            zip_info["zip_ok"],

        "required_metadata_members":
            member_gate["passed"],

        "model_member_present":
            len(model_members) >= 1,

        "single_physical_mesh":
            len(leaves) == 1,

        "paint_triangle_count_preserved":
            paint["painted_triangle_count"]
            == int(expected_painted_triangles),

        "paint_code_is_filament_2":
            paint["paint_codes"] == ["8"],

        "no_duplicate_zip_members":
            len(zip_info["duplicate_members"]) == 0,

        "no_zero_size_critical_members":
            len(zip_info["zero_size_critical_members"]) == 0,

        "legacy_partition_markers_absent":
            legacy["passed"],
    }

    return {
        "zip": zip_info,
        "required_members": member_gate,
        "model_members": model_members,
        "leaf_count": len(leaves),
        "paint": paint,
        "legacy_scan": legacy,
        "checks": checks,
        "failed_checks": [
            key for key, value in checks.items()
            if not value
        ],
    }


def gcode_integrity(
    gcode: Path,
) -> dict[str, Any]:
    zip_info = inspect_zip_integrity(
        gcode,
        kind="G-code 3MF",
    )

    member_gate = require_members(
        zip_info,
        required=[
            "Metadata/project_settings.config",
            "Metadata/slice_info.config",
            "Metadata/plate_1.gcode",
        ],
    )

    inspected = inspect_gcode_3mf(gcode)
    legacy = scan_legacy_markers(gcode)

    filament_rows = {
        row.get("id_int"): row
        for row in inspected["slice_info"]["filaments"]
        if row.get("id_int") in {1, 2}
    }

    f1 = filament_rows.get(1)
    f2 = filament_rows.get(2)

    usage = inspected["gcode_usage"]

    checks = {
        "zip_integrity":
            zip_info["zip_ok"],

        "required_members":
            member_gate["passed"],

        "single_plate_gcode":
            inspected["gcode_member"] == "Metadata/plate_1.gcode",

        "gcode_nonempty":
            inspected["gcode_member_size_bytes"] > 0,

        "filament_1_present":
            f1 is not None,

        "filament_2_present":
            f2 is not None,

        "filament_1_used_for_object":
            bool(f1 and f1.get("used_for_object_bool")),

        "filament_2_used_for_object":
            bool(f2 and f2.get("used_for_object_bool")),

        "filament_1_positive_usage":
            bool(
                f1
                and f1.get("used_g_float") is not None
                and f1["used_g_float"] > 0
            ),

        "filament_2_positive_usage":
            bool(
                f2
                and f2.get("used_g_float") is not None
                and f2["used_g_float"] > 0
            ),

        "m620_contains_both":
            usage["m620_logical_filaments"] == [0, 1],

        "m621_contains_both":
            usage["m621_logical_filaments"] == [0, 1],

        "toolchange_transition_present":
            usage["logical_toolchange_transitions"] > 0,

        "extrusion_present":
            usage["extrusion_command_count"] > 0,

        "no_duplicate_zip_members":
            len(zip_info["duplicate_members"]) == 0,

        "no_zero_size_critical_members":
            len(zip_info["zero_size_critical_members"]) == 0,

        "legacy_partition_markers_absent":
            legacy["passed"],
    }

    return {
        "zip": zip_info,
        "required_members": member_gate,
        "inspection": inspected,
        "legacy_scan": legacy,
        "checks": checks,
        "failed_checks": [
            key for key, value in checks.items()
            if not value
        ],
    }


def lineage_integrity(
    *,
    project: Path,
    gcode: Path,
    surface_report: dict[str, Any],
    slice_report: dict[str, Any],
) -> dict[str, Any]:
    project_sha = sha256_file(project)
    gcode_sha = sha256_file(gcode)

    surface_status = surface_report.get("status")
    slice_status = slice_report.get("status")

    surface_project_sha = (
        surface_report
        .get("project", {})
        .get("sha256")
    )

    # Older surface report schema may store the output SHA only indirectly.
    # The R2S report is the authoritative downstream hash lock.
    r2s_input_before = (
        slice_report
        .get("input_project", {})
        .get("sha256_before")
    )

    r2s_input_after = (
        slice_report
        .get("input_project", {})
        .get("sha256_after")
    )

    r2s_gcode_sha = (
        slice_report
        .get("gcode", {})
        .get("sha256")
    )

    r2s_gcode_path = (
        slice_report
        .get("gcode", {})
        .get("path")
    )

    checks = {
        "surface_report_pass":
            surface_status
            == "surface_painted_multimaterial_project_ready",

        "slice_report_pass":
            slice_status
            == "slice_paint_proof_pass",

        "project_sha_matches_r2s_input_before":
            project_sha == r2s_input_before,

        "project_sha_matches_r2s_input_after":
            project_sha == r2s_input_after,

        "r2s_input_sha_stable":
            r2s_input_before == r2s_input_after,

        "gcode_sha_matches_r2s":
            gcode_sha == r2s_gcode_sha,

        "gcode_path_matches_r2s":
            (
                str(Path(r2s_gcode_path).resolve())
                == str(Path(gcode).resolve())
                if r2s_gcode_path
                else False
            ),
    }

    # Surface report project SHA is optional because the existing report schema
    # may not expose it at project.sha256.
    if surface_project_sha:
        checks["project_sha_matches_surface_report"] = (
            project_sha == surface_project_sha
        )

    return {
        "project_sha256": project_sha,
        "gcode_sha256": gcode_sha,
        "surface_report_status": surface_status,
        "slice_report_status": slice_status,
        "r2s_input_sha_before": r2s_input_before,
        "r2s_input_sha_after": r2s_input_after,
        "r2s_gcode_sha256": r2s_gcode_sha,
        "r2s_gcode_path": r2s_gcode_path,
        "checks": checks,
        "failed_checks": [
            key for key, value in checks.items()
            if not value
        ],
    }


def run_r3(
    *,
    project: Path,
    gcode: Path,
    surface_report_path: Path,
    slice_report_path: Path,
    output_report: Path,
) -> dict[str, Any]:
    project = Path(project).resolve()
    gcode = Path(gcode).resolve()

    surface_report = load_json(surface_report_path)
    slice_report = load_json(slice_report_path)

    expected_painted = (
        surface_report
        .get("paint_inspection", {})
        .get("painted_triangle_count")
    )

    if not isinstance(expected_painted, int) or expected_painted <= 0:
        raise R3IntegrityError(
            "Surface report lacks a valid painted_triangle_count."
        )

    project_result = project_integrity(
        project,
        expected_painted_triangles=expected_painted,
    )

    gcode_result = gcode_integrity(
        gcode,
    )

    lineage = lineage_integrity(
        project=project,
        gcode=gcode,
        surface_report=surface_report,
        slice_report=slice_report,
    )

    all_checks = {
        **{
            f"project::{key}": value
            for key, value in project_result["checks"].items()
        },
        **{
            f"gcode::{key}": value
            for key, value in gcode_result["checks"].items()
        },
        **{
            f"lineage::{key}": value
            for key, value in lineage["checks"].items()
        },
    }

    failed = [
        key for key, value in all_checks.items()
        if not value
    ]

    status = (
        "r3_project_integrity_pass"
        if not failed
        else "r3_project_integrity_fail"
    )

    report = {
        "schema_version":
            "r3-project-integrity-v2",

        "module":
            "M4",

        "stage":
            "R3_PROJECT_INTEGRITY",

        "status":
            status,

        "project":
            project_result,

        "gcode":
            gcode_result,

        "lineage":
            lineage,

        "checks":
            all_checks,

        "failed_checks":
            failed,

        "legacy_artifacts_policy": {
            "z60_partition_reusable":
                False,

            "old_region_stls_reusable":
                False,

            "old_r3_r11_evidence_reusable":
                False,
        },

        "safety": {
            "files_modified":
                False,

            "network_used":
                False,

            "printer_contacted":
                False,

            "artifact_uploaded":
                False,

            "print_started":
                False,
        },

        "next_gate":
            (
                "R4_PRIME_TOWER"
                if status == "r3_project_integrity_pass"
                else None
            ),
    }

    write_json(
        output_report,
        report,
    )

    report["report_path"] = str(
        Path(output_report).resolve()
    )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only R3 integrity gate for the rebuilt single-mesh "
            "surface-painted dual-material Bambu project and its sliced G-code."
        )
    )

    parser.add_argument("--project", required=True)
    parser.add_argument("--gcode", required=True)
    parser.add_argument("--surface-report", required=True)
    parser.add_argument("--slice-report", required=True)
    parser.add_argument("--report", required=True)

    args = parser.parse_args()

    result = run_r3(
        project=Path(args.project),
        gcode=Path(args.gcode),
        surface_report_path=Path(args.surface_report),
        slice_report_path=Path(args.slice_report),
        output_report=Path(args.report),
    )

    p = result["project"]
    g = result["gcode"]
    l = result["lineage"]

    print("=== R3 PROJECT INTEGRITY V2 ===")
    print("PROJECT_SHA256=", l["project_sha256"])
    print("GCODE_SHA256=", l["gcode_sha256"])
    print()
    print("PROJECT_ZIP_OK=", p["zip"]["zip_ok"])
    print("PROJECT_LEAF_COUNT=", p["leaf_count"])
    print("PROJECT_PAINTED_TRIANGLES=", p["paint"]["painted_triangle_count"])
    print("PROJECT_PAINT_CODES=", p["paint"]["paint_codes"])
    print("PROJECT_LEGACY_MARKER_HITS=", p["legacy_scan"]["hits"])
    print()
    print("GCODE_ZIP_OK=", g["zip"]["zip_ok"])
    print(
        "GCODE_MEMBER=",
        g["inspection"]["gcode_member"],
    )
    print(
        "FILAMENT_1_USED_G=",
        next(
            (
                row.get("used_g_float")
                for row in g["inspection"]["slice_info"]["filaments"]
                if row.get("id_int") == 1
            ),
            None,
        ),
    )
    print(
        "FILAMENT_2_USED_G=",
        next(
            (
                row.get("used_g_float")
                for row in g["inspection"]["slice_info"]["filaments"]
                if row.get("id_int") == 2
            ),
            None,
        ),
    )
    print(
        "TOOLCHANGE_TRANSITIONS=",
        g["inspection"]["gcode_usage"]["logical_toolchange_transitions"],
    )
    print("GCODE_LEGACY_MARKER_HITS=", g["legacy_scan"]["hits"])
    print()
    print(
        "PROJECT_SHA_MATCH_R2S=",
        l["checks"]["project_sha_matches_r2s_input_before"]
        and l["checks"]["project_sha_matches_r2s_input_after"],
    )
    print(
        "GCODE_SHA_MATCH_R2S=",
        l["checks"]["gcode_sha_matches_r2s"],
    )
    print("FAILED_CHECKS=", result["failed_checks"])
    print()
    print("FILES_MODIFIED=False")
    print("NETWORK_USED=False")
    print("PRINTER_CONTACTED=False")
    print("ARTIFACT_UPLOADED=False")
    print("PRINT_STARTED=False")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("REPORT=", result["report_path"])

    return 0 if result["status"] == "r3_project_integrity_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
