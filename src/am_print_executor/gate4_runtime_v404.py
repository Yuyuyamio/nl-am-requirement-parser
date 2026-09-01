from __future__ import annotations

import importlib
import json
import ssl
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError as exc:
    mqtt = None
    _PAHO_ERROR = exc
else:
    _PAHO_ERROR = None

from .ftps_probe_v32 import load_gate1_identity

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
MQTT_PORT = 8883
MQTT_USERNAME = "bblp"
REPORT_WILDCARD = "device/+/report"
REPORT_NAME = "m4_gate4a_runtime_preflight_v404.json"


class Gate4V404Error(RuntimeError):
    pass


def _v40():
    return importlib.import_module("am_print_executor.gate4_runtime_v40")


def _reason_failed(reason_code: Any) -> bool:
    marker = getattr(reason_code, "is_failure", None)
    if marker is not None:
        return bool(marker)
    return bool(reason_code != 0)


def _device_id_from_topic(topic: str) -> str | None:
    parts = topic.split("/")
    if len(parts) == 3 and parts[0] == "device" and parts[2] == "report":
        return parts[1] or None
    return None


def _runtime_status_v404(
    *,
    ip_address: str,
    access_code: str,
    passive_seconds: float = 10.0,
    solicited_seconds: float = 30.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if mqtt is None:
        raise Gate4V404Error("paho-mqtt is missing. Install paho-mqtt==2.1.0") from _PAHO_ERROR
    if not access_code:
        raise Gate4V404Error("Access Code cannot be empty.")

    connected = threading.Event()
    subscribed = threading.Event()
    status_arrived = threading.Event()
    state: dict[str, Any] = {
        "error": None,
        "print": None,
        "topic": None,
        "publish_count": 0,
        "source": None,
    }

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        try:
            if _reason_failed(reason_code):
                state["error"] = f"MQTT connect/authentication failed: {reason_code}"
                connected.set()
                return
            connected.set()
            result, _mid = client.subscribe(REPORT_WILDCARD, qos=0)
            if result != mqtt.MQTT_ERR_SUCCESS:
                state["error"] = f"MQTT subscribe failed: {result}"
        except Exception as exc:
            state["error"] = f"MQTT on_connect callback failed: {type(exc).__name__}: {exc}"
            connected.set()

    def on_subscribe(client: Any, userdata: Any, mid: Any, reason_codes: Any, properties: Any) -> None:
        try:
            failures = [str(code) for code in reason_codes if getattr(code, "is_failure", False)]
            if failures:
                state["error"] = f"MQTT subscription refused: {failures}"
            else:
                subscribed.set()
        except Exception as exc:
            state["error"] = f"MQTT on_subscribe callback failed: {type(exc).__name__}: {exc}"

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        did = _device_id_from_topic(str(message.topic))
        if did != EXPECTED_DEVICE_ID:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        print_obj = payload.get("print")
        if isinstance(print_obj, dict):
            state["print"] = print_obj
            state["topic"] = str(message.topic)
            if state["source"] is None:
                state["source"] = "unsolicited_report"
            status_arrived.set()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"m4-g4a-v404-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
        reconnect_on_failure=False,
    )
    client.username_pw_set(MQTT_USERNAME, access_code)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    client.tls_set_context(context)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    try:
        client.connect(ip_address, MQTT_PORT, keepalive=30)
        client.loop_start()
        if not connected.wait(10.0):
            raise Gate4V404Error("MQTT port 8883 connection timed out before CONNACK.")
        if state["error"]:
            raise Gate4V404Error(str(state["error"]))
        if not subscribed.wait(10.0):
            if state["error"]:
                raise Gate4V404Error(str(state["error"]))
            raise Gate4V404Error("MQTT connected but device/+/report subscription did not complete.")

        if status_arrived.wait(passive_seconds):
            state["source"] = "unsolicited_report"
        else:
            sequence_id = str(int(time.time() * 1000))
            payload = {
                "pushing": {
                    "sequence_id": sequence_id,
                    "command": "pushall",
                    "version": 1,
                    "push_target": 1,
                }
            }
            request_topic = f"device/{EXPECTED_DEVICE_ID}/request"
            info = client.publish(
                request_topic,
                json.dumps(payload, separators=(",", ":")),
                qos=1,
                retain=False,
            )
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise Gate4V404Error(f"One-shot pushall status request publish failed: rc={info.rc}")
            state["publish_count"] = 1
            state["source"] = "one_shot_pushall"
            if not status_arrived.wait(solicited_seconds):
                raise Gate4V404Error(
                    "MQTT connected and subscribed, and one non-actuating pushall status request was sent, "
                    "but no print-status object arrived from the locked X1C."
                )
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass

    print_obj = state["print"]
    if not isinstance(print_obj, dict):
        raise Gate4V404Error("No valid X1C print-status object was captured.")
    telemetry = {
        "subscription": REPORT_WILDCARD,
        "matched_topic": state["topic"],
        "status_source": state["source"],
        "mqtt_publish_count": state["publish_count"],
        "status_request_publish_count": state["publish_count"],
        "control_command_count": 0,
        "print_start_command_count": 0,
        "heating_command_count": 0,
        "motion_command_count": 0,
        "automatic_retry_count": 0,
    }
    return print_obj, telemetry


def _gate3_lock(project_root: Path) -> dict[str, Any]:
    v40 = _v40()
    task_dir = project_root / "outputs" / "m4" / REQUEST_ID
    v40._validate_gate2_v32(task_dir)
    gate3_path, gate3 = v40._find_gate3_v331(task_dir)
    audit_path, _audit, artifact_path, artifact_sha, gcode_entries = v40._validate_fixed_m3_audit(project_root)
    remote_name, remote_path = v40._gate3_remote_info(gate3, artifact_sha)
    return {
        "gate3_report": str(gate3_path),
        "gate3_version_lock": "3.3.1",
        "remote_sha256_verified": True,
        "remote_retained": True,
        "remote_name": remote_name,
        "remote_path": remote_path,
        "artifact_sha256": artifact_sha,
        "artifact_size_bytes": artifact_path.stat().st_size,
        "fixed_m3_audit": str(audit_path),
        "gcode_entries": gcode_entries,
        "ftps_connection_attempted": False,
    }


def run_gate4a_v404(project_root: Path, access_code: str) -> dict[str, Any]:
    project_root = project_root.resolve()
    v40 = _v40()
    profile = load_gate1_identity(project_root, REQUEST_ID)
    if profile.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate4V404Error("Gate1 V3.1 locked DEVICE_ID mismatch.")
    ip_address = profile.get("ip_address")
    if not isinstance(ip_address, str) or not ip_address:
        raise Gate4V404Error("Gate1 V3.1 locked printer IP is missing.")

    lock = _gate3_lock(project_root)
    print_obj, telemetry = _runtime_status_v404(
        ip_address=ip_address,
        access_code=access_code,
    )
    runtime = v40._preflight_checks(print_obj)
    if not runtime["passed"]:
        raise Gate4V404Error(f"Runtime safety checks failed: {runtime['checks']}")

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 4,
        "version": "4.0.4",
        "stage": "runtime_preflight_v404",
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
        "next_phase": "m4_gate4b_manual_first_print_start_after_v404_review",
    }
    report_path = project_root / "outputs" / "m4" / REQUEST_ID / REPORT_NAME
    v40._write_json_atomic(report_path, report)
    return report | {"report_file": str(report_path)}
