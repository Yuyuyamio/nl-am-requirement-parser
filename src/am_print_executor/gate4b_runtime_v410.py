from __future__ import annotations

import importlib
import json
import ssl
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from .ftps_probe_v32 import load_gate1_identity
from .gate4_runtime_v404 import (
    REQUEST_ID,
    EXPECTED_DEVICE_ID,
    _runtime_status_v404,
    _gate3_lock,
)
from .gate4_runtime_v406 import _runtime_preflight_v406

GATE4A_REPORT = "m4_gate4a_runtime_preflight_v406.json"
GATE4B_REPORT = "m4_gate4b_first_print_start_v410.json"
AUTH_PHRASE = f"START_PRINT_{REQUEST_ID}_{EXPECTED_DEVICE_ID}"


class Gate4BV410Error(RuntimeError):
    pass


def _v40():
    return importlib.import_module("am_print_executor.gate4_runtime_v40")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise Gate4BV410Error(f"Expected JSON object: {path}")
    return obj


def _validate_gate4a_lock(project_root: Path) -> dict[str, Any]:
    path = project_root / "outputs" / "m4" / REQUEST_ID / GATE4A_REPORT
    if not path.is_file():
        raise Gate4BV410Error(f"Gate4A V4.0.6 report missing: {path}")
    report = _load_json(path)
    if report.get("version") != "4.0.6":
        raise Gate4BV410Error("Gate4A version lock is not 4.0.6.")
    if report.get("status") != "runtime_preflight_passed":
        raise Gate4BV410Error("Gate4A V4.0.6 did not pass.")
    if report.get("request_id") != REQUEST_ID:
        raise Gate4BV410Error("Gate4A request_id mismatch.")
    if report.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate4BV410Error("Gate4A device_id mismatch.")
    if report.get("policy", {}).get("print_started") is not False:
        raise Gate4BV410Error("Gate4A policy lock is invalid.")
    return report | {"report_path": str(path)}


def _project_file_payload(
    sequence_id: str,
    remote_path: str,
    gcode_entry: str,
) -> dict[str, Any]:
    if not remote_path.startswith("/cache/"):
        raise Gate4BV410Error("Gate3 remote_path is not under /cache.")
    if not gcode_entry.startswith("Metadata/plate_") or not gcode_entry.endswith(".gcode"):
        raise Gate4BV410Error("Gate3 gcode entry is not a plate gcode path.")

    return {
        "print": {
            "sequence_id": sequence_id,
            "command": "project_file",
            "param": gcode_entry,
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "subtask_id": "0",
            "subtask_name": REQUEST_ID,
            "file": "",
            "url": f"ftp://{remote_path}",
            "md5": "",
            "timelapse": False,
            "bed_type": "auto",
            "bed_levelling": True,
            "flow_cali": True,
            "vibration_cali": True,
            "layer_inspect": True,
            "ams_mapping": "",
            "use_ams": False,
        }
    }


def _reason_failed(reason_code: Any) -> bool:
    if hasattr(reason_code, "is_failure"):
        return bool(reason_code.is_failure)
    try:
        return int(reason_code) != 0
    except Exception:
        return str(reason_code).lower() not in {"0", "success"}


def _classify_outcome(
    *,
    published: bool,
    ack: dict[str, Any] | None,
    observed_states: list[str],
) -> str:
    active = {"PREPARE", "RUNNING", "SLICING", "PAUSE", "PAUSED"}
    transitioned = any(str(s).upper() in active for s in observed_states)

    if not published:
        return "first_print_not_sent"

    if ack is not None:
        result = str(ack.get("result", "")).lower()
        if result and result != "success":
            return "first_print_start_rejected"

    if transitioned:
        return "first_print_started"

    return "first_print_start_outcome_unknown"


