from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import ssl
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError as exc:
    mqtt = None
    _PAHO_ERROR = exc
else:
    _PAHO_ERROR = None

REQUEST_ID_DEFAULT = "M2-1E4B2301FADD"
MQTT_PORT = 8883
MQTT_USERNAME = "bblp"
REPORT_WILDCARD = "device/+/report"


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
    parts = topic.split("/")
    if len(parts) == 3 and parts[0] == "device" and parts[2] == "report" and parts[1]:
        return parts[1]
    return None


def _extract_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    print_obj = payload.get("print")
    if not isinstance(print_obj, dict):
        return None
    wanted = (
        "gcode_state", "mc_percent", "mc_remaining_time", "nozzle_temper",
        "bed_temper", "chamber_temper", "wifi_signal", "print_error",
        "ams_status", "stg_cur"
    )
    summary = {key: print_obj.get(key) for key in wanted if key in print_obj}
    summary["report_has_print_object"] = True
    return summary


def passive_discover(ip_address: str, access_code: str, timeout: float = 20.0) -> ProbeResult:
    if mqtt is None:
        raise ProbeError("paho-mqtt is missing. Install paho-mqtt==2.1.0") from _PAHO_ERROR
    if not access_code:
        raise ProbeError("Access Code cannot be empty.")

    started = time.monotonic()
    done = threading.Event()
    state: dict[str, Any] = {
        "connected": False,
        "subscribed": False,
        "device_id": None,
        "summary": None,
        "error": None,
    }

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code.is_failure:
            state["error"] = f"MQTT connection/authentication failed: {reason_code}"
            done.set()
            return
        state["connected"] = True
        result, _mid = client.subscribe(REPORT_WILDCARD, qos=0)
        if result != mqtt.MQTT_ERR_SUCCESS:
            state["error"] = f"Subscribe failed: {result}"
            done.set()

    def on_subscribe(client: Any, userdata: Any, mid: Any, reason_codes: Any, properties: Any) -> None:
        failures = [str(code) for code in reason_codes if getattr(code, "is_failure", False)]
        if failures:
            state["error"] = f"MQTT subscription refused: {failures}"
            done.set()
            return
        state["subscribed"] = True

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        device_id = _extract_device_id(message.topic)
        if not device_id:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        summary = _extract_summary(payload)
        if summary is None:
            return
        state["device_id"] = device_id
        state["summary"] = summary
        done.set()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"m4-auto-{uuid.uuid4().hex[:12]}",
        protocol=mqtt.MQTTv311,
        reconnect_on_failure=False,
    )
    client.username_pw_set(MQTT_USERNAME, access_code)
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls_context.check_hostname = False
    tls_context.verify_mode = ssl.CERT_NONE
    client.tls_set_context(tls_context)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    try:
        client.connect(ip_address, MQTT_PORT, keepalive=30)
        client.loop_start()
        done.wait(timeout)
    except Exception as exc:
        state["error"] = f"MQTT probe exception: {type(exc).__name__}: {exc}"
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass

    if state["error"] is None and state["summary"] is None:
        if not state["connected"]:
            state["error"] = "Timed out before MQTT connection completed."
        elif not state["subscribed"]:
            state["error"] = "Connected but subscription did not complete."
        else:
            state["error"] = "Connected and subscribed, but no device/+/report status arrived."

    return ProbeResult(
        connected=bool(state["connected"]),
        subscribed=bool(state["subscribed"]),
        message_received=state["summary"] is not None,
        device_id=state["device_id"],
        summary=state["summary"],
        error=state["error"],
        elapsed_seconds=round(time.monotonic() - started, 3),
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
