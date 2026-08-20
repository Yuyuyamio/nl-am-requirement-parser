from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any


class R8CapacityError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise R8CapacityError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise R8CapacityError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise R8CapacityError(f"Expected JSON object: {path}")

    return obj


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def required_with_margin(
    used_g: float,
    margin_ratio: float,
) -> float:
    return round(float(used_g) * (1.0 + float(margin_ratio)), 2)


def selected_tray(
    r7: dict[str, Any],
    logical_id: str,
) -> dict[str, Any]:
    row = (
        r7.get("logical_to_physical", {})
        .get(logical_id)
    )

    if not isinstance(row, dict):
        raise R8CapacityError(
            f"R7 logical material {logical_id} missing."
        )

    selected = row.get("selected")

    if not isinstance(selected, dict):
        raise R8CapacityError(
            f"R7 logical material {logical_id} has no unique selected tray."
        )

    return selected


def demand_from_r5(
    r5: dict[str, Any],
) -> dict[str, float]:
    info = r5.get("gcode_printability")

    if not isinstance(info, dict):
        raise R8CapacityError(
            "R5 report lacks gcode_printability."
        )

    gray = as_float(
        info.get("filament_1_used_g")
    )

    yellow = as_float(
        info.get("filament_2_used_g")
    )

    if gray is None or gray <= 0:
        raise R8CapacityError(
            "R5 Gray demand is missing/non-positive."
        )

    if yellow is None or yellow <= 0:
        raise R8CapacityError(
            "R5 Yellow demand is missing/non-positive."
        )

    return {
        "gray_used_g": gray,
        "yellow_used_g": yellow,
    }


def capacity_telemetry(
    tray: dict[str, Any],
) -> dict[str, Any]:
    remain = tray.get("remain")
    nominal_weight = tray.get("tray_weight")

    return {
        "global_tray_id":
            tray.get("global_tray_id"),

        "human_slot":
            tray.get("human_slot"),

        "tray_color":
            tray.get("tray_color"),

        "tray_type":
            tray.get("tray_type"),

        "tray_sub_brands":
            tray.get("tray_sub_brands"),

        "tray_uuid":
            tray.get("tray_uuid"),

        "remain_raw":
            remain,

        "tray_weight_raw":
            nominal_weight,

        "remain_interpreted_as_grams":
            False,

        "tray_weight_interpreted_as_current_remaining_grams":
            False,

        "automatic_remaining_grams":
            None,

        "automatic_capacity_proven":
            False,
    }