def _publish_once_and_observe(
    *,
    ip_address: str,
    access_code: str,
    payload: dict[str, Any],
    sequence_id: str,
    observe_seconds: float = 35.0,
) -> dict[str, Any]:
    request_topic = f"device/{EXPECTED_DEVICE_ID}/request"
    report_topic = f"device/{EXPECTED_DEVICE_ID}/report"

    connected = threading.Event()
    subscribed = threading.Event()
    ack_event = threading.Event()
    state_event = threading.Event()
    ack: dict[str, Any] | None = None
    observed_states: list[str] = []
    raw_events: list[dict[str, Any]] = []
    callback_errors: list[str] = []

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if _reason_failed(reason_code):
            callback_errors.append(f"MQTT connect rejected: {reason_code}")
            connected.set()
            return
        connected.set()
        client.subscribe(report_topic, qos=0)

    def on_subscribe(client, userdata, mid, reason_codes, properties=None):
        try:
            refused = any(getattr(rc, "is_failure", False) for rc in (reason_codes or []))
        except Exception:
            refused = False
        if refused:
            callback_errors.append("MQTT report subscription refused.")
        subscribed.set()

    def on_message(client, userdata, msg):
        nonlocal ack
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="strict"))
        except Exception as exc:
            callback_errors.append(f"Invalid MQTT JSON: {exc}")
            return

        if not isinstance(data, dict):
            return

        pobj = data.get("print")
        if not isinstance(pobj, dict):
            return

        state = pobj.get("gcode_state")
        if state is not None:
            s = str(state).upper()
            if not observed_states or observed_states[-1] != s:
                observed_states.append(s)
            if s in {"PREPARE", "RUNNING", "SLICING", "PAUSE", "PAUSED"}:
                state_event.set()

        if (
            str(pobj.get("command", "")) == "project_file"
            and str(pobj.get("sequence_id", "")) == sequence_id
        ):
            ack = dict(pobj)
            ack_event.set()

        raw_events.append({
            "command": pobj.get("command"),
            "sequence_id": pobj.get("sequence_id"),
            "result": pobj.get("result"),
            "reason": pobj.get("reason"),
            "gcode_state": pobj.get("gcode_state"),
            "mc_percent": pobj.get("mc_percent"),
            "mc_remaining_time": pobj.get("mc_remaining_time"),
            "print_error": pobj.get("print_error"),
        })

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"nl-am-gate4b-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    published = False
    publish_rc = None

    try:
        client.connect(ip_address, 8883, keepalive=60)
        client.loop_start()

        if not connected.wait(12):
            raise Gate4BV410Error("MQTT connect timeout before print publish.")
        if callback_errors:
            raise Gate4BV410Error(callback_errors[-1])
        if not subscribed.wait(8):
            raise Gate4BV410Error("MQTT subscribe timeout before print publish.")
        if callback_errors:
            raise Gate4BV410Error(callback_errors[-1])

        info = client.publish(
            request_topic,
            payload=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            qos=0,
            retain=False,
        )
        publish_rc = int(info.rc)
        if publish_rc != mqtt.MQTT_ERR_SUCCESS:
            raise Gate4BV410Error(f"MQTT publish rejected locally rc={publish_rc}")

        info.wait_for_publish(timeout=8)
        if not info.is_published():
            raise Gate4BV410Error("MQTT publish did not complete locally.")
        published = True

        deadline = time.monotonic() + observe_seconds
        while time.monotonic() < deadline:
            if state_event.is_set():
                time.sleep(2.0)
                break
            time.sleep(0.25)

    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

    outcome = _classify_outcome(
        published=published,
        ack=ack,
        observed_states=observed_states,
    )

    return {
        "published": published,
        "publish_rc": publish_rc,
        "mqtt_publish_count": 1 if published else 0,
        "automatic_retry_count": 0,
        "ack": ack,
        "ack_received": ack is not None,
        "observed_states": observed_states,
        "events": raw_events[-20:],
        "callback_errors": callback_errors,
        "outcome": outcome,
    }


