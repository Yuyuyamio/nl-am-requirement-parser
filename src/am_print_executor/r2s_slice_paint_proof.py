from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import zipfile
import xml.etree.ElementTree as ET

from collections import Counter
from pathlib import Path
from typing import Any

from am_print_executor import multimaterial_project as mm
from am_print_executor import developer_mode_backend_v1120 as backend
from am_print_executor.bambu_headless_cli import (
    run_bambu_cli,
)
from am_print_executor.surface_painted_multimaterial_project import (
    inspect_paint,
)


class SlicePaintProofError(RuntimeError):
    pass


_M620_RE = re.compile(
    r"^\s*M620\s+S(\d+)(?:A)?(?:\s|$)",
    re.IGNORECASE | re.MULTILINE,
)

_M621_RE = re.compile(
    r"^\s*M621\s+S(\d+)(?:A)?(?:\s|$)",
    re.IGNORECASE | re.MULTILINE,
)

_EXTRUSION_RE = re.compile(
    r"^\s*G(?:0|1)\b[^\n;]*\bE-?(?:\d+(?:\.\d*)?|\.\d+)",
    re.IGNORECASE | re.MULTILINE,
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
        raise SlicePaintProofError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SlicePaintProofError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise SlicePaintProofError(f"Expected JSON object: {path}")

    return obj


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def lname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_slice_info_xml(raw: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SlicePaintProofError(
            f"Metadata/slice_info.config is invalid XML: {exc}"
        ) from exc

    filaments = []
    layer_rows = []

    for el in root.iter():
        name = lname(el.tag)

        if name == "filament":
            row = dict(el.attrib)
            row["id_int"] = (
                int(row["id"])
                if str(row.get("id", "")).isdigit()
                else None
            )
            row["used_g_float"] = float_or_none(row.get("used_g"))
            row["used_m_float"] = float_or_none(row.get("used_m"))
            row["used_for_object_bool"] = parse_bool(
                row.get("used_for_object")
            )
            row["used_for_support_bool"] = parse_bool(
                row.get("used_for_support")
            )
            filaments.append(row)

        elif name == "layer_filament_list":
            text = str(el.attrib.get("filament_list") or "").strip()
            ids = []
            for token in text.split():
                if token.lstrip("-").isdigit():
                    ids.append(int(token))

            layer_rows.append(
                {
                    "filament_list_raw": text,
                    "filament_indices_zero_based": ids,
                    "layer_ranges": el.attrib.get("layer_ranges"),
                }
            )

    return {
        "filaments": filaments,
        "layer_filament_lists": layer_rows,
    }


def parse_gcode_usage(text: str) -> dict[str, Any]:
    m620_sequence = [
        int(x)
        for x in _M620_RE.findall(text)
    ]

    m621_sequence = [
        int(x)
        for x in _M621_RE.findall(text)
    ]

    # 0 and 1 are the two logical print filaments in this formal dual-colour job.
    filtered = [
        value
        for value in m620_sequence
        if value in {0, 1}
    ]

    transitions = 0
    for previous, current in zip(
        filtered,
        filtered[1:],
    ):
        if previous != current:
            transitions += 1

    return {
        "m620_sequence": m620_sequence,
        "m621_sequence": m621_sequence,
        "m620_counts": dict(sorted(Counter(m620_sequence).items())),
        "m621_counts": dict(sorted(Counter(m621_sequence).items())),
        "m620_logical_filaments": sorted(set(filtered)),
        "m621_logical_filaments": sorted(
            set(x for x in m621_sequence if x in {0, 1})
        ),
        "logical_toolchange_transitions": transitions,
        "extrusion_command_count": len(_EXTRUSION_RE.findall(text)),
        "gcode_length_chars": len(text),
    }


def inspect_gcode_3mf(path: Path) -> dict[str, Any]:
    path = Path(path)

    if not path.is_file():
        raise SlicePaintProofError(f"G-code 3MF missing: {path}")

    if not zipfile.is_zipfile(path):
        raise SlicePaintProofError(f"Not a valid G-code 3MF ZIP: {path}")

    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()

        slice_member = "Metadata/slice_info.config"
        if slice_member not in names:
            raise SlicePaintProofError(
                "G-code 3MF is missing Metadata/slice_info.config."
            )

        gcode_members = [
            name
            for name in names
            if name.lower().endswith(".gcode")
        ]

        if len(gcode_members) != 1:
            raise SlicePaintProofError(
                f"Expected exactly one G-code member, got {gcode_members}"
            )

        slice_info = parse_slice_info_xml(
            zf.read(slice_member)
        )

        gcode_member = gcode_members[0]
        gcode_bytes = zf.read(gcode_member)

        if not gcode_bytes:
            raise SlicePaintProofError("G-code member is empty.")

        text = gcode_bytes.decode(
            "utf-8",
            errors="replace",
        )

    gcode_usage = parse_gcode_usage(text)

    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "valid_zip": True,
        "slice_info_member": slice_member,
        "gcode_member": gcode_member,
        "gcode_member_size_bytes": len(gcode_bytes),
        "slice_info": slice_info,
        "gcode_usage": gcode_usage,
    }


def build_slice_command(
    *,
    studio_exe: Path,
    input_project: Path,
    output_gcode: Path,
    machine_json: Path,
    process_json: Path,
    filament_jsons: list[Path],
) -> list[str]:
    """
    Formal post-paint slicing contract.

    Deliberately does NOT contain --orient, --arrange or --ensure-on-bed.
    Orientation was already accepted before surface painting.
    """
    if input_project.suffix.lower() != ".3mf":
        raise SlicePaintProofError(
            "R2S slice proof only accepts an already-oriented .3mf input."
        )

    return [
        str(studio_exe),
        "--load-settings",
        f"{machine_json};{process_json}",
        "--load-filaments",
        ";".join(str(path) for path in filament_jsons),
        "--slice",
        "0",
        "--debug",
        "5",
        "--export-3mf",
        str(output_gcode),
        str(input_project),
    ]


def color_norm(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text and not text.startswith("#"):
        text = "#" + text
    return text


def validate_dual_material_evidence(
    *,
    inspection: dict[str, Any],
    expected_colours: list[str],
) -> dict[str, Any]:
    filament_rows = inspection["slice_info"]["filaments"]

    by_id: dict[int, dict[str, Any]] = {}
    for row in filament_rows:
        fid = row.get("id_int")
        if fid in {1, 2}:
            by_id[int(fid)] = row

    expected = {
        1: color_norm(expected_colours[0]),
        2: color_norm(expected_colours[1]),
    }

    material_rows = {}

    for fid in (1, 2):
        row = by_id.get(fid)

        material_rows[str(fid)] = {
            "present": row is not None,
            "expected_colour": expected[fid],
            "actual_colour": (
                color_norm(row.get("color"))
                if row is not None
                else None
            ),
            "colour_match": (
                row is not None
                and color_norm(row.get("color")) == expected[fid]
            ),
            "used_for_object": (
                bool(row.get("used_for_object_bool"))
                if row is not None
                else False
            ),
            "used_g": (
                row.get("used_g_float")
                if row is not None
                else None
            ),
            "used_m": (
                row.get("used_m_float")
                if row is not None
                else None
            ),
            "positive_usage": (
                row is not None
                and row.get("used_g_float") is not None
                and row.get("used_g_float") > 0
                and row.get("used_m_float") is not None
                and row.get("used_m_float") > 0
            ),
        }

    layer_union = sorted(
        {
            idx
            for row in inspection["slice_info"]["layer_filament_lists"]
            for idx in row["filament_indices_zero_based"]
            if idx in {0, 1}
        }
    )

    usage = inspection["gcode_usage"]

    checks = {
        "filament_1_present":
            material_rows["1"]["present"],

        "filament_2_present":
            material_rows["2"]["present"],

        "filament_1_colour_match":
            material_rows["1"]["colour_match"],

        "filament_2_colour_match":
            material_rows["2"]["colour_match"],

        "filament_1_used_for_object":
            material_rows["1"]["used_for_object"],

        "filament_2_used_for_object":
            material_rows["2"]["used_for_object"],

        "filament_1_positive_usage":
            material_rows["1"]["positive_usage"],

        "filament_2_positive_usage":
            material_rows["2"]["positive_usage"],

        "slice_layer_union_contains_both":
            layer_union == [0, 1],

        "m620_contains_both_logical_filaments":
            usage["m620_logical_filaments"] == [0, 1],

        "m621_contains_both_logical_filaments":
            usage["m621_logical_filaments"] == [0, 1],

        "actual_toolchange_transition_present":
            usage["logical_toolchange_transitions"] > 0,

        "extrusion_present":
            usage["extrusion_command_count"] > 0,
    }

    return {
        "checks": checks,
        "failed_checks": [
            key for key, value in checks.items()
            if not value
        ],
        "material_rows": material_rows,
        "layer_filament_union_zero_based": layer_union,
    }


def run_slice_paint_proof(
    *,
    studio_exe: Path,
    input_project: Path,
    surface_report: Path,
    legacy_manifest: Path,
    source_task_dir: Path,
    output_gcode: Path,
    report_path: Path,
) -> dict[str, Any]:
    studio_exe = Path(studio_exe).expanduser().resolve()
    input_project = Path(input_project).expanduser().resolve()
    surface_report = Path(surface_report).expanduser().resolve()
    legacy_manifest = Path(legacy_manifest).expanduser().resolve()
    source_task_dir = Path(source_task_dir).expanduser().resolve()
    output_gcode = Path(output_gcode).expanduser().resolve()
    report_path = Path(report_path).expanduser().resolve()

    if not studio_exe.is_file():
        raise SlicePaintProofError(
            f"Bambu Studio missing: {studio_exe}"
        )

    if not input_project.is_file():
        raise SlicePaintProofError(
            f"Painted project missing: {input_project}"
        )

    if input_project.suffix.lower() != ".3mf":
        raise SlicePaintProofError(
            "Paint proof input must be an already-oriented .3mf."
        )

    paint_report = load_json(surface_report)

    if (
        paint_report.get("status")
        != "surface_painted_multimaterial_project_ready"
    ):
        raise SlicePaintProofError(
            "Upstream R1S/R2S surface-painted project is not PASS."
        )

    if paint_report.get("legacy_generated_regions_used") is not False:
        raise SlicePaintProofError(
            "Upstream report does not prove legacy generated regions were excluded."
        )

    paint_before = inspect_paint(input_project)

    if (
        paint_before.get("leaf_count") != 1
        or paint_before.get("painted_triangle_count", 0) <= 0
        or paint_before.get("paint_codes") != ["8"]
    ):
        raise SlicePaintProofError(
            f"Input paint contract invalid: {paint_before}"
        )

    expected_colours = (
        paint_report.get("project", {})
        .get("filament_colours")
    )

    if (
        not isinstance(expected_colours, list)
        or len(expected_colours) != 2
    ):
        raise SlicePaintProofError(
            "Upstream report does not contain exactly two expected filament colours."
        )

    legacy = load_json(legacy_manifest)
    profile_rows = legacy.get("filament_profiles")

    if not isinstance(profile_rows, list) or len(profile_rows) != 2:
        raise SlicePaintProofError(
            "Formal R2S slice proof requires exactly two filament profiles."
        )

    original_filaments = []

    for row in profile_rows:
        if not isinstance(row, dict):
            raise SlicePaintProofError("Invalid filament profile row.")

        path = Path(str(row.get("path") or "")).expanduser().resolve()

        if not path.is_file():
            raise SlicePaintProofError(
                f"Filament profile missing: {path}"
            )

        original_filaments.append(path)

    machine = mm.discover_resolved_profile(
        source_task_dir,
        "machine",
    )

    process = mm.discover_resolved_profile(
        source_task_dir,
        "process",
    )

    resolved = backend.materialize_bambu_cli_profiles(
        studio_exe=studio_exe,
        machine_json=machine,
        process_json=process,
        filament_jsons=original_filaments,
        cache_dir=(
            output_gcode.parent
            / ".nl_am_r2s_slice_profiles"
        ),
    )

    resolved_machine = Path(
        resolved["machine"]
    ).resolve()

    resolved_process = Path(
        resolved["process"]
    ).resolve()

    resolved_filaments = [
        Path(path).resolve()
        for path in resolved["filaments"]
    ]

    if len(resolved_filaments) != 2:
        raise SlicePaintProofError(
            f"Resolved filament count is {len(resolved_filaments)}, expected 2."
        )

    output_gcode.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        output_gcode.unlink()
    except FileNotFoundError:
        pass

    input_sha_before = sha256_file(input_project)

    command = build_slice_command(
        studio_exe=studio_exe,
        input_project=input_project,
        output_gcode=output_gcode,
        machine_json=resolved_machine,
        process_json=resolved_process,
        filament_jsons=resolved_filaments,
    )

    forbidden = [
        token
        for token in (
            "--orient",
            "--arrange",
            "--ensure-on-bed",
        )
        if token in command
    ]

    if forbidden:
        raise SlicePaintProofError(
            f"Forbidden post-paint geometry commands present: {forbidden}"
        )

    started = time.time()

    cli_result = run_bambu_cli(
        command,
        expected_outputs=[output_gcode],
        cwd=str(output_gcode.parent),
        timeout=900,
    )

    raw_rc = cli_result.raw_exit
    signed_rc = cli_result.signed_exit

    if not cli_result.success:
        raise SlicePaintProofError(
            "Bambu post-paint slicing failed.\n"
            f"returncode_raw={raw_rc}\n"
            f"returncode_signed={signed_rc}\n"
            f"outputs_exist={cli_result.outputs_exist}\n"
            f"stdout_tail={cli_result.stdout[-4000:]}\n"
            f"stderr_tail={cli_result.stderr[-4000:]}"
        )

    input_sha_after = sha256_file(input_project)

    inspection = inspect_gcode_3mf(
        output_gcode
    )

    evidence = validate_dual_material_evidence(
        inspection=inspection,
        expected_colours=expected_colours,
    )

    global_checks = {
        "upstream_surface_paint_pass":
            True,

        "single_physical_mesh_input":
            paint_before["leaf_count"] == 1,

        "accent_paint_present_before_slice":
            paint_before["painted_triangle_count"] > 0,

        "post_paint_orient_absent":
            "--orient" not in command,

        "post_paint_arrange_absent":
            "--arrange" not in command,

        "post_paint_ensure_on_bed_absent":
            "--ensure-on-bed" not in command,

        "input_project_sha_unchanged":
            input_sha_before == input_sha_after,

        "slice_exit_zero":
            signed_rc == 0,

        "exact_output_exists":
            output_gcode.is_file(),
    }

    checks = {
        **global_checks,
        **evidence["checks"],
    }

    failed_checks = [
        key for key, value in checks.items()
        if not value
    ]

    status = (
        "slice_paint_proof_pass"
        if not failed_checks
        else "slice_paint_proof_fail"
    )

    report = {
        "schema_version":
            "r2s-slice-paint-proof-v1",

        "module":
            "M4",

        "stage":
            "R2S_SLICE_PAINT_PROOF",

        "status":
            status,

        "input_project": {
            "path":
                str(input_project),

            "sha256_before":
                input_sha_before,

            "sha256_after":
                input_sha_after,

            "paint":
                paint_before,
        },

        "expected_filament_colours":
            expected_colours,

        "profiles": {
            "machine":
                str(resolved_machine),

            "process":
                str(resolved_process),

            "filaments":
                [
                    str(path)
                    for path in resolved_filaments
                ],
        },

        "slice": {
            "command":
                command,

            "forbidden_geometry_flags_present":
                forbidden,

            "returncode_raw":
                raw_rc,

            "returncode_signed":
                signed_rc,

            "elapsed_seconds":
                round(
                    time.time() - started,
                    3,
                ),

            "stdout_tail":
                cli_result.stdout[-3000:],

            "stderr_tail":
                cli_result.stderr[-3000:],
        },

        "gcode":
            inspection,

        "dual_material_evidence":
            evidence,

        "checks":
            checks,

        "failed_checks":
            failed_checks,

        "safety": {
            "printer_api_used":
                False,

            "printer_contacted":
                False,

            "artifact_uploaded":
                False,

            "print_command_sent":
                False,

            "print_started":
                False,

            "network_operation_requested":
                False,
        },

        "next_gate":
            "R3_PROJECT_INTEGRITY"
            if status == "slice_paint_proof_pass"
            else None,
    }

    write_json(
        report_path,
        report,
    )

    report["report_path"] = str(report_path)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline R2S proof that Bambu Studio converts the accepted "
            "single-mesh surface paint into real two-filament toolpaths."
        )
    )

    parser.add_argument(
        "--studio",
        required=True,
    )

    parser.add_argument(
        "--input-project",
        required=True,
    )

    parser.add_argument(
        "--surface-report",
        required=True,
    )

    parser.add_argument(
        "--legacy-manifest",
        required=True,
    )

    parser.add_argument(
        "--source-task-dir",
        required=True,
    )

    parser.add_argument(
        "--output-gcode",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args()

    result = run_slice_paint_proof(
        studio_exe=Path(args.studio),
        input_project=Path(args.input_project),
        surface_report=Path(args.surface_report),
        legacy_manifest=Path(args.legacy_manifest),
        source_task_dir=Path(args.source_task_dir),
        output_gcode=Path(args.output_gcode),
        report_path=Path(args.report),
    )

    evidence = result["dual_material_evidence"]
    gcode = result["gcode"]["gcode_usage"]

    print("=== R2S SLICE PAINT PROOF ===")
    print("UPSTREAM_SURFACE_PAINT_PASS=True")
    print("INPUT_PHYSICAL_MESH_COUNT=1")
    print("POST_PAINT_ORIENT_COUNT=0")
    print("POST_PAINT_ARRANGE_COUNT=0")
    print("INPUT_PROJECT_SHA_UNCHANGED=", result["checks"]["input_project_sha_unchanged"])
    print()
    print("FILAMENT_1=", evidence["material_rows"]["1"])
    print("FILAMENT_2=", evidence["material_rows"]["2"])
    print("LAYER_FILAMENT_UNION=", evidence["layer_filament_union_zero_based"])
    print()
    print("M620_COUNTS=", gcode["m620_counts"])
    print("M621_COUNTS=", gcode["m621_counts"])
    print("M620_LOGICAL_FILAMENTS=", gcode["m620_logical_filaments"])
    print("M621_LOGICAL_FILAMENTS=", gcode["m621_logical_filaments"])
    print("TOOLCHANGE_TRANSITIONS=", gcode["logical_toolchange_transitions"])
    print("EXTRUSION_COMMAND_COUNT=", gcode["extrusion_command_count"])
    print()
    print("FAILED_CHECKS=", result["failed_checks"])
    print("PRINTER_CONTACTED=False")
    print("ARTIFACT_UPLOADED=False")
    print("PRINT_COMMAND_SENT=False")
    print("PRINT_STARTED=False")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("GCODE=", result["gcode"]["path"])
    print("REPORT=", result["report_path"])

    return 0 if result["status"] == "slice_paint_proof_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
