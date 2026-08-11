from __future__ import annotations

import importlib
import json
import ssl
import threading
import time
import uuid
from pathlib import Path
from typing import Any

REPORT_A = "m4_gate4a_runtime_preflight_v402.json"
REPORT_B = "m4_gate4b_first_print_start_v402.json"


def _load_v40():
    return importlib.import_module("am_print_executor.gate4_runtime_v40")


def _load_v401():
    return importlib.import_module("am_print_executor.gate4_runtime_v401")


def _mqtt_module():
    return importlib.import_module("paho.mqtt.client")


def _reason_code_failed(reason_code: Any) -> bool:
    """Paho callback API v2 compatibility.

    Paho 2.x passes ReasonCode objects. Do not coerce them with int(); use the
    documented is_failure property when available, and only fall back to
    equality comparison for older/plain return-code objects.
    """
    marker = getattr(reason_code, "is_failure", None)
    if marker is not None:
        return bool(marker)
    try:
        return bool(reason_code != 0)
    except Exception:
        return True


def _safe_passive_status_once_v402(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    v40 = _load_v40()
    mqtt = _mqtt_module()
    if not access_code:
        raise v40.Gate4V40Error("Access Code cannot be empty.")

    event = threading.Event()
    state: dict[str, Any] = {"error": None, "print": None}
    topic = f"device/{device_id}/report"

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        try:
            if _reason_code_failed(reason_code):
                state["error"] = f"MQTT connect failed: {reason_code}"
                event.set()
                return
            result, _mid = client.subscribe(topic, qos=0)
            if result != mqtt.MQTT_ERR_SUCCESS:
                state["error"] = f"MQTT subscribe failed: {result}"
                event.set()
        except Exception as exc:
            state["error"] = f"MQTT on_connect callback failed: {type(exc).__name__}: {exc}"
            event.set()

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        print_obj = payload.get("print")
        if isinstance(print_obj, dict):
            state["print"] = print_obj
            event.set()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"m4-g4a-v402-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
        reconnect_on_failure=False,
    )
    client.username_pw_set(v40.MQTT_USERNAME, access_code)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    client.tls_set_context(context)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(ip_address, v40.MQTT_PORT, keepalive=30)
        client.loop_start()
        event.wait(timeout)
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass

    if state["error"]:
        raise v40.Gate4V40Error(str(state["error"]))
    if not isinstance(state["print"], dict):
        raise v40.Gate4V40Error(
            "Gate4A MQTT timeout on port 8883: no X1C print-status object arrived."
        )
    return state["print"]