def run_gate4b_v410(
    project_root: Path,
    access_code: str,
    authorization_phrase: str,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    v40 = _v40()

    gate4a = _validate_gate4a_lock(project_root)

    profile = load_gate1_identity(project_root, REQUEST_ID)
    if profile.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate4BV410Error("Gate1 V3.1 locked DEVICE_ID mismatch.")
    ip_address = profile.get("ip_address")
    if not isinstance(ip_address, str) or not ip_address:
        raise Gate4BV410Error("Gate1 printer IP missing.")

    gate3 = _gate3_lock(project_root)
    if gate3.get("gate3_version_lock") != "3.3.1":
        raise Gate4BV410Error("Gate3 version lock is not 3.3.1.")
    if gate3.get("remote_sha256_verified") is not True:
        raise Gate4BV410Error("Gate3 remote SHA-256 lock is not verified.")
    if gate3.get("remote_retained") is not True:
        raise Gate4BV410Error("Gate3 artifact is not retained on printer.")

    gcode_entries = gate3.get("gcode_entries")
    if not isinstance(gcode_entries, list) or len(gcode_entries) != 1:
        raise Gate4BV410Error("Gate3 must expose exactly one plate gcode entry.")
    gcode_entry = str(gcode_entries[0])
    remote_path = str(gate3.get("remote_path", ""))

    # Fresh pre-publish safety read. This is not a rerun of Gate4A;
    # it prevents a stale PASS report from authorizing a now-busy printer.
    print_obj, status_telemetry = _runtime_status_v404(
        ip_address=ip_address,
        access_code=access_code,
    )
    prepublish = _runtime_preflight_v406(print_obj)
    if not prepublish.get("passed"):
        raise Gate4BV410Error(
            f"Fresh pre-publish safety state is not safe: {prepublish}"
        )

    if authorization_phrase != AUTH_PHRASE:
        raise Gate4BV410Error("Manual START_PRINT authorization phrase mismatch; nothing published.")

    sequence_id = str(int(time.time() * 1000))
    payload = _project_file_payload(
        sequence_id=sequence_id,
        remote_path=remote_path,
        gcode_entry=gcode_entry,
    )

    dispatch = _publish_once_and_observe(
        ip_address=ip_address,
        access_code=access_code,
        payload=payload,
        sequence_id=sequence_id,
    )

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "4B",
        "version": "4.1.0",
        "stage": "manual_first_real_print_start",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "printer_ip": ip_address,
        "created_at": v40._utc_now(),
        "created_unix": time.time(),
        "gate4a_lock": {
            "version": gate4a.get("version"),
            "status": gate4a.get("status"),
            "report": gate4a.get("report_path"),
        },
        "artifact_integrity_lock": gate3,
        "fresh_prepublish_runtime": prepublish,
        "fresh_status_telemetry": status_telemetry,
        "manual_authorization": {
            "required_phrase": AUTH_PHRASE,
            "matched": True,
            "means": [
                "human is physically present",
                "build plate is cleared and ready",
                "external-spool single-material PLA setup is ready",
                "user explicitly authorizes one real print-start command",
            ],
        },
        "print_command": {
            "sequence_id": sequence_id,
            "topic": f"device/{EXPECTED_DEVICE_ID}/request",
            "command": "project_file",
            "param": gcode_entry,
            "url": payload["print"]["url"],
            "use_ams": False,
            "timelapse": False,
            "bed_levelling": True,
            "flow_cali": True,
            "vibration_cali": True,
            "layer_inspect": True,
        },
        "dispatch": dispatch,
        "status": dispatch["outcome"],
        "policy": {
            "print_start_command_count": dispatch["mqtt_publish_count"],
            "automatic_retry_count": 0,
            "retry_after_unknown_outcome_allowed": False,
            "access_code_stored": False,
            "artifact_reuploaded": False,
        },
        "next_action": (
            "Proceed to Gate5 monitoring."
            if dispatch["outcome"] == "first_print_started"
            else "Do not rerun Gate4B. Physically inspect printer and review report."
            if dispatch["outcome"] == "first_print_start_outcome_unknown"
            else "Review rejection or pre-send failure before any further print-start attempt."
        ),
    }

    report_path = project_root / "outputs" / "m4" / REQUEST_ID / GATE4B_REPORT
    v40._write_json_atomic(report_path, report)
    return report | {"report_file": str(report_path)}
