from __future__ import annotations

import getpass
import hashlib
import json
import os
import ssl
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:
    mqtt = None  # type: ignore[assignment]

from .ftps_probe_v32 import (
    FTPS_PORT,
    FTPS_USERNAME,
    SessionReuseImplicitFTP_TLS,
    load_gate1_identity,
)

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
MQTT_PORT = 8883
MQTT_USERNAME = "bblp"
GATE4A_REPORT = "m4_gate4a_runtime_preflight_v40.json"
GATE4B_REPORT = "m4_gate4b_first_print_start_v40.json"
START_PREFIX = "START_PRINT_"
PREFLIGHT_MAX_AGE_SECONDS = 300


class Gate4V40Error(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate4V40Error(f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate4V40Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Gate4V40Error(f"JSON root must be an object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8-sig")
        if existing == text:
            return
        raise Gate4V40Error(f"Refusing to overwrite a different Gate 4 report: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _task_dir(project_root: Path) -> Path:
    return project_root / "outputs" / "m4" / REQUEST_ID


def _fixed_audit_path(project_root: Path) -> Path:
    return project_root / "outputs" / "m3" / REQUEST_ID / "m3_gcode_audit.json"


def _validate_gate2_v32(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "m4_gate2_ftps_probe_v32.json"
    report = _load_json(path)
    if report.get("request_id") != REQUEST_ID:
        raise Gate4V40Error("Gate 2 v3.2 request_id mismatch.")
    if report.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate4V40Error("Gate 2 v3.2 DEVICE_ID mismatch.")
    if report.get("status") != "ftps_probe_passed":
        raise Gate4V40Error("Gate 2 v3.2 has not passed.")
    transport = report.get("transport_fix")
    if not isinstance(transport, dict) or transport.get("tls_session_reuse_on_data_channel") is not True:
        raise Gate4V40Error("Gate 2 v3.2 TLS session-reuse lock is missing.")
    return report


def _is_v331(report: dict[str, Any], path: Path) -> bool:
    stage = str(report.get("stage", "")).lower()
    version = str(report.get("version", report.get("gate_version", ""))).lower()
    name = path.name.lower()
    return (
        "v331" in stage
        or "3.3.1" in stage
        or version == "3.3.1"
        or "v331" in name
        or "v3_3_1" in name
    )


def _find_gate3_v331(task_dir: Path) -> tuple[Path, dict[str, Any]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(task_dir.glob("m4_gate3*.json")):
        try:
            report = _load_json(path)
        except Gate4V40Error:
            continue
        if report.get("module") != "M4" or report.get("phase") != 3:
            continue
        if report.get("request_id") != REQUEST_ID or report.get("device_id") != EXPECTED_DEVICE_ID:
            continue
        if report.get("status") != "audited_artifact_uploaded":
            continue
        if not _is_v331(report, path):
            continue
        artifact = report.get("artifact")
        if not isinstance(artifact, dict):
            continue
        if artifact.get("remote_sha256_verified") is not True:
            continue
        if artifact.get("remote_retained") is not True:
            continue
        matches.append((path, report))
    if len(matches) != 1:
        raise Gate4V40Error(
            f"Expected exactly one eligible Gate 3 v3.3.1 report; found {len(matches)}. "
            "Old Gate 3 versions are not accepted."
        )
    return matches[0]


def _validate_fixed_m3_audit(project_root: Path) -> tuple[Path, dict[str, Any], Path, str, list[str]]:
    path = _fixed_audit_path(project_root)
    audit = _load_json(path)
    if audit.get("request_id") != REQUEST_ID:
        raise Gate4V40Error("Fixed Phase 5 audit request_id mismatch.")
    if audit.get("module") != "M3" or audit.get("phase") != 5:
        raise Gate4V40Error("Fixed m3_gcode_audit.json is not the Phase 5 audit.")
    if audit.get("status") != "audit_passed" or audit.get("hard_constraints_passed") is not True:
        raise Gate4V40Error("Fixed Phase 5 audit has not passed.")
    if int(audit.get("hard_failure_count", -1)) != 0:
        raise Gate4V40Error("Fixed Phase 5 audit records hard failures.")
    artifact = audit.get("artifact")
    if not isinstance(artifact, dict):
        raise Gate4V40Error("Phase 5 audit artifact record is missing.")
    raw_path = artifact.get("path")
    expected_sha = artifact.get("sha256")
    entries = artifact.get("gcode_entries")
    if not isinstance(raw_path, str) or not raw_path:
        raise Gate4V40Error("Phase 5 audit artifact path is missing.")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise Gate4V40Error("Phase 5 audit artifact SHA-256 is invalid.")
    if not isinstance(entries, list) or not entries or not all(isinstance(x, str) and x for x in entries):
        raise Gate4V40Error("Phase 5 audit G-code entries are missing.")
    artifact_path = Path(raw_path)
    if not artifact_path.is_absolute():
        artifact_path = (path.parent / artifact_path).resolve()
    else:
        artifact_path = artifact_path.resolve()
    if not artifact_path.is_file():
        raise Gate4V40Error(f"Audited artifact does not exist: {artifact_path}")
    actual_sha = _sha256_file(artifact_path)
    if actual_sha != expected_sha.lower():
        raise Gate4V40Error("Audited artifact SHA-256 changed after Phase 5.")
    return path, audit, artifact_path, actual_sha, entries


def _gate3_remote_info(gate3: dict[str, Any], artifact_sha: str) -> tuple[str, str]:
    artifact = gate3.get("artifact")
    assert isinstance(artifact, dict)
    if artifact.get("local_sha256") != artifact_sha:
        raise Gate4V40Error("Gate 3 v3.3.1 local SHA-256 does not match fixed Phase 5 audit.")
    remote_name = artifact.get("remote_name")
    remote_path = artifact.get("remote_path")
    if not isinstance(remote_name, str) or not remote_name.endswith(".gcode.3mf"):
        raise Gate4V40Error("Gate 3 v3.3.1 remote artifact name is invalid.")
    if not isinstance(remote_path, str) or not remote_path.endswith(remote_name):
        raise Gate4V40Error("Gate 3 v3.3.1 remote artifact path is invalid.")
    if artifact.get("remote_sha256") != artifact_sha:
        raise Gate4V40Error("Gate 3 v3.3.1 remote SHA-256 does not match the audited artifact.")
    return remote_name, remote_path


def _remote_sha256(ftps: Any, remote_name: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    def consume(data: bytes) -> None:
        nonlocal count
        digest.update(data)
        count += len(data)
    ftps.retrbinary(f"RETR {remote_name}", consume)
    return digest.hexdigest(), count


def verify_remote_artifact_v32(
    *,
    ip_address: str,
    access_code: str,
    remote_name: str,
    expected_sha256: str,
    expected_size: int,
    remote_dir: str = "/cache",
    timeout: float = 30.0,
    ftp_factory=SessionReuseImplicitFTP_TLS,
) -> dict[str, Any]:
    if not access_code:
        raise Gate4V40Error("Access Code cannot be empty.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    ftp = ftp_factory(context=context, timeout=timeout)
    try:
        ftp.connect(ip_address, FTPS_PORT, timeout=timeout)
        ftp.login(FTPS_USERNAME, access_code)
        ftp.prot_p()
        ftp.cwd(remote_dir)
        remote_size = ftp.size(remote_name)
        if remote_size is None or int(remote_size) != expected_size:
            raise Gate4V40Error(
                f"Remote artifact SIZE mismatch: expected={expected_size}, observed={remote_size}"
            )
        remote_sha, downloaded_size = _remote_sha256(ftp, remote_name)
        if downloaded_size != expected_size:
            raise Gate4V40Error("Remote artifact download size mismatch during Gate 4 preflight.")
        if remote_sha != expected_sha256:
            raise Gate4V40Error("Remote artifact SHA-256 changed after Gate 3 v3.3.1.")
        return {
            "remote_directory": remote_dir,
            "remote_name": remote_name,
            "remote_size_bytes": int(remote_size),
            "remote_size_verified": True,
            "remote_sha256": remote_sha,
            "remote_sha256_verified": True,
        }
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def _passive_status_once(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    if not access_code:
        raise Gate4V40Error("Access Code cannot be empty.")
    if mqtt is None:
        raise Gate4V40Error("paho-mqtt is required for Gate 4 MQTT runtime checks.")
    event = threading.Event()
    state: dict[str, Any] = {"error": None, "print": None}
    topic = f"device/{device_id}/report"

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if int(reason_code) != 0:
            state["error"] = f"MQTT connect failed: {reason_code}"
            event.set()
            return
        result, _mid = client.subscribe(topic, qos=0)
        if result != mqtt.MQTT_ERR_SUCCESS:
            state["error"] = f"MQTT subscribe failed: {result}"
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
        client_id=f"m4-g4a-{uuid.uuid4().hex[:10]}",
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
    client.on_message = on_message
    try:
        client.connect(ip_address, MQTT_PORT, keepalive=30)
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
        raise Gate4V40Error(str(state["error"]))
    if not isinstance(state["print"], dict):
        raise Gate4V40Error("No X1C print-status object arrived during Gate 4A preflight.")
    return state["print"]


def _active_hms(print_obj: dict[str, Any]) -> list[Any]:
    hms = print_obj.get("hms")
    return hms if isinstance(hms, list) else []


def _preflight_checks(print_obj: dict[str, Any]) -> dict[str, Any]:
    gcode_state = str(print_obj.get("gcode_state", "")).upper()
    print_error = print_obj.get("print_error", print_obj.get("mc_print_error_code", 0))
    try:
        print_error_int = int(print_error)
    except Exception:
        print_error_int = -1
    nozzle_diameter = str(print_obj.get("nozzle_diameter", ""))
    sdcard = print_obj.get("sdcard")
    hms = _active_hms(print_obj)
    nozzle_temp = print_obj.get("nozzle_temper")
    bed_temp = print_obj.get("bed_temper")
    nozzle_target = print_obj.get("nozzle_target_temper")
    bed_target = print_obj.get("bed_target_temper")
    checks = {
        "gcode_state_idle": gcode_state == "IDLE",
        "print_error_zero": print_error_int == 0,
        "nozzle_diameter_0_4": nozzle_diameter in {"0.4", "0.40", "0.400"},
        "sdcard_available": sdcard is True,
        "hms_empty": len(hms) == 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "gcode_state": gcode_state,
            "print_error": print_error,
            "nozzle_diameter": nozzle_diameter,
            "sdcard": sdcard,
            "hms": hms,
            "nozzle_temper": nozzle_temp,
            "bed_temper": bed_temp,
            "nozzle_target_temper": nozzle_target,
            "bed_target_temper": bed_target,
            "wifi_signal": print_obj.get("wifi_signal"),
        },
    }


def run_gate4a(project_root: Path, access_code: str) -> dict[str, Any]:
    project_root = project_root.resolve()
    task_dir = _task_dir(project_root)
    identity = load_gate1_identity(project_root, REQUEST_ID)
    if identity.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate4V40Error("Gate 1 locked DEVICE_ID mismatch.")
    gate2 = _validate_gate2_v32(task_dir)
    gate3_path, gate3 = _find_gate3_v331(task_dir)
    audit_path, _audit, artifact_path, artifact_sha, gcode_entries = _validate_fixed_m3_audit(project_root)
    remote_name, remote_path = _gate3_remote_info(gate3, artifact_sha)
    if gate3.get("source_m3", {}).get("gcode_audit_file") not in {None, str(audit_path)}:
        raise Gate4V40Error("Gate 3 v3.3.1 did not use the fixed Phase 5 audit path.")

    remote = verify_remote_artifact_v32(
        ip_address=identity["ip_address"],
        access_code=access_code,
        remote_name=remote_name,
        expected_sha256=artifact_sha,
        expected_size=artifact_path.stat().st_size,
    )
    print_obj = _passive_status_once(
        ip_address=identity["ip_address"],
        device_id=EXPECTED_DEVICE_ID,
        access_code=access_code,
    )
    runtime = _preflight_checks(print_obj)
    if not runtime["passed"]:
        raise Gate4V40Error(f"Gate 4A runtime checks failed: {runtime['checks']}")

    if len(gcode_entries) != 1:
        raise Gate4V40Error(
            f"First real print requires exactly one audited G-code plate entry; observed {gcode_entries}"
        )
    gcode_entry = gcode_entries[0]
    if not gcode_entry.lower().endswith(".gcode"):
        raise Gate4V40Error("Audited internal G-code entry is invalid.")

    report_path = task_dir / GATE4A_REPORT
    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 4,
        "stage": "runtime_preflight_v40",
        "request_id": REQUEST_ID,
        "status": "runtime_preflight_passed",
        "created_at": _utc_now(),
        "created_unix": time.time(),
        "device_id": EXPECTED_DEVICE_ID,
        "printer_ip": identity["ip_address"],
        "source_gate2": {
            "report_file": str(task_dir / "m4_gate2_ftps_probe_v32.json"),
            "status": gate2.get("status"),
            "tls_session_reuse_on_data_channel": True,
        },
        "source_gate3": {
            "report_file": str(gate3_path),
            "version_lock": "3.3.1",
            "status": gate3.get("status"),
        },
        "source_m3": {
            "gcode_audit_file": str(audit_path),
            "artifact_path": str(artifact_path),
            "artifact_sha256": artifact_sha,
            "gcode_entry": gcode_entry,
        },
        "remote_artifact": remote | {"remote_path": remote_path},
        "runtime": runtime,
        "policy": {
            "mqtt_publish_count": 0,
            "control_command_count": 0,
            "print_start_command_count": 0,
            "access_code_stored": False,
            "artifact_reuploaded": False,
            "print_started": False,
            "first_print_external_spool_only": True,
        },
        "next_phase": "m4_gate4b_manual_first_print_start_v40",
    }
    _write_json_atomic(report_path, report)
    return report | {"report_file": str(report_path)}


def expected_start_phrase() -> str:
    return f"{START_PREFIX}{REQUEST_ID}_{EXPECTED_DEVICE_ID}"


def _load_fresh_gate4a(project_root: Path) -> dict[str, Any]:
    path = _task_dir(project_root) / GATE4A_REPORT
    report = _load_json(path)
    if report.get("status") != "runtime_preflight_passed":
        raise Gate4V40Error("Gate 4A runtime preflight has not passed.")
    if report.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate4V40Error("Gate 4A DEVICE_ID mismatch.")
    created_unix = report.get("created_unix")
    try:
        age = time.time() - float(created_unix)
    except Exception as exc:
        raise Gate4V40Error("Gate 4A timestamp is invalid.") from exc
    if age < 0 or age > PREFLIGHT_MAX_AGE_SECONDS:
        raise Gate4V40Error(
            f"Gate 4A preflight is stale ({age:.1f}s old). Re-run Gate 4A before print start."
        )
    return report


def _build_project_file_payload(preflight: dict[str, Any], sequence_id: str) -> dict[str, Any]:
    remote = preflight["remote_artifact"]
    source_m3 = preflight["source_m3"]
    remote_name = remote["remote_name"]
    gcode_entry = source_m3["gcode_entry"]
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
            "url": f"ftp:///cache/{remote_name}",
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


def publish_first_print_once(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
    payload: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    if not access_code:
        raise Gate4V40Error("Access Code cannot be empty.")
    if mqtt is None:
        raise Gate4V40Error("paho-mqtt is required for Gate 4 print start.")
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
        if int(reason_code) != 0:
            state["error"] = f"MQTT connect failed: {reason_code}"
            event.set()
            return
        state["connected"] = True
        result, _mid = client.subscribe(report_topic, qos=0)
        if result != mqtt.MQTT_ERR_SUCCESS:
            state["error"] = f"MQTT subscribe failed: {result}"
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
        client_id=f"m4-g4b-{uuid.uuid4().hex[:10]}",
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
    client.on_message = on_message

    try:
        client.connect(ip_address, MQTT_PORT, keepalive=30)
        client.loop_start()
        deadline = time.monotonic() + timeout
        while not state["connected"] and time.monotonic() < deadline and not state["error"]:
            time.sleep(0.05)
        if state["error"]:
            raise Gate4V40Error(str(state["error"]))
        if not state["connected"]:
            raise Gate4V40Error("MQTT connection timed out before print start; nothing was published.")
        info = client.publish(request_topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise Gate4V40Error(f"MQTT publish failed before acceptance: rc={info.rc}")
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


def run_gate4b(project_root: Path, access_code: str, authorization_phrase: str) -> dict[str, Any]:
    project_root = project_root.resolve()
    required = expected_start_phrase()
    if authorization_phrase.strip() != required:
        raise Gate4V40Error(f"Explicit print-start authorization not granted. Required phrase: {required}")
    preflight = _load_fresh_gate4a(project_root)
    identity = load_gate1_identity(project_root, REQUEST_ID)
    if identity.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate4V40Error("Gate 1 DEVICE_ID changed before Gate 4B.")
    sequence_id = str(int(time.time() * 1000))
    payload = _build_project_file_payload(preflight, sequence_id)
    result = publish_first_print_once(
        ip_address=identity["ip_address"],
        device_id=EXPECTED_DEVICE_ID,
        access_code=access_code,
        payload=payload,
    )
    task_dir = _task_dir(project_root)
    report_path = task_dir / GATE4B_REPORT
    status = (
        "first_print_started"
        if result["outcome"] == "print_start_confirmed"
        else "first_print_start_rejected"
        if result["outcome"] == "command_rejected"
        else "first_print_start_outcome_unknown"
    )
    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 4,
        "stage": "manual_first_print_start_v40",
        "request_id": REQUEST_ID,
        "status": status,
        "created_at": _utc_now(),
        "device_id": EXPECTED_DEVICE_ID,
        "source_preflight": str(task_dir / GATE4A_REPORT),
        "authorization": {
            "required_phrase": required,
            "phrase_matched": True,
            "human_authorized_print_start": True,
        },
        "print_command": {
            "command": "project_file",
            "sequence_id": sequence_id,
            "url": payload["print"]["url"],
            "param": payload["print"]["param"],
            "use_ams": False,
            "external_spool_only": True,
            "bed_levelling": True,
            "flow_cali": True,
            "vibration_cali": True,
            "layer_inspect": True,
            "timelapse": False,
        },
        "result": result,
        "policy": {
            "access_code_stored": False,
            "automatic_retry_allowed": False,
            "raw_gcode_command_allowed": False,
            "print_start_command_count": result["mqtt_publish_count"],
            "artifact_upload_count": 0,
            "gate3_rerun": False,
        },
        "next_phase": (
            "m4_gate5_live_print_monitoring"
            if status == "first_print_started"
            else None
        ),
    }
    _write_json_atomic(report_path, report)
    return report | {"report_file": str(report_path)}
