from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .x1c_connection import (
    MQTT_PORT,
    MQTT_USERNAME,
    PrinterConnectionError,
    connect_printer,
)


REQUEST_ID_DEFAULT = "M2-1E4B2301FADD"


class ReadonlyProbeError(RuntimeError):
    """Raised when the read-only printer probe cannot be completed safely."""


@dataclass(frozen=True)
class DeviceIdentity:
    ip_address: str
    serial_number: str
    firmware_version: str | None = None


@dataclass(frozen=True)
class ProbeAttempt:
    attempt: int
    success: bool
    connected: bool
    subscribed: bool
    message_received: bool
    elapsed_seconds: float
    error: str | None
    status_summary: dict[str, Any] | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_private_ipv4(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ReadonlyProbeError(f"Invalid printer IP address: {value!r}") from exc
    if address.version != 4:
        raise ReadonlyProbeError("Gate 1 currently requires an IPv4 printer address.")
    if not address.is_private:
        raise ReadonlyProbeError(
            "The printer IP is not private. Do not expose Developer Mode MQTT to the public Internet."
        )
    return str(address)


def validate_serial(value: str) -> str:
    serial = value.strip()
    if len(serial) < 8 or len(serial) > 64:
        raise ReadonlyProbeError("Printer serial number length is not plausible.")
    if not all(character.isalnum() or character in {"-", "_"} for character in serial):
        raise ReadonlyProbeError("Printer serial number contains unsupported characters.")
    return serial


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_tls_certificate_fingerprint(
    ip_address: str,
    *,
    port: int = MQTT_PORT,
    timeout_seconds: float = 6.0,
) -> str:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip_address, port), timeout=timeout_seconds) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=ip_address) as tls_socket:
                certificate = tls_socket.getpeercert(binary_form=True)
    except OSError as exc:
        raise ReadonlyProbeError(
            f"Unable to open TLS connection to {ip_address}:{port}: {exc}"
        ) from exc
    if not certificate:
        raise ReadonlyProbeError("The printer did not provide a TLS certificate.")
    return hashlib.sha256(certificate).hexdigest().upper()


