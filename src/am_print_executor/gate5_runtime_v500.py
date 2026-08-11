from __future__ import annotations

import argparse
import getpass
import importlib
import json
import ssl
import threading
import time
import uuid
from pathlib import Path
from typing import Any

REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"

GATE4B_REPORT = "m4_gate4b_developer_mode_first_print_v411.json"
GATE5A_REPORT = "m4_gate5_post_print_acceptance_v500.json"
LIVE_JSONL = "m4_gate5_live_monitor_v500.jsonl"
LIVE_SUMMARY = "m4_gate5_live_monitor_summary_v500.json"

CONFIRM_PHRASE = f"CONFIRM_PRINT_COMPLETED_{REQUEST_ID}_{EXPECTED_DEVICE_ID}"


class Gate5V500Error(RuntimeError):
    pass


def _v40():
    return importlib.import_module("am_print_executor.gate4_runtime_v40")


def _v407():
    return importlib.import_module("am_print_executor.gate4_runtime_v407")


def _ftps_v32():
    return importlib.import_module("am_print_executor.ftps_probe_v32")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate5V500Error(f"Required report is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate5V500Error(f"Invalid JSON report: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise Gate5V500Error(f"Expected JSON object: {path}")
    return data


def _validate_gate4b(project_root: Path) -> tuple[Path, dict[str, Any]]:
    path = project_root / "outputs" / "m4" / REQUEST_ID / GATE4B_REPORT
    report = _load_json(path)

    if report.get("version") != "4.1.1":
        raise Gate5V500Error("Gate4B version lock is not 4.1.1.")
    if report.get("request_id") != REQUEST_ID:
        raise Gate5V500Error("Gate4B request_id mismatch.")
    if report.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate5V500Error("Gate4B device_id mismatch.")
    if report.get("status") != "first_print_started":
        raise Gate5V500Error(
            f"Gate4B did not record a confirmed start: status={report.get('status')!r}"
        )

    dispatch = report.get("dispatch")
    if not isinstance(dispatch, dict):
        raise Gate5V500Error("Gate4B dispatch record is missing.")
    if dispatch.get("published") is not True:
        raise Gate5V500Error("Gate4B did not publish the print-start command.")
    if int(dispatch.get("mqtt_publish_count", -1)) != 1:
        raise Gate5V500Error("Gate4B print-start publish count is not exactly one.")
    if int(dispatch.get("automatic_retry_count", -1)) != 0:
        raise Gate5V500Error("Gate4B automatic retry count is not zero.")

    return path, report


def _terminal_evaluation(print_obj: dict[str, Any]) -> dict[str, Any]:
    state = str(print_obj.get("gcode_state", "")).strip().upper()
    hms = print_obj.get("hms")
    print_error = print_obj.get("print_error")
    percent = print_obj.get("mc_percent")
    remaining = print_obj.get("mc_remaining_time")

    clean = (
        print_error == 0
        and isinstance(hms, list)
        and len(hms) == 0
    )

    idle_complete = state == "IDLE" and clean

    finish_complete = (
        state == "FINISH"
        and clean
        and isinstance(percent, (int, float))
        and float(percent) >= 100.0
        and isinstance(remaining, (int, float))
        and float(remaining) <= 0.0
    )

    return {
        "passed": idle_complete or finish_complete,
        "state": state,
        "idle_complete": idle_complete,
        "finish_complete": finish_complete,
        "print_error_zero": print_error == 0,
        "hms_empty": isinstance(hms, list) and len(hms) == 0,
        "observed": {
            "gcode_state": print_obj.get("gcode_state"),
            "mc_percent": percent,
            "mc_remaining_time": remaining,
            "print_error": print_error,
            "hms": hms,
            "nozzle_temper": print_obj.get("nozzle_temper"),
            "bed_temper": print_obj.get("bed_temper"),
            "chamber_temper": print_obj.get("chamber_temper"),
            "layer_num": print_obj.get("layer_num"),
            "total_layer_num": print_obj.get("total_layer_num"),
            "subtask_name": print_obj.get("subtask_name"),
        },
    }


def run_post_print_acceptance(
    project_root: Path,
    access_code: str,
    confirmation_phrase: str,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    v40 = _v40()
    v407 = _v407()
    ftps = _ftps_v32()

    gate4b_path, gate4b = _validate_gate4b(project_root)

    if confirmation_phrase != CONFIRM_PHRASE:
        raise Gate5V500Error(
            "Physical-completion confirmation phrase mismatch; no acceptance report written."
        )

    identity = ftps.load_gate1_identity(project_root, REQUEST_ID)
    if identity.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate5V500Error("Gate1 locked DEVICE_ID mismatch.")
    ip_address = identity.get("ip_address")
    if not isinstance(ip_address, str) or not ip_address:
        raise Gate5V500Error("Gate1 locked printer IP is missing.")

    print_obj, telemetry = v407._robust_status_read(ip_address, access_code)
    terminal = _terminal_evaluation(print_obj)

    if not terminal["passed"]:
        raise Gate5V500Error(
            f"Printer is not in a clean completed terminal state: {terminal}"
        )

    artifact_lock = gate4b.get("artifact_integrity_lock")
    if not isinstance(artifact_lock, dict):
        raise Gate5V500Error("Gate4B artifact integrity lock is missing.")
    if artifact_lock.get("remote_sha256_verified") is not True:
        raise Gate5V500Error("Gate4B artifact SHA-256 lock is not verified.")

    task_dir = project_root / "outputs" / "m4" / REQUEST_ID
    report_path = task_dir / GATE5A_REPORT

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 5,
        "version": "5.0.0",
        "stage": "post_print_acceptance",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": "first_real_print_accepted",
        "created_at": v40._utc_now(),
        "created_unix": time.time(),
        "source_gate4b": {
            "report_file": str(gate4b_path),
            "version": gate4b.get("version"),
            "status": gate4b.get("status"),
            "print_start_sequence_id": gate4b.get("print_command", {}).get("sequence_id"),
        },
        "artifact_integrity_lock": artifact_lock,
        "physical_confirmation": {
            "required_phrase": CONFIRM_PHRASE,
            "matched": True,
            "user_confirms_complete_physical_print": True,
            "user_confirms_process_finished_smoothly": True,
        },
        "terminal_state": terminal,
        "telemetry": telemetry,
        "policy": {
            "read_only": True,
            "mqtt_control_publish_count": 0,
            "print_start_command_count": 0,
            "stop_command_count": 0,
            "pause_command_count": 0,
            "parameter_adjustment_count": 0,
            "access_code_stored": False,
        },
        "next_phase": "m4_gate5b_live_monitor_validation",
    }

    v40._write_json_atomic(report_path, report)
    return report | {"report_file": str(report_path)}


def _reason_failed(reason_code: Any) -> bool:
    marker = getattr(reason_code, "is_failure", None)
    if marker is not None:
        return bool(marker)
    return bool(reason_code != 0)


def _safe_float(value: Any) -> float | None:
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


def _snapshot(print_obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp_unix": time.time(),
        "gcode_state": print_obj.get("gcode_state"),
        "mc_percent": print_obj.get("mc_percent"),
        "mc_remaining_time": print_obj.get("mc_remaining_time"),
        "nozzle_temper": print_obj.get("nozzle_temper"),
        "nozzle_target_temper": print_obj.get("nozzle_target_temper"),
        "bed_temper": print_obj.get("bed_temper"),
        "bed_target_temper": print_obj.get("bed_target_temper"),
        "chamber_temper": print_obj.get("chamber_temper"),
        "layer_num": print_obj.get("layer_num"),
        "total_layer_num": print_obj.get("total_layer_num"),
        "print_error": print_obj.get("print_error"),
        "hms": print_obj.get("hms"),
        "wifi_signal": print_obj.get("wifi_signal"),
        "subtask_name": print_obj.get("subtask_name"),
        "gcode_file": print_obj.get("gcode_file"),
        "stg_cur": print_obj.get("stg_cur"),
    }


def run_live_monitor(
    project_root: Path,
    access_code: str,
    wait_for_start_seconds: int,
    max_monitor_seconds: int,
) -> dict[str, Any]:
    import paho.mqtt.client as mqtt

    project_root = project_root.resolve()
    v40 = _v40()
    ftps = _ftps_v32()

    identity = ftps.load_gate1_identity(project_root, REQUEST_ID)
    if identity.get("device_id") != EXPECTED_DEVICE_ID:
        raise Gate5V500Error("Gate1 locked DEVICE_ID mismatch.")
    ip_address = identity.get("ip_address")
    if not isinstance(ip_address, str) or not ip_address:
        raise Gate5V500Error("Gate1 locked printer IP is missing.")

    task_dir = project_root / "outputs" / "m4" / REQUEST_ID
    task_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = task_dir / LIVE_JSONL
    summary_path = task_dir / LIVE_SUMMARY

    if jsonl_path.exists() or summary_path.exists():
        raise Gate5V500Error(
            "Gate5 live-monitor output already exists; refusing to overwrite. "
            "Archive/rename it before a new monitored print."
        )

    connected = threading.Event()
    subscribed = threading.Event()
    first_message = threading.Event()
    stop_event = threading.Event()

    callback_error: list[str] = []
    snapshots: list[dict[str, Any]] = []
    states: list[str] = []
    saw_active = False
    terminal_after_active = False
    last_written_key: tuple[Any, ...] | None = None

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if _reason_failed(reason_code):
            callback_error.append(f"MQTT connect failed: {reason_code}")
            connected.set()
            return
        connected.set()
        client.subscribe(f"device/{EXPECTED_DEVICE_ID}/report", qos=0)

    def on_subscribe(client, userdata, mid, reason_codes, properties=None):
        if any(getattr(x, "is_failure", False) for x in (reason_codes or [])):
            callback_error.append("MQTT subscription refused.")
        subscribed.set()

    def on_message(client, userdata, msg):
        nonlocal saw_active, terminal_after_active, last_written_key

        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="strict"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        pobj = payload.get("print")
        if not isinstance(pobj, dict):
            return

        first_message.set()
        snap = _snapshot(pobj)
        state = str(snap.get("gcode_state") or "").upper()

        if state and (not states or states[-1] != state):
            states.append(state)

        if state in {"PREPARE", "RUNNING", "SLICING", "PAUSE", "PAUSED"}:
            saw_active = True

        if saw_active and state in {"FINISH", "IDLE", "FAILED"}:
            terminal_after_active = True

        key = (
            state,
            snap.get("mc_percent"),
            snap.get("mc_remaining_time"),
            snap.get("layer_num"),
            snap.get("print_error"),
            json.dumps(snap.get("hms"), sort_keys=True, ensure_ascii=False),
        )

        # Log on meaningful changes; also retain the latest snapshot in memory.
        if key != last_written_key:
            with jsonl_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + "\n")
            last_written_key = key
            snapshots.append(snap)

        if terminal_after_active:
            stop_event.set()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"nl-am-gate5-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    start_unix = time.time()
    try:
        client.connect(ip_address, 8883, keepalive=60)
        client.loop_start()

        if not connected.wait(12):
            raise Gate5V500Error("Gate5 MQTT connection timeout.")
        if callback_error:
            raise Gate5V500Error(callback_error[-1])
        if not subscribed.wait(8):
            raise Gate5V500Error("Gate5 MQTT subscription timeout.")
        if callback_error:
            raise Gate5V500Error(callback_error[-1])

        # Wait for an unsolicited status first. If silent, ask once for full status.
        if not first_message.wait(10):
            request = {
                "pushing": {
                    "sequence_id": str(int(time.time() * 1000)),
                    "command": "pushall",
                    "version": 1,
                    "push_target": 1,
                }
            }
            info = client.publish(
                f"device/{EXPECTED_DEVICE_ID}/request",
                json.dumps(request, separators=(",", ":")),
                qos=0,
                retain=False,
            )
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise Gate5V500Error(f"Gate5 one-shot pushall failed locally rc={info.rc}")
            if not first_message.wait(30):
                raise Gate5V500Error("Gate5 received no X1C status after one pushall request.")

        active_deadline = time.time() + max(0, wait_for_start_seconds)
        while not saw_active and time.time() < active_deadline:
            if stop_event.wait(0.5):
                break

        if not saw_active:
            raise Gate5V500Error(
                "No active print state was observed within the configured wait-for-start window."
            )

        monitor_deadline = time.time() + max(1, max_monitor_seconds)
        while not stop_event.is_set() and time.time() < monitor_deadline:
            time.sleep(0.5)

    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

    end_unix = time.time()

    if not snapshots:
        raise Gate5V500Error("Gate5 captured no status snapshots.")

    nozzle_values = [
        x for x in (_safe_float(s.get("nozzle_temper")) for s in snapshots) if x is not None
    ]
    bed_values = [
        x for x in (_safe_float(s.get("bed_temper")) for s in snapshots) if x is not None
    ]

    nonzero_errors = [
        s.get("print_error")
        for s in snapshots
        if s.get("print_error") not in (0, "0", None)
    ]
    hms_events = [
        s.get("hms")
        for s in snapshots
        if isinstance(s.get("hms"), list) and len(s.get("hms")) > 0
    ]

    final = snapshots[-1]
    final_state = str(final.get("gcode_state") or "").upper()
    success_terminal = (
        final_state in {"FINISH", "IDLE"}
        and not nonzero_errors
        and not hms_events
    )

    summary = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 5,
        "version": "5.0.0",
        "stage": "live_print_monitor",
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": (
            "monitored_print_completed_cleanly"
            if success_terminal
            else "monitored_print_ended_with_attention_required"
        ),
        "monitor_started_unix": start_unix,
        "monitor_ended_unix": end_unix,
        "monitor_duration_seconds": round(end_unix - start_unix, 3),
        "state_transitions": states,
        "sample_count": len(snapshots),
        "saw_active_print": saw_active,
        "terminal_after_active": terminal_after_active,
        "final_snapshot": final,
        "max_nozzle_temperature_c": max(nozzle_values) if nozzle_values else None,
        "max_bed_temperature_c": max(bed_values) if bed_values else None,
        "nonzero_print_errors": nonzero_errors,
        "hms_events": hms_events,
        "telemetry_file": str(jsonl_path),
        "policy": {
            "read_only_monitoring": True,
            "print_start_command_count": 0,
            "pause_command_count": 0,
            "stop_command_count": 0,
            "parameter_adjustment_count": 0,
            "one_shot_status_request_max": 1,
            "automatic_control_enabled": False,
            "access_code_stored": False,
        },
        "next_phase": (
            "m4_gate6_optimization_policy"
            if success_terminal
            else "m4_gate5_review_monitoring_anomalies"
        ),
    }

    v40._write_json_atomic(summary_path, summary)
    return summary | {"report_file": str(summary_path)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="M4 Gate5 v5.0.0 post-print acceptance and live monitor")
    sub = p.add_subparsers(dest="mode", required=True)

    a = sub.add_parser("accept")
    a.add_argument("--project-root", required=True)

    m = sub.add_parser("monitor")
    m.add_argument("--project-root", required=True)
    m.add_argument("--wait-for-start-seconds", type=int, default=600)
    m.add_argument("--max-monitor-seconds", type=int, default=21600)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(args.project_root)
    access_code = getpass.getpass(
        "X1C Developer Mode Access Code (hidden; not stored): "
    ).strip()

    try:
        if args.mode == "accept":
            print("")
            print("This step records the already-completed physical print.")
            print("Type exactly:")
            print(CONFIRM_PHRASE)
            phrase = input("Completion confirmation: ").strip()
            result = run_post_print_acceptance(project_root, access_code, phrase)
        else:
            result = run_live_monitor(
                project_root,
                access_code,
                args.wait_for_start_seconds,
                args.max_monitor_seconds,
            )

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Gate5V500Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 5,
            "version": "5.0.0",
            "status": "gate5_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 5,
            "version": "5.0.0",
            "status": "gate5_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
