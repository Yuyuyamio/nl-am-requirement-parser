from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import M4Error

EXPECTED_REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_MODEL = "Bambu Lab X1 Carbon"
EXPECTED_VARIANT = "X1C Combo"
EXPECTED_NOZZLE_MM = 0.4
EXPECTED_NOZZLE_MATERIAL = "hardened_steel"
EXPECTED_MATERIAL = "PLA"

_SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,63}$")
_FIRMWARE_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{2,31}$")


@dataclass(frozen=True)
class DeviceInput:
    printer_ip: str
    serial_number: str
    firmware_version: str
    material_source: str
    ams_present: bool
    network_mode: str
    physical_device_observed: bool
    printer_idle: bool
    build_plate_installed: bool
    build_plate_clean: bool
    chamber_clear: bool
    filament_loaded: bool
    nozzle_matches_profile: bool
    material_matches_profile: bool
    authorize_readonly_probe: bool


def _load_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise M4Error(code, f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise M4Error(code, f"Invalid JSON: {path}", details={"line": exc.lineno, "column": exc.colno}) from exc
    if not isinstance(value, dict):
        raise M4Error(code, f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise M4Error("M4_PHASE0_REQUIRED_FILE_MISSING", f"Required file does not exist: {path}") from exc
    return digest.hexdigest()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8-sig")
        if existing == canonical:
            return
        raise M4Error("M4_OUTPUT_CONFLICT", f"Refusing to overwrite a different report: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _resolve_path(value: str, base_dir: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def _validate_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise M4Error("M4_PHASE0_INVALID_PRINTER_IP", "Printer IP is invalid.", details={"value": value}) from exc
    if address.version != 4:
        raise M4Error("M4_PHASE0_INVALID_PRINTER_IP", "Only an IPv4 printer address is supported in this phase.")
    if not address.is_private:
        raise M4Error(
            "M4_PHASE0_NONPRIVATE_PRINTER_IP",
            "Printer IP must be a private LAN address.",
            details={"value": str(address)},
        )
    return str(address)


def _validate_text(value: str, regex: re.Pattern[str], code: str, label: str) -> str:
    cleaned = value.strip()
    if not regex.fullmatch(cleaned):
        raise M4Error(code, f"Invalid {label}.", details={"value": value})
    return cleaned


def _require_true(name: str, value: bool) -> None:
    if value is not True:
        raise M4Error(
            "M4_PHASE0_OPERATOR_CHECK_FAILED",
            f"Operator check is not confirmed: {name}",
            details={"check": name},
        )


def _validate_m3_acceptance(path: Path, request_id: str) -> dict[str, Any]:
    report = _load_json(path, code="M4_PHASE0_INVALID_M3_ACCEPTANCE")
    expected = {
        "module": "M3",
        "phase": 7,
        "stage": "final_acceptance",
        "request_id": request_id,
        "status": "m3_accepted",
        "m3_complete": True,
        "m4_activation_authorized": False,
        "printer_connection_attempted": False,
        "artifact_uploaded": False,
        "print_started": False,
        "next_module": "M4",
    }
    mismatches = {key: {"expected": expected_value, "actual": report.get(key)} for key, expected_value in expected.items() if report.get(key) != expected_value}
    if mismatches:
        raise M4Error("M4_PHASE0_M3_ACCEPTANCE_GATE_FAILED", "M3 final acceptance is not eligible for M4.", details=mismatches)
    return report


def create_device_readiness(
    *,
    project_root: Path,
    final_acceptance_path: Path,
    device_input: DeviceInput,
    request_id: str = EXPECTED_REQUEST_ID,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    final_acceptance_path = final_acceptance_path.resolve()
    final_acceptance = _validate_m3_acceptance(final_acceptance_path, request_id)

    handoff_package_value = final_acceptance.get("handoff_package")
    package_sha_value = final_acceptance.get("package_sha256")
    if not isinstance(handoff_package_value, str) or not handoff_package_value:
        raise M4Error("M4_PHASE0_M3_ACCEPTANCE_INCOMPLETE", "M3 final acceptance does not identify the handoff package.")
    if not isinstance(package_sha_value, str) or len(package_sha_value) != 64:
        raise M4Error("M4_PHASE0_M3_ACCEPTANCE_INCOMPLETE", "M3 final acceptance does not contain a valid package SHA-256.")

    handoff_package = _resolve_path(handoff_package_value, final_acceptance_path.parent)
    actual_package_sha = _sha256(handoff_package)
    if actual_package_sha.lower() != package_sha_value.lower():
        raise M4Error(
            "M4_PHASE0_HANDOFF_PACKAGE_HASH_MISMATCH",
            "M3 handoff package changed after final acceptance.",
            details={"expected": package_sha_value, "actual": actual_package_sha},
        )

    printer_ip = _validate_ip(device_input.printer_ip)
    serial_number = _validate_text(
        device_input.serial_number,
        _SERIAL_RE,
        "M4_PHASE0_INVALID_SERIAL_NUMBER",
        "printer serial number",
    )
    firmware_version = _validate_text(
        device_input.firmware_version,
        _FIRMWARE_RE,
        "M4_PHASE0_INVALID_FIRMWARE_VERSION",
        "firmware version",
    )

    material_source = device_input.material_source.strip().lower()
    if material_source not in {"external_spool", "ams"}:
        raise M4Error("M4_PHASE0_INVALID_MATERIAL_SOURCE", "Material source must be external_spool or ams.")
    if material_source == "ams" and device_input.ams_present is not True:
        raise M4Error("M4_PHASE0_AMS_CONTRADICTION", "AMS material source requires ams_present=true.")

    network_mode = device_input.network_mode.strip().lower()
    if network_mode not in {"cloud", "lan_only"}:
        raise M4Error("M4_PHASE0_INVALID_NETWORK_MODE", "Network mode must be cloud or lan_only.")

    for check_name in (
        "physical_device_observed",
        "printer_idle",
        "build_plate_installed",
        "build_plate_clean",
        "chamber_clear",
        "filament_loaded",
        "nozzle_matches_profile",
        "material_matches_profile",
    ):
        _require_true(check_name, getattr(device_input, check_name))

    status = "device_gate_ready" if device_input.authorize_readonly_probe else "readonly_authorization_required"
    next_phase = "m4_phase1_readonly_connection_probe" if device_input.authorize_readonly_probe else None

    task_dir = project_root / "outputs" / "m4" / request_id
    output_path = task_dir / "m4_device_readiness.json"

    report: dict[str, Any] = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 0,
        "stage": "device_readiness",
        "request_id": request_id,
        "status": status,
        "source_m3_final_acceptance": str(final_acceptance_path),
        "source_m3_final_acceptance_sha256": _sha256(final_acceptance_path),
        "source_handoff_package": str(handoff_package),
        "source_handoff_package_sha256": actual_package_sha,
        "printer": {
            "manufacturer": "Bambu Lab",
            "model": EXPECTED_MODEL,
            "variant": EXPECTED_VARIANT,
            "serial_number": serial_number,
            "firmware_version": firmware_version,
            "ip_address": printer_ip,
            "network_mode": network_mode,
            "nozzle_diameter_mm": EXPECTED_NOZZLE_MM,
            "nozzle_material": EXPECTED_NOZZLE_MATERIAL,
            "material_type": EXPECTED_MATERIAL,
            "material_source": material_source,
            "ams_present": device_input.ams_present,
        },
        "operator_checks": {
            "physical_device_observed": device_input.physical_device_observed,
            "printer_idle": device_input.printer_idle,
            "build_plate_installed": device_input.build_plate_installed,
            "build_plate_clean": device_input.build_plate_clean,
            "chamber_clear": device_input.chamber_clear,
            "filament_loaded": device_input.filament_loaded,
            "nozzle_matches_profile": device_input.nozzle_matches_profile,
            "material_matches_profile": device_input.material_matches_profile,
        },
        "security": {
            "access_code_requested": False,
            "access_code_stored": False,
            "printer_connection_attempted": False,
            "artifact_uploaded": False,
            "print_started": False,
        },
        "authorizations": {
            "readonly_connection_probe_authorized": device_input.authorize_readonly_probe,
            "artifact_upload_authorized": False,
            "print_start_authorized": False,
        },
        "hard_constraints_passed": True,
        "next_phase": next_phase,
    }

    _atomic_write_json(output_path, report)
    return {
        "schema_version": report["schema_version"],
        "module": report["module"],
        "phase": report["phase"],
        "stage": report["stage"],
        "request_id": request_id,
        "status": status,
        "device_readiness_file": str(output_path),
        "printer_ip": printer_ip,
        "serial_number": serial_number,
        "firmware_version": firmware_version,
        "readonly_connection_probe_authorized": device_input.authorize_readonly_probe,
        "printer_connection_attempted": False,
        "artifact_uploaded": False,
        "print_started": False,
        "next_phase": next_phase,
    }
