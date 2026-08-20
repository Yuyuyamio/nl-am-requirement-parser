from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile

from pathlib import Path
from typing import Any

from am_print_executor.r2s_slice_paint_proof import inspect_gcode_3mf
from am_print_executor.r3_project_integrity_v2 import scan_legacy_markers


class R6ConsistencyError(RuntimeError):
    pass


_T_RE = re.compile(
    r"^\s*T(\d+)\s*(?:;.*)?$",
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
        raise R6ConsistencyError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise R6ConsistencyError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise R6ConsistencyError(f"Expected JSON object: {path}")

    return obj


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def norm_colour(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text and not text.startswith("#"):
        text = "#" + text
    return text


def read_project_settings(gcode_3mf: Path) -> dict[str, Any]:
    with zipfile.ZipFile(gcode_3mf, "r") as zf:
        name = "Metadata/project_settings.config"

        if name not in zf.namelist():
            raise R6ConsistencyError(
                "G-code 3MF lacks Metadata/project_settings.config."
            )

        try:
            data = json.loads(
                zf.read(name).decode("utf-8-sig")
            )
        except Exception as exc:
            raise R6ConsistencyError(
                f"Cannot parse project_settings.config: {exc}"
            ) from exc

    if not isinstance(data, dict):
        raise R6ConsistencyError(
            "project_settings.config must be a JSON object."
        )

    return data


def extract_gcode_text(gcode_3mf: Path) -> str:
    inspected = inspect_gcode_3mf(gcode_3mf)
    member = inspected["gcode_member"]

    with zipfile.ZipFile(gcode_3mf, "r") as zf:
        return zf.read(member).decode(
            "utf-8",
            errors="replace",
        )


def filtered_logical_sequence(
    values: list[int],
) -> list[int]:
    return [
        int(value)
        for value in values
        if int(value) in {0, 1}
    ]


def transition_count(
    sequence: list[int],
) -> int:
    return sum(
        1
        for previous, current in zip(
            sequence,
            sequence[1:],
        )
        if previous != current
    )


def material_rows(
    *,
    inspected: dict[str, Any],
    settings: dict[str, Any],
    expected_colours: list[str],
) -> dict[str, Any]:
    slice_rows = {
        row.get("id_int"): row
        for row in inspected["slice_info"]["filaments"]
        if row.get("id_int") in {1, 2}
    }

    setting_colours = settings.get("filament_colour")
    setting_types = settings.get("filament_type")
    setting_ids = settings.get("filament_settings_id")

    if not isinstance(setting_colours, list):
        setting_colours = []

    if not isinstance(setting_types, list):
        setting_types = []

    if not isinstance(setting_ids, list):
        setting_ids = []

    rows: dict[str, Any] = {}

    labels = {
        0: "Gray",
        1: "Yellow",
    }

    for logical_id in (0, 1):
        slice_id = logical_id + 1
        row = slice_rows.get(slice_id)

        project_colour = (
            norm_colour(setting_colours[logical_id])
            if logical_id < len(setting_colours)
            else None
        )

        project_type = (
            str(setting_types[logical_id]).strip().upper()
            if logical_id < len(setting_types)
            else None
        )

        project_profile = (
            str(setting_ids[logical_id])
            if logical_id < len(setting_ids)
            else None
        )

        slice_colour = (
            norm_colour(row.get("color"))
            if row is not None
            else None
        )

        slice_type = (
            str(row.get("type") or "").strip().upper()
            if row is not None
            else None
        )

        expected_colour = norm_colour(
            expected_colours[logical_id]
        )

        rows[str(logical_id)] = {
            "logical_filament_id_zero_based":
                logical_id,

            "slice_info_filament_id_one_based":
                slice_id,

            "semantic_label":
                labels[logical_id],

            "expected_colour":
                expected_colour,

            "project_colour":
                project_colour,

            "slice_colour":
                slice_colour,

            "project_type":
                project_type,

            "slice_type":
                slice_type,

            "project_filament_settings_id":
                project_profile,

            "used_for_object":
                bool(
                    row
                    and row.get("used_for_object_bool")
                ),

            "used_g":
                (
                    row.get("used_g_float")
                    if row
                    else None
                ),

            "used_m":
                (
                    row.get("used_m_float")
                    if row
                    else None
                ),

            "colour_consistent":
                (
                    project_colour == expected_colour
                    and slice_colour == expected_colour
                ),

            "type_consistent":
                (
                    project_type == "PLA"
                    and slice_type == "PLA"
                ),

            "positive_usage":
                bool(
                    row
                    and row.get("used_g_float") is not None
                    and row.get("used_g_float") > 0
                    and row.get("used_m_float") is not None
                    and row.get("used_m_float") > 0
                ),

            "physical_ams_slot":
                None,
        }

    return rows


def sequence_audit(
    inspected: dict[str, Any],
    gcode_text: str,
) -> dict[str, Any]:
    usage = inspected["gcode_usage"]

    m620_all = [
        int(value)
        for value in usage["m620_sequence"]
    ]

    m621_all = [
        int(value)
        for value in usage["m621_sequence"]
    ]

    m620 = filtered_logical_sequence(m620_all)
    m621 = filtered_logical_sequence(m621_all)

    t_all = [
        int(value)
        for value in _T_RE.findall(gcode_text)
    ]

    t_logical = sorted(
        {
            value
            for value in t_all
            if value in {0, 1}
        }
    )

    m620_set = sorted(set(m620))
    m621_set = sorted(set(m621))

    return {
        "m620_all": m620_all,
        "m621_all": m621_all,
        "m620_logical_sequence": m620,
        "m621_logical_sequence": m621,
        "m620_logical_set": m620_set,
        "m621_logical_set": m621_set,
        "m620_transition_count": transition_count(m620),
        "m621_transition_count": transition_count(m621),
        "m620_m621_logical_sequence_identical": m620 == m621,
        "t_commands_all": t_all,
        "t_logical_set": t_logical,
        "special_255_present_in_m620": 255 in m620_all,
        "special_255_present_in_m621": 255 in m621_all,
    }


def run_r6(
    *,
    gcode_path: Path,
    r5_report_path: Path,
    surface_report_path: Path,
    expected_gcode_sha: str,
    output_report: Path,
) -> dict[str, Any]:
    gcode_path = Path(gcode_path).resolve()

    if not gcode_path.is_file():
        raise R6ConsistencyError(
            f"R5/R4 G-code artifact missing: {gcode_path}"
        )

    r5 = load_json(r5_report_path)
    surface = load_json(surface_report_path)

    if r5.get("status") != "r5_printability_pass":
        raise R6ConsistencyError(
            "Upstream R5 is not PASS."
        )

    if (
        surface.get("status")
        != "surface_painted_multimaterial_project_ready"
    ):
        raise R6ConsistencyError(
            "Upstream surface-painted material semantics report is not PASS."
        )

    expected_colours = (
        surface
        .get("project", {})
        .get("filament_colours")
    )

    if (
        not isinstance(expected_colours, list)
        or len(expected_colours) != 2
    ):
        raise R6ConsistencyError(
            "Surface report does not expose exactly two filament colours."
        )

    gcode_sha = sha256_file(gcode_path)

    inspected = inspect_gcode_3mf(
        gcode_path
    )

    settings = read_project_settings(
        gcode_path
    )

    gcode_text = extract_gcode_text(
        gcode_path
    )

    rows = material_rows(
        inspected=inspected,
        settings=settings,
        expected_colours=expected_colours,
    )

    seq = sequence_audit(
        inspected,
        gcode_text,
    )

    legacy = scan_legacy_markers(
        gcode_path
    )

    r5_gcode_sha = (
        r5.get("artifacts", {})
        .get("gcode_sha256")
    )

    checks = {
        "r5_report_pass":
            r5.get("status")
            == "r5_printability_pass",

        "gcode_sha_matches_expected":
            gcode_sha.lower()
            == expected_gcode_sha.lower(),

        "gcode_sha_matches_r5":
            (
                isinstance(r5_gcode_sha, str)
                and gcode_sha.lower()
                == r5_gcode_sha.lower()
            ),

        "exactly_two_logical_material_rows":
            set(rows.keys()) == {"0", "1"},

        "logical_0_is_gray":
            rows["0"]["semantic_label"] == "Gray",

        "logical_1_is_yellow":
            rows["1"]["semantic_label"] == "Yellow",

        "logical_0_colour_consistent":
            rows["0"]["colour_consistent"],

        "logical_1_colour_consistent":
            rows["1"]["colour_consistent"],

        "logical_0_type_consistent":
            rows["0"]["type_consistent"],

        "logical_1_type_consistent":
            rows["1"]["type_consistent"],

        "logical_0_used_for_object":
            rows["0"]["used_for_object"],

        "logical_1_used_for_object":
            rows["1"]["used_for_object"],

        "logical_0_positive_usage":
            rows["0"]["positive_usage"],

        "logical_1_positive_usage":
            rows["1"]["positive_usage"],

        "m620_uses_exactly_logical_0_and_1":
            seq["m620_logical_set"] == [0, 1],

        "m621_uses_exactly_logical_0_and_1":
            seq["m621_logical_set"] == [0, 1],

        "m620_m621_sequences_identical":
            seq["m620_m621_logical_sequence_identical"],

        "m620_transitions_present":
            seq["m620_transition_count"] > 0,

        "m621_transitions_present":
            seq["m621_transition_count"] > 0,

        "m620_m621_transition_counts_match":
            (
                seq["m620_transition_count"]
                == seq["m621_transition_count"]
            ),

        "no_unknown_logical_m620_tools":
            all(
                value in {0, 1, 255}
                for value in seq["m620_all"]
            ),

        "no_unknown_logical_m621_tools":
            all(
                value in {0, 1, 255}
                for value in seq["m621_all"]
            ),

        "legacy_partition_markers_absent":
            legacy["passed"],
    }

    failed = [
        key
        for key, value in checks.items()
        if not value
    ]

    status = (
        "r6_toolchange_material_consistency_pass"
        if not failed
        else "r6_toolchange_material_consistency_fail"
    )

    report = {
        "schema_version":
            "r6-toolchange-material-consistency-v2",

        "module":
            "M4",

        "stage":
            "R6_TOOLCHANGE_MATERIAL_CONSISTENCY",

        "status":
            status,

        "artifact": {
            "gcode":
                str(gcode_path),

            "gcode_sha256":
                gcode_sha,
        },

        "upstream": {
            "r5_report":
                str(Path(r5_report_path).resolve()),

            "r5_status":
                r5.get("status"),

            "surface_report":
                str(Path(surface_report_path).resolve()),

            "surface_status":
                surface.get("status"),
        },

        "logical_materials":
            rows,

        "toolchange_sequence":
            seq,

        "legacy_scan":
            legacy,

        "checks":
            checks,

        "failed_checks":
            failed,

        "mapping_policy": {
            "logical_ids_are_not_ams_slots":
                True,

            "physical_ams_mapping_performed_here":
                False,

            "physical_ams_mapping_next_gate":
                "R7_AMS_MAPPING",
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

            "print_command_sent":
                False,

            "print_started":
                False,
        },

        "next_gate":
            (
                "R7_AMS_MAPPING"
                if status
                == "r6_toolchange_material_consistency_pass"
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
            "Read-only R6 audit: prove Gray/Yellow logical filament identity "
            "and M620/M621 toolchange consistency before any AMS slot mapping."
        )
    )

    parser.add_argument(
        "--gcode",
        required=True,
    )

    parser.add_argument(
        "--r5-report",
        required=True,
    )

    parser.add_argument(
        "--surface-report",
        required=True,
    )

    parser.add_argument(
        "--expected-gcode-sha",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args()

    result = run_r6(
        gcode_path=Path(args.gcode),
        r5_report_path=Path(args.r5_report),
        surface_report_path=Path(args.surface_report),
        expected_gcode_sha=args.expected_gcode_sha,
        output_report=Path(args.report),
    )

    materials = result["logical_materials"]
    seq = result["toolchange_sequence"]

    print("=== R6 TOOLCHANGE / MATERIAL CONSISTENCY V2 ===")
    print(
        "GCODE_SHA256=",
        result["artifact"]["gcode_sha256"],
    )
    print()
    print("LOGICAL_0=", materials["0"])
    print("LOGICAL_1=", materials["1"])
    print()
    print(
        "M620_LOGICAL_SET=",
        seq["m620_logical_set"],
    )
    print(
        "M621_LOGICAL_SET=",
        seq["m621_logical_set"],
    )
    print(
        "M620_TRANSITIONS=",
        seq["m620_transition_count"],
    )
    print(
        "M621_TRANSITIONS=",
        seq["m621_transition_count"],
    )
    print(
        "M620_M621_SEQUENCE_IDENTICAL=",
        seq["m620_m621_logical_sequence_identical"],
    )
    print(
        "SPECIAL_255_PRESENT_M620=",
        seq["special_255_present_in_m620"],
    )
    print(
        "SPECIAL_255_PRESENT_M621=",
        seq["special_255_present_in_m621"],
    )
    print()
    print(
        "PHYSICAL_AMS_MAPPING_PERFORMED=False"
    )
    print(
        "LOGICAL_IDS_ARE_AMS_SLOTS=False"
    )
    print("FAILED_CHECKS=", result["failed_checks"])
    print("FILES_MODIFIED=False")
    print("NETWORK_USED=False")
    print("PRINTER_CONTACTED=False")
    print("ARTIFACT_UPLOADED=False")
    print("PRINT_COMMAND_SENT=False")
    print("PRINT_STARTED=False")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("REPORT=", result["report_path"])

    return (
        0
        if result["status"]
        == "r6_toolchange_material_consistency_pass"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