def format_fingerprint(value: str) -> str:
    compact = value.replace(":", "").upper()
    return ":".join(compact[index : index + 2] for index in range(0, len(compact), 2))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadonlyProbeError(f"Cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReadonlyProbeError(f"Expected a JSON object in {path}")
    return data


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_m3_acceptance(project_root: Path, request_id: str) -> Path:
    outputs_root = project_root / "outputs" / "m3"
    candidates = list(outputs_root.rglob("m3_final_acceptance.json")) if outputs_root.exists() else []
    matches: list[Path] = []
    for candidate in candidates:
        try:
            data = _load_json(candidate)
        except ReadonlyProbeError:
            continue
        if data.get("request_id") != request_id:
            continue
        if data.get("status") != "m3_accepted" or data.get("m3_complete") is not True:
            continue
        if data.get("m4_activation_authorized") is not False:
            continue
        matches.append(candidate)
    if len(matches) != 1:
        raise ReadonlyProbeError(
            f"Expected exactly one accepted M3 report for {request_id}; found {len(matches)}."
        )
    return matches[0]


def ensure_tls_pin(
    pin_path: Path,
    identity: DeviceIdentity,
    observed_fingerprint: str,
    confirmation_reader: Callable[[str], str] = input,
) -> dict[str, Any]:
    observed = observed_fingerprint.replace(":", "").upper()
    if pin_path.exists():
        pin = _load_json(pin_path)
        expected = str(pin.get("certificate_sha256", "")).replace(":", "").upper()
        if pin.get("ip_address") != identity.ip_address:
            raise ReadonlyProbeError("Pinned printer IP does not match the current IP.")
        if pin.get("serial_number") != identity.serial_number:
            raise ReadonlyProbeError("Pinned printer serial does not match the current serial.")
        if expected != observed:
            raise ReadonlyProbeError(
                "Printer TLS certificate fingerprint changed. Stop and verify the physical printer before continuing."
            )
        return pin

    formatted = format_fingerprint(observed)
    suffix = observed[-8:]
    print("\nFIRST-CONNECTION TLS FINGERPRINT")
    print(formatted)
    print("Check that you are on the intended private LAN and physically looking at the intended X1C.")
    entered = confirmation_reader(
        f"Type the final 8 characters {suffix} to pin this printer certificate: "
    ).strip().replace(":", "").upper()
    if entered != suffix:
        raise ReadonlyProbeError("TLS fingerprint confirmation was not accepted.")

    pin = {
        "schema_version": "0.1.0",
        "module": "M4",
        "stage": "printer_tls_pin",
        "request_id": REQUEST_ID_DEFAULT,
        "ip_address": identity.ip_address,
        "serial_number": identity.serial_number,
        "certificate_sha256": observed,
        "certificate_sha256_formatted": formatted,
        "trust_model": "trust_on_first_use_with_manual_suffix_confirmation",
        "created_at": utc_now(),
        "access_code_stored": False,
    }
    _write_json_atomic(pin_path, pin)
    return pin


def extract_status_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    print_state = payload.get("print")
    if not isinstance(print_state, dict):
        return None

    keys = (
        "gcode_state",
        "mc_percent",
        "mc_remaining_time",
        "nozzle_temper",
        "bed_temper",
        "chamber_temper",
        "wifi_signal",
        "print_error",
        "lifecycle",
        "stg_cur",
        "stg",
        "ams_status",
    )
    summary = {key: print_state.get(key) for key in keys if key in print_state}
    summary["report_has_print_object"] = True
    summary["received_at"] = utc_now()
    return summary


def run_single_passive_attempt(
    identity: DeviceIdentity,
    access_code: str,
    *,
    timeout_seconds: float = 15.0,
) -> ProbeAttempt:
    started = time.monotonic()
    try:
        status = connect_printer(
            ip=identity.ip_address,
            access_code=access_code,
            device_id=identity.serial_number,
            timeout_seconds=timeout_seconds,
            attempts=1,
        )
    except PrinterConnectionError as exc:
        partial = exc.partial_status
        return ProbeAttempt(
            attempt=0,
            success=False,
            connected=bool(partial and partial.connected),
            subscribed=bool(partial and partial.subscribed),
            message_received=False,
            elapsed_seconds=round(time.monotonic() - started, 3),
            error=f"{exc.code}: {exc}",
            status_summary=None,
        )
    return ProbeAttempt(
        attempt=0,
        success=True,
        connected=status.connected,
        subscribed=status.subscribed,
        message_received=status.printer_state_observed,
        elapsed_seconds=status.elapsed_seconds,
        error=None,
        status_summary=status.status_summary,
    )


def run_readonly_probe(
    project_root: Path,
    request_id: str,
    identity: DeviceIdentity,
    access_code: str,
    *,
    attempts: int = 1,
    timeout_seconds: float = 15.0,
    confirmation_reader: Callable[[str], str] = input,
) -> dict[str, Any]:
    if not access_code:
        raise ReadonlyProbeError("Access Code cannot be empty.")
    if attempts < 1 or attempts > 10:
        raise ReadonlyProbeError("Attempts must be between 1 and 10.")

    project_root = project_root.resolve()
    m3_acceptance = validate_m3_acceptance(project_root, request_id)
    identity = DeviceIdentity(
        ip_address=validate_private_ipv4(identity.ip_address),
        serial_number=validate_serial(identity.serial_number),
        firmware_version=(identity.firmware_version or "").strip() or None,
    )

    task_dir = project_root / "outputs" / "m4" / request_id
    task_dir.mkdir(parents=True, exist_ok=True)
    pin_path = task_dir / "m4_printer_tls_pin.json"
    report_path = task_dir / "m4_gate1_readonly_probe.json"

    fingerprint = get_tls_certificate_fingerprint(identity.ip_address)
    pin = ensure_tls_pin(
        pin_path,
        identity,
        fingerprint,
        confirmation_reader=confirmation_reader,
    )

    results: list[ProbeAttempt] = []
    for index in range(1, attempts + 1):
        attempt = run_single_passive_attempt(
            identity,
            access_code,
            timeout_seconds=timeout_seconds,
        )
        results.append(
            ProbeAttempt(
                attempt=index,
                success=attempt.success,
                connected=attempt.connected,
                subscribed=attempt.subscribed,
                message_received=attempt.message_received,
                elapsed_seconds=attempt.elapsed_seconds,
                error=attempt.error,
                status_summary=attempt.status_summary,
            )
        )
        if index < attempts:
            time.sleep(1.0)

    success_count = sum(1 for item in results if item.success)
    all_passed = success_count == attempts
    status = "readonly_probe_passed" if all_passed else "readonly_probe_failed"
    next_phase = (
        "m4_gate2_ftps_upload_probe"
        if all_passed and attempts == 10
        else "m4_gate1_stability_test"
        if all_passed
        else None
    )

    payload = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 1,
        "stage": "developer_mode_passive_mqtt_probe",
        "request_id": request_id,
        "status": status,
        "created_at": utc_now(),
        "project_root": str(project_root),
        "source_m3_acceptance": str(m3_acceptance),
        "source_m3_acceptance_sha256": sha256_file(m3_acceptance),
        "printer": {
            "model_expected": "Bambu Lab X1 Carbon",
            "ip_address": identity.ip_address,
            "serial_number": identity.serial_number,
            "firmware_version_user_reported": identity.firmware_version,
            "mqtt_port": MQTT_PORT,
            "mqtt_username": MQTT_USERNAME,
            "report_topic": f"device/{identity.serial_number}/report",
            "certificate_sha256": pin["certificate_sha256"],
        },
        "probe_policy": {
            "developer_mode_required": True,
            "private_lan_required": True,
            "passive_subscription_only": True,
            "mqtt_publish_count": 0,
            "control_command_count": 0,
            "access_code_stored": False,
            "tls_certificate_pinned": True,
            "printer_connection_attempted": True,
            "artifact_uploaded": False,
            "print_started": False,
        },
        "attempt_count": attempts,
        "success_count": success_count,
        "all_attempts_passed": all_passed,
        "attempts": [item.__dict__ for item in results],
        "next_phase": next_phase,
    }
    _write_json_atomic(report_path, payload)
    payload["report_file"] = str(report_path)
    return payload
