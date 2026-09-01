from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from am_print_executor.r7_live_ams_mapping_v2 import (
    extract_ams_trays,
    match_material,
    read_live_status,
)


class R10PreflightError(RuntimeError):
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
        raise R10PreflightError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise R10PreflightError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise R10PreflightError(f"Expected JSON object: {path}")
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
        return float(value)
    except (TypeError, ValueError):
        return None


def final_safe_state(print_obj: dict[str, Any]) -> dict[str, Any]:
    state = str(print_obj.get("gcode_state") or "").strip().upper()

    active_states = {
        "PREPARE",
        "RUNNING",
        "SLICING",
        "PAUSE",
        "PAUSED",
    }

    terminal_or_idle_states = {
        "IDLE",
        "FINISH",
        "FAILED",
    }

    nozzle_target = as_float(
        print_obj.get("nozzle_target_temper")
    )
    bed_target = as_float(
        print_obj.get("bed_target_temper")
    )

    hms = print_obj.get("hms")

    checks = {
        "state_not_active":
            state not in active_states,

        "state_is_known_safe_terminal_or_idle":
            state in terminal_or_idle_states,

        "print_error_zero":
            print_obj.get("print_error") in (0, "0", None),

        "hms_empty":
            isinstance(hms, list) and len(hms) == 0,

        "sdcard_available":
            print_obj.get("sdcard") is True,

        "nozzle_target_safe":
            nozzle_target is not None and nozzle_target <= 40.0,

        "bed_target_safe":
            bed_target is not None and bed_target <= 40.0,
    }

    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "gcode_state": print_obj.get("gcode_state"),
            "print_error": print_obj.get("print_error"),
            "hms": hms,
            "sdcard": print_obj.get("sdcard"),
            "mc_percent": print_obj.get("mc_percent"),
            "mc_remaining_time": print_obj.get("mc_remaining_time"),
            "print_real_action": print_obj.get("print_real_action"),
            "print_gcode_action": print_obj.get("print_gcode_action"),
            "nozzle_temper": print_obj.get("nozzle_temper"),
            "nozzle_target_temper": print_obj.get("nozzle_target_temper"),
            "bed_temper": print_obj.get("bed_temper"),
            "bed_target_temper": print_obj.get("bed_target_temper"),
        },
    }