def run_r8(
    *,
    r5_report_path: Path,
    r7_report_path: Path,
    output_report: Path,
    margin_ratio: float,
    operator_confirm_gray: bool,
    operator_confirm_yellow: bool,
) -> dict[str, Any]:
    if margin_ratio < 0 or margin_ratio > 1:
        raise R8CapacityError(
            "margin_ratio must be between 0 and 1."
        )

    r5 = load_json(r5_report_path)
    r7 = load_json(r7_report_path)

    if r5.get("status") != "r5_printability_pass":
        raise R8CapacityError(
            "Upstream R5 is not PASS."
        )

    if r7.get("status") != "r7_ams_mapping_pass":
        raise R8CapacityError(
            "Upstream R7 is not PASS."
        )

    demand = demand_from_r5(r5)

    gray_tray = selected_tray(r7, "0")
    yellow_tray = selected_tray(r7, "1")

    gray_need = required_with_margin(
        demand["gray_used_g"],
        margin_ratio,
    )

    yellow_need = required_with_margin(
        demand["yellow_used_g"],
        margin_ratio,
    )

    gray_telemetry = capacity_telemetry(
        gray_tray
    )

    yellow_telemetry = capacity_telemetry(
        yellow_tray
    )

    material_rows = {
        "0": {
            "semantic_label":
                "Gray",

            "used_g":
                demand["gray_used_g"],

            "required_with_margin_g":
                gray_need,

            "margin_ratio":
                margin_ratio,

            "tray":
                gray_telemetry,

            "operator_confirmed_sufficient":
                operator_confirm_gray,

            "capacity_basis":
                (
                    "operator_confirmation"
                    if operator_confirm_gray
                    else "unproven"
                ),
        },

        "1": {
            "semantic_label":
                "Yellow",

            "used_g":
                demand["yellow_used_g"],

            "required_with_margin_g":
                yellow_need,

            "margin_ratio":
                margin_ratio,

            "tray":
                yellow_telemetry,

            "operator_confirmed_sufficient":
                operator_confirm_yellow,

            "capacity_basis":
                (
                    "operator_confirmation"
                    if operator_confirm_yellow
                    else "unproven"
                ),
        },
    }

    r7_mapping = r7.get(
        "ams_mapping_two_logical_filaments"
    )

    checks = {
        "r5_pass":
            r5.get("status")
            == "r5_printability_pass",

        "r7_pass":
            r7.get("status")
            == "r7_ams_mapping_pass",

        "gray_demand_positive":
            demand["gray_used_g"] > 0,

        "yellow_demand_positive":
            demand["yellow_used_g"] > 0,

        "gray_tray_is_live_selected":
            gray_tray.get("global_tray_id")
            == 0
            or (
                isinstance(r7_mapping, list)
                and len(r7_mapping) >= 1
                and gray_tray.get("global_tray_id")
                == r7_mapping[0]
            ),

        "yellow_tray_is_live_selected":
            (
                isinstance(r7_mapping, list)
                and len(r7_mapping) >= 2
                and yellow_tray.get("global_tray_id")
                == r7_mapping[1]
            ),

        "gray_operator_capacity_confirmed":
            operator_confirm_gray,

        "yellow_operator_capacity_confirmed":
            operator_confirm_yellow,

        "remain_not_misinterpreted_as_grams":
            True,

        "tray_weight_not_misinterpreted_as_remaining_grams":
            True,
    }

    failed = [
        key
        for key, value in checks.items()
        if not value
    ]

    status = (
        "r8_material_capacity_preflight_pass"
        if not failed
        else "r8_material_capacity_manual_confirmation_required"
    )

    report = {
        "schema_version":
            "r8-material-capacity-preflight-v2",

        "module":
            "M4",

        "stage":
            "R8_MATERIAL_CAPACITY_PREFLIGHT",

        "created_unix":
            time.time(),

        "status":
            status,

        "upstream": {
            "r5_report":
                str(Path(r5_report_path).resolve()),

            "r5_status":
                r5.get("status"),

            "r7_report":
                str(Path(r7_report_path).resolve()),

            "r7_status":
                r7.get("status"),

            "live_ams_mapping":
                r7_mapping,

            "x1c_wire_mapping":
                r7.get("x1c_wire_mapping"),
        },

        "policy": {
            "capacity_safety_margin_ratio":
                margin_ratio,

            "remain_field_unit_assumed":
                None,

            "remain_interpreted_as_grams":
                False,

            "tray_weight_interpreted_as_current_remaining_grams":
                False,

            "operator_confirmation_is_explicit":
                True,

            "automatic_capacity_claim":
                False,
        },

        "materials":
            material_rows,

        "checks":
            checks,

        "failed_checks":
            failed,

        "safety": {
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

            "printer_parameters_changed":
                False,
        },

        "next_gate":
            (
                "R9_UPLOAD_ONLY"
                if status
                == "r8_material_capacity_preflight_pass"
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
            "R8 conservative material-capacity preflight. "
            "Bambu AMS remain/tray_weight telemetry is preserved but "
            "never silently converted to available grams."
        )
    )

    parser.add_argument(
        "--r5-report",
        required=True,
    )

    parser.add_argument(
        "--r7-report",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    parser.add_argument(
        "--margin-ratio",
        type=float,
        default=0.20,
    )

    parser.add_argument(
        "--confirm-gray-sufficient",
        action="store_true",
    )

    parser.add_argument(
        "--confirm-yellow-sufficient",
        action="store_true",
    )

    args = parser.parse_args()

    result = run_r8(
        r5_report_path=Path(args.r5_report),
        r7_report_path=Path(args.r7_report),
        output_report=Path(args.report),
        margin_ratio=args.margin_ratio,
        operator_confirm_gray=args.confirm_gray_sufficient,
        operator_confirm_yellow=args.confirm_yellow_sufficient,
    )

    gray = result["materials"]["0"]
    yellow = result["materials"]["1"]

    print("=== R8 MATERIAL CAPACITY PREFLIGHT V2 ===")
    print()
    print("GRAY_USED_G=", gray["used_g"])
    print(
        "GRAY_REQUIRED_WITH_MARGIN_G=",
        gray["required_with_margin_g"],
    )
    print(
        "GRAY_LIVE_TRAY=",
        gray["tray"]["global_tray_id"],
    )
    print(
        "GRAY_REMAIN_RAW=",
        gray["tray"]["remain_raw"],
    )
    print(
        "GRAY_OPERATOR_CONFIRMED=",
        gray["operator_confirmed_sufficient"],
    )
    print()
    print("YELLOW_USED_G=", yellow["used_g"])
    print(
        "YELLOW_REQUIRED_WITH_MARGIN_G=",
        yellow["required_with_margin_g"],
    )
    print(
        "YELLOW_LIVE_TRAY=",
        yellow["tray"]["global_tray_id"],
    )
    print(
        "YELLOW_REMAIN_RAW=",
        yellow["tray"]["remain_raw"],
    )
    print(
        "YELLOW_OPERATOR_CONFIRMED=",
        yellow["operator_confirmed_sufficient"],
    )
    print()
    print("REMAIN_INTERPRETED_AS_GRAMS=False")
    print(
        "TRAY_WEIGHT_INTERPRETED_AS_CURRENT_REMAINING_GRAMS=False"
    )
    print("FAILED_CHECKS=", result["failed_checks"])
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
        == "r8_material_capacity_preflight_pass"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
