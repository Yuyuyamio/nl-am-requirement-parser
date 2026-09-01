from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import ssl
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .x1c_connection import (
    MQTT_PORT,
    MQTT_USERNAME,
    REPORT_WILDCARD,
    PrinterConnectionError,
    connect_printer,
    extract_device_id,
    extract_status_summary,
)

REQUEST_ID_DEFAULT = "M2-1E4B2301FADD"
class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    connected: bool
    subscribed: bool
    message_received: bool
    device_id: str | None
    summary: dict[str, Any] | None
    error: str | None
    elapsed_seconds: float


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8-sig")
        if existing == text:
            return
        raise ProbeError(f"Refusing to overwrite a different file: {path}")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate_private_ipv4(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ProbeError(f"Invalid IPv4 address: {value}") from exc
    if address.version != 4 or not address.is_private:
        raise ProbeError("Printer address must be a private IPv4 address.")
    return str(address)


def get_tls_fingerprint(ip_address: str, timeout: float = 6.0) -> str:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip_address, MQTT_PORT), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=ip_address) as tls_socket:
                cert = tls_socket.getpeercert(binary_form=True)
    except OSError as exc:
        raise ProbeError(f"Cannot open TLS connection to {ip_address}:{MQTT_PORT}: {exc}") from exc
    if not cert:
        raise ProbeError("Printer did not provide a TLS certificate.")
    return hashlib.sha256(cert).hexdigest().upper()


def format_fingerprint(value: str) -> str:
    compact = value.replace(":", "").upper()
    return ":".join(compact[i:i+2] for i in range(0, len(compact), 2))


def _extract_device_id(topic: str) -> str | None:
    return extract_device_id(topic)


def _extract_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    return extract_status_summary(payload)


def passive_discover(ip_address: str, access_code: str, timeout: float = 20.0) -> ProbeResult:
    started = time.monotonic()
    try:
        status = connect_printer(
            ip=ip_address,
            access_code=access_code,
            device_id=None,
            timeout_seconds=timeout,
            attempts=1,
        )
    except PrinterConnectionError as exc:
        partial = exc.partial_status
        return ProbeResult(
            connected=bool(partial and partial.connected),
            subscribed=bool(partial and partial.subscribed),
            message_received=False,
            device_id=exc.device_id,
            summary=None,
            error=f"{exc.code}: {exc}",
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
    return ProbeResult(
        connected=status.connected,
        subscribed=status.subscribed,
        message_received=status.printer_state_observed,
        device_id=status.device_id,
        summary=status.status_summary,
        error=None,
        elapsed_seconds=status.elapsed_seconds,
    )


def run_probe(project_root: Path, request_id: str, ip_address: str, access_code: str, attempts: int = 1) -> dict[str, Any]:
    ip_address = validate_private_ipv4(ip_address)
    if attempts < 1 or attempts > 10:
        raise ProbeError("Attempts must be between 1 and 10.")

    task_dir = project_root.resolve() / "outputs" / "m4" / request_id
    task_dir.mkdir(parents=True, exist_ok=True)
    pin_path = task_dir / "m4_gate1_autodiscovered_device_v31.json"
    report_path = task_dir / "m4_gate1_autodiscovery_probe_v31.json"

    fingerprint = get_tls_fingerprint(ip_address)
    results: list[ProbeResult] = []
    discovered_ids: set[str] = set()

    for attempt in range(attempts):
        result = passive_discover(ip_address, access_code)
        results.append(result)
        if result.device_id:
            discovered_ids.add(result.device_id)
        if attempt + 1 < attempts:
            time.sleep(1.0)

    if len(discovered_ids) > 1:
        raise ProbeError(f"Multiple DEVICE_ID values were observed from one printer IP: {sorted(discovered_ids)}")

    success_count = sum(1 for item in results if item.connected and item.subscribed and item.message_received and item.device_id)
    all_passed = success_count == attempts
    device_id = next(iter(discovered_ids), None)

    if all_passed and device_id:
        pin = {
            "schema_version": "0.1.0",
            "module": "M4",
            "stage": "autodiscovered_device_identity",
            "request_id": request_id,
            "ip_address": ip_address,
            "device_id": device_id,
            "tls_certificate_sha256": fingerprint,
            "access_code_stored": False,
            "mqtt_publish_count": 0,
        }
        if pin_path.exists():
            existing = json.loads(pin_path.read_text(encoding="utf-8-sig"))
            if existing.get("ip_address") != ip_address or existing.get("device_id") != device_id or existing.get("tls_certificate_sha256") != fingerprint:
                raise ProbeError("Pinned IP / DEVICE_ID / TLS certificate changed. Stop and verify the physical printer.")
        else:
            _write_json_atomic(pin_path, pin)

    payload = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 1,
        "stage": "developer_mode_passive_autodiscovery",
        "request_id": request_id,
        "status": "readonly_probe_passed" if all_passed else "readonly_probe_failed",
        "printer_ip": ip_address,
        "device_id_autodiscovered": device_id,
        "tls_certificate_sha256": fingerprint,
        "attempt_count": attempts,
        "success_count": success_count,
        "all_attempts_passed": all_passed,
        "policy": {
            "subscription_topic": REPORT_WILDCARD,
            "mqtt_publish_count": 0,
            "control_command_count": 0,
            "access_code_stored": False,
            "artifact_uploaded": False,
            "print_started": False,
        },
        "attempts": [item.__dict__ for item in results],
        "next_phase": "m4_gate2_ftps_upload_probe" if all_passed and attempts == 10 else "m4_gate1_stability_test" if all_passed else None,
    }
    _write_json_atomic(report_path, payload)
    payload["report_file"] = str(report_path)
    return payload
