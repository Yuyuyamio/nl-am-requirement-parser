from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any

from .ftps_probe_v32 import load_gate1_identity
from .gate4_runtime_v404 import (
    REQUEST_ID,
    EXPECTED_DEVICE_ID,
    _runtime_status_v404,
    _gate3_lock,
)

REPORT_NAME = "m4_gate4a_runtime_preflight_v406.json"


class Gate4V406Error(RuntimeError):
    pass


def _v40():
    return importlib.import_module("am_print_executor.gate4_runtime_v40")


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _runtime_preflight_v406(print_obj: dict[str, Any]) -> dict[str, Any]:
    gcode_state = str(print_obj.get("gcode_state", "")).upper().strip()
    print_error = print_obj.get("print_error")
    nozzle_diameter = str(print_obj.get("nozzle_diameter", "")).strip()
    sdcard = print_obj.get("sdcard")
    hms = print_obj.get("hms")
    mc_percent = _as_number(print_obj.get("mc_percent"))
    mc_remaining_time = _as_number(print_obj.get("mc_remaining_time"))

    print_error_zero = print_error == 0
    nozzle_diameter_0_4 = nozzle_diameter == "0.4"
    sdcard_available = sdcard is True
    hms_empty = isinstance(hms, list) and len(hms) == 0

    idle_terminal_state = gcode_state == "IDLE"
    finish_completed = (
        gcode_state == "FINISH"
        and mc_percent is not None
        and mc_percent >= 100.0
        and mc_remaining_time is not None
        and mc_remaining_time <= 0.0
        and print_error_zero
        and hms_empty
    )
    safe_terminal_state = idle_terminal_state or finish_completed

    checks = {
        "safe_terminal_state": safe_terminal_state,
        "gcode_state_idle": idle_terminal_state,
        "gcode_state_finish_completed": finish_completed,
        "print_error_zero": print_error_zero,
        "nozzle_diameter_0_4": nozzle_diameter_0_4,
        "sdcard_available": sdcard_available,
        "hms_empty": hms_empty,
    }

    passed = (
        safe_terminal_state
        and print_error_zero
        and nozzle_diameter_0_4
        and sdcard_available
        and hms_empty
    )

    return {
        "passed": passed,
        "checks": checks,
        "observed": {
            "gcode_state": gcode_state,
            "print_error": print_error,
            "nozzle_diameter": nozzle_diameter,
            "sdcard": sdcard,
            "hms": hms,
            "mc_percent": print_obj.get("mc_percent"),
            "mc_remaining_time": print_obj.get("mc_remaining_time"),
            "nozzle_temper": print_obj.get("nozzle_temper"),
            "bed_temper": print_obj.get("bed_temper"),
        },
        "state_interpretation": (
            "idle"
            if idle_terminal_state
            else "finished_completed_terminal_state"
            if finish_completed
            else "not_safe_terminal_state"
        ),
    }


def run_gate4a_v406(project_root: Path, access_code: str) -> dict[str, Any]:
    project_root = project_root.resolve()
    v40 = _v40()

    profile = load_gate1_identity(project_root, REQUEST_ID)
    if profile.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate4V406Error("Gate1 V3.1 locked DEVICE_ID mismatch.")

    ip_address = profile.get("ip_address")
    if not isinstance(ip_address, str) or not ip_address:
        raise Gate4V406Error("Gate1 V3.1 locked printer IP is missing.")

    lock = _gate3_lock(project_root)

    try:
        print_obj, telemetry = _runtime_status_v404(
            ip_address=ip_address,
            access_code=access_code,
        )
    except Exception as exc:
        raise Gate4V406Error(f"MQTT runtime status acquisition failed: {type(exc).__name__}: {exc}") from exc

    runtime = _runtime_preflight_v406(print_obj)
    if not runtime["passed"]:
        raise Gate4V406Error(
            f"Runtime safety checks failed: {runtime['checks']}; observed={runtime['observed']}"
        )

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 4,
        "version": "4.0.6",
        "stage": "runtime_preflight_v406",
        "request_id": REQUEST_ID,
        "status": "runtime_preflight_passed",
        "created_at": v40._utc_now(),
        "created_unix": time.time(),
        "device_id": EXPECTED_DEVICE_ID,
        "printer_ip": ip_address,
        "artifact_integrity_lock": lock,
        "runtime": runtime,
        "telemetry": telemetry,
        "policy": {
            "ftps_connection_attempted": False,
            "artifact_reuploaded": False,
            "access_code_stored": False,
            "mqtt_publish_count": telemetry["mqtt_publish_count"],
            "status_request_publish_count": telemetry["status_request_publish_count"],
            "control_command_count": 0,
            "print_start_command_count": 0,
            "heating_command_count": 0,
            "motion_command_count": 0,
            "print_started": False,
        },
        "manual_requirements_before_gate4b": {
            "printer_physically_not_moving": True,
            "print_bed_cleared_and_ready": True,
            "human_presence_required": True,
            "note": "These are human checks; Gate4A cannot verify the physical build plate is clear."
        },
        "next_phase": "m4_gate4b_manual_first_print_start_after_v406_review",
    }

    report_path = project_root / "outputs" / "m4" / REQUEST_ID / REPORT_NAME
    v40._write_json_atomic(report_path, report)
    return report | {"report_file": str(report_path)}