def _safe_publish_first_print_once_v402(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
    payload: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    v40 = _load_v40()
    mqtt = _mqtt_module()

    event = threading.Event()
    ack_event = threading.Event()
    started_event = threading.Event()
    state: dict[str, Any] = {
        "connected": False,
        "published": False,
        "ack": None,
        "latest_state": None,
        "error": None,
    }
    request_topic = f"device/{device_id}/request"
    report_topic = f"device/{device_id}/report"
    seq = str(payload["print"]["sequence_id"])

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        try:
            if _reason_code_failed(reason_code):
                state["error"] = f"MQTT connect failed: {reason_code}"
                event.set()
                return
            state["connected"] = True
            result, _mid = client.subscribe(report_topic, qos=0)
            if result != mqtt.MQTT_ERR_SUCCESS:
                state["error"] = f"MQTT subscribe failed: {result}"
                event.set()
        except Exception as exc:
            state["error"] = f"MQTT on_connect callback failed: {type(exc).__name__}: {exc}"
            event.set()

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        try:
            body = json.loads(message.payload.decode("utf-8"))
        except Exception:
            return
        if not isinstance(body, dict):
            return
        print_obj = body.get("print")
        if not isinstance(print_obj, dict):
            return
        if str(print_obj.get("command", "")) == "project_file" and str(print_obj.get("sequence_id", "")) == seq:
            state["ack"] = print_obj
            ack_event.set()
        gcode_state = str(print_obj.get("gcode_state", "")).upper()
        if gcode_state:
            state["latest_state"] = gcode_state
        if gcode_state and gcode_state != "IDLE":
            started_event.set()
        if ack_event.is_set() and started_event.is_set():
            event.set()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"m4-g4b-v402-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
        reconnect_on_failure=False,
    )
    client.username_pw_set(v40.MQTT_USERNAME, access_code)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    client.tls_set_context(context)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(ip_address, v40.MQTT_PORT, keepalive=30)
        client.loop_start()
        deadline = time.monotonic() + timeout
        while not state["connected"] and time.monotonic() < deadline and not state["error"]:
            time.sleep(0.05)
        if state["error"]:
            raise v40.Gate4V40Error(str(state["error"]))
        if not state["connected"]:
            raise v40.Gate4V40Error("MQTT connection timed out before print start; nothing was published.")

        info = client.publish(
            request_topic,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise v40.Gate4V40Error(f"MQTT publish failed before acceptance: rc={info.rc}")
        state["published"] = True
        event.wait(max(0.0, deadline - time.monotonic()))
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass

    ack = state["ack"]
    ack_result = str(ack.get("result", "")).lower() if isinstance(ack, dict) else None
    return {
        "mqtt_connected": bool(state["connected"]),
        "mqtt_publish_count": 1 if state["published"] else 0,
        "project_file_publish_attempted": bool(state["published"]),
        "command_ack_received": isinstance(ack, dict),
        "command_ack_result": ack_result,
        "command_ack": ack,
        "non_idle_state_observed": started_event.is_set(),
        "latest_gcode_state": state["latest_state"],
        "outcome": (
            "print_start_confirmed"
            if ack_result == "success" and started_event.is_set()
            else "command_rejected"
            if isinstance(ack, dict) and ack_result not in {"success", ""}
            else "start_outcome_unknown"
            if state["published"]
            else "not_published"
        ),
        "automatic_retry_allowed": False,
    }


def _patch_writer(v40: Any):
    original = v40._write_json_atomic

    def patched(path: Path, payload: dict[str, Any]) -> None:
        data = dict(payload)
        if path.name == REPORT_A:
            data["stage"] = "runtime_preflight_v402"
            data["network_fix_version"] = "4.0.2"
            data["paho_callback_fix"] = "ReasonCode.is_failure"
            data["next_phase"] = "m4_gate4b_manual_first_print_start_v402"
        elif path.name == REPORT_B:
            data["stage"] = "manual_first_print_start_v402"
            data["network_fix_version"] = "4.0.2"
            data["paho_callback_fix"] = "ReasonCode.is_failure"
        original(path, data)

    return patched


def _reload_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    data["report_file"] = str(path)
    return data


def run_gate4a_v402(project_root: Path, access_code: str) -> dict[str, Any]:
    v40 = _load_v40()
    v401 = _load_v401()
    original_verify = v40.verify_remote_artifact_v32
    original_status = v40._passive_status_once
    original_writer = v40._write_json_atomic
    old_report_a = v40.GATE4A_REPORT
    try:
        v40.GATE4A_REPORT = REPORT_A
        v40.verify_remote_artifact_v32 = lambda **kwargs: v401._fast_remote_check(v40, **kwargs)
        v40._passive_status_once = _safe_passive_status_once_v402
        v40._write_json_atomic = _patch_writer(v40)
        v40.run_gate4a(project_root, access_code)
        report_path = project_root.resolve() / "outputs" / "m4" / v40.REQUEST_ID / REPORT_A
        return _reload_report(report_path)
    finally:
        v40.verify_remote_artifact_v32 = original_verify
        v40._passive_status_once = original_status
        v40._write_json_atomic = original_writer
        v40.GATE4A_REPORT = old_report_a


def run_gate4b_v402(project_root: Path, access_code: str, authorization_phrase: str) -> dict[str, Any]:
    v40 = _load_v40()
    original_publish = v40.publish_first_print_once
    original_writer = v40._write_json_atomic
    old_report_a = v40.GATE4A_REPORT
    old_report_b = v40.GATE4B_REPORT
    try:
        v40.GATE4A_REPORT = REPORT_A
        v40.GATE4B_REPORT = REPORT_B
        v40.publish_first_print_once = _safe_publish_first_print_once_v402
        v40._write_json_atomic = _patch_writer(v40)
        v40.run_gate4b(project_root, access_code, authorization_phrase)
        report_path = project_root.resolve() / "outputs" / "m4" / v40.REQUEST_ID / REPORT_B
        return _reload_report(report_path)
    finally:
        v40.publish_first_print_once = original_publish
        v40._write_json_atomic = original_writer
        v40.GATE4A_REPORT = old_report_a
        v40.GATE4B_REPORT = old_report_b


def expected_start_phrase() -> str:
    return _load_v40().expected_start_phrase()