def run_r10(
    *,
    artifact_path: Path,
    r6_report_path: Path,
    r7_report_path: Path,
    r8_report_path: Path,
    r9_report_path: Path,
    expected_sha: str,
    ip_address: str,
    device_id: str,
    report_path: Path,
) -> dict[str, Any]:
    artifact_path = Path(artifact_path).resolve()

    if not artifact_path.is_file():
        raise R10PreflightError(
            f"Locked artifact missing: {artifact_path}"
        )

    r6 = load_json(r6_report_path)
    r7 = load_json(r7_report_path)
    r8 = load_json(r8_report_path)
    r9 = load_json(r9_report_path)

    required_statuses = {
        "r6": (
            r6.get("status")
            == "r6_toolchange_material_consistency_pass"
        ),
        "r7": (
            r7.get("status")
            == "r7_ams_mapping_pass"
        ),
        "r8": (
            r8.get("status")
            == "r8_material_capacity_preflight_pass"
        ),
        "r9": (
            r9.get("status")
            == "r9_upload_only_pass"
        ),
    }

    if not all(required_statuses.values()):
        raise R10PreflightError(
            f"Upstream gate not PASS: {required_statuses}"
        )

    local_sha = sha256_file(artifact_path)

    if local_sha.lower() != expected_sha.lower():
        raise R10PreflightError(
            "Local artifact SHA changed after R9."
        )

    r9_upload = r9.get("upload")
    if not isinstance(r9_upload, dict):
        raise R10PreflightError(
            "R9 report lacks upload block."
        )

    remote_sha = r9_upload.get("remote_sha256")
    remote_path = r9_upload.get("remote_path")

    if remote_sha != expected_sha:
        raise R10PreflightError(
            "R9 remote SHA does not match locked artifact."
        )

    if not isinstance(remote_path, str) or not remote_path.startswith("/"):
        raise R10PreflightError(
            "R9 remote path is invalid."
        )

    print("Enter current X1C LAN Access Code.")
    print("Input is hidden and is NOT written to disk.")
    access_code = getpass.getpass("X1C Access Code: ").strip()

    if not access_code:
        raise R10PreflightError(
            "Access Code is empty."
        )

    live = read_live_status(
        ip_address=ip_address,
        device_id=device_id,
        access_code=access_code,
    )

    print_obj = live["print_object"]
    safe = final_safe_state(print_obj)

    trays = extract_ams_trays(print_obj)

    gray = match_material(
        trays,
        logical_id=0,
        semantic_label="Gray",
        expected_color="#A6A9AA",
        expected_type="PLA",
    )

    yellow = match_material(
        trays,
        logical_id=1,
        semantic_label="Yellow",
        expected_color="#F4EE2A",
        expected_type="PLA",
    )

    current_mapping = None

    if gray["unique_match"] and yellow["unique_match"]:
        current_mapping = [
            gray["selected"]["global_tray_id"],
            yellow["selected"]["global_tray_id"],
        ]

    current_wire = None
    if current_mapping is not None:
        current_wire = [
            current_mapping[0],
            current_mapping[1],
            -1,
            -1,
            -1,
        ]

    checks = {
        "r6_pass": required_statuses["r6"],
        "r7_pass": required_statuses["r7"],
        "r8_pass": required_statuses["r8"],
        "r9_pass": required_statuses["r9"],

        "local_sha_locked":
            local_sha == expected_sha,

        "r9_remote_sha_locked":
            remote_sha == expected_sha,

        "r9_remote_size_verified":
            r9_upload.get("remote_size_verified") is True,

        "r9_remote_sha_verified":
            r9_upload.get("remote_sha256_verified") is True,

        "printer_final_safe_state":
            safe["passed"],

        "gray_live_unique_match":
            gray["unique_match"],

        "yellow_live_unique_match":
            yellow["unique_match"],

        "live_mapping_still_0_3":
            current_mapping == [0, 3],

        "live_wire_mapping_still_expected":
            current_wire == [0, 3, -1, -1, -1],
    }

    failed = [
        key
        for key, value in checks.items()
        if not value
    ]

    status = (
        "r10_final_preflight_pass"
        if not failed
        else "r10_final_preflight_fail"
    )

    report = {
        "schema_version": "r10-final-preflight-v2",
        "module": "M4",
        "stage": "R10_FINAL_PREFLIGHT",
        "created_unix": time.time(),
        "status": status,

        "device": {
            "device_id": device_id,
            "ip_address": ip_address,
        },

        "artifact": {
            "local_path": str(artifact_path),
            "local_sha256": local_sha,
            "expected_sha256": expected_sha,
            "remote_path": remote_path,
            "remote_sha256": remote_sha,
        },

        "live_printer_state": safe,

        "live_ams": {
            "gray": gray,
            "yellow": yellow,
            "logical_mapping": current_mapping,
            "x1c_wire_mapping": current_wire,
        },

        "checks": checks,
        "failed_checks": failed,

        "policy": {
            "access_code_stored": False,
            "r9_artifact_reuploaded": False,
            "old_ams_snapshot_reused": False,
        },

        "safety": {
            "network_used": True,
            "printer_contacted": True,
            "read_only_status_request_only": True,
            "ftps_upload_count": 0,
            "mqtt_project_file_publish_count": 0,
            "print_command_sent": False,
            "print_started": False,
            "heating_command_sent": False,
            "motion_command_sent": False,
            "parameter_adjustment_count": 0,
        },

        "next_gate": (
            "R11_START_CONTRACT"
            if status == "r10_final_preflight_pass"
            else None
        ),
    }

    write_json(report_path, report)
    report["report_path"] = str(Path(report_path).resolve())

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "R10 final read-only X1C preflight before the single "
            "R11 project_file print-start publish."
        )
    )

    parser.add_argument("--artifact", required=True)
    parser.add_argument("--r6-report", required=True)
    parser.add_argument("--r7-report", required=True)
    parser.add_argument("--r8-report", required=True)
    parser.add_argument("--r9-report", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--report", required=True)

    args = parser.parse_args()

    result = run_r10(
        artifact_path=Path(args.artifact),
        r6_report_path=Path(args.r6_report),
        r7_report_path=Path(args.r7_report),
        r8_report_path=Path(args.r8_report),
        r9_report_path=Path(args.r9_report),
        expected_sha=args.expected_sha,
        ip_address=args.ip,
        device_id=args.device_id,
        report_path=Path(args.report),
    )

    state = result["live_printer_state"]
    ams = result["live_ams"]

    print()
    print("=== R10 FINAL PREFLIGHT V2 ===")
    print("LOCAL_SHA256=", result["artifact"]["local_sha256"])
    print("REMOTE_SHA256=", result["artifact"]["remote_sha256"])
    print("REMOTE_PATH=", result["artifact"]["remote_path"])
    print()
    print("PRINTER_SAFE=", state["passed"])
    print("PRINTER_STATE=", state["observed"]["gcode_state"])
    print("PRINT_ERROR=", state["observed"]["print_error"])
    print("HMS=", state["observed"]["hms"])
    print("SDCARD=", state["observed"]["sdcard"])
    print("NOZZLE_TARGET=", state["observed"]["nozzle_target_temper"])
    print("BED_TARGET=", state["observed"]["bed_target_temper"])
    print()
    print("LIVE_AMS_MAPPING=", ams["logical_mapping"])
    print("LIVE_X1C_WIRE_MAPPING=", ams["x1c_wire_mapping"])
    print()
    print("FAILED_CHECKS=", result["failed_checks"])
    print("R9_ARTIFACT_REUPLOADED=False")
    print("PROJECT_FILE_COMMAND_SENT=False")
    print("PRINT_COMMAND_SENT=False")
    print("PRINT_STARTED=False")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("REPORT=", result["report_path"])

    return 0 if result["status"] == "r10_final_preflight_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
