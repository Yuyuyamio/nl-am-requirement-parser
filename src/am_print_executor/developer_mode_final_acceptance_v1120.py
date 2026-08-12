from __future__ import annotations

import argparse
import getpass
import json
import re
import ssl
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(r"E:\nl-am-requirement-parser-M2-source-20260804_152224")
REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
TASK_DIR = PROJECT_ROOT / "outputs" / "m4" / REQUEST_ID

EVENTS_OUT = TASK_DIR / "m4_developer_backend_final_live_events_v1120.jsonl"
REPORT_OUT = TASK_DIR / "m4_developer_backend_final_acceptance_v1120.json"


class FinalAcceptanceError(RuntimeError):
    pass


def _load_backend():
    from am_print_executor import developer_mode_backend_v1120 as backend
    return backend


def _load_identity():
    from am_print_executor.ftps_probe_v32 import load_gate1_identity
    ident = load_gate1_identity(PROJECT_ROOT, REQUEST_ID)
    if ident.get("device_id") != EXPECTED_DEVICE_ID:
        raise FinalAcceptanceError("Locked DEVICE_ID mismatch.")
    return ident


def _inspect_project_filaments(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise FinalAcceptanceError(f"Artifact missing: {path}")
    if not zipfile.is_zipfile(path):
        raise FinalAcceptanceError(f"Not a valid 3MF ZIP: {path}")

    result: dict[str, Any] = {
        "artifact": str(path),
        "project_filament_count": None,
        "filament_settings_id": None,
        "filament_colours": None,
        "used_tool_ids": [],
        "multi_material_candidate": False,
    }

    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())

        project_settings_name = None
        for cand in (
            "Metadata/project_settings.config",
            "Metadata/project_settings.json",
        ):
            if cand in names:
                project_settings_name = cand
                break

        if project_settings_name:
            try:
                obj = json.loads(zf.read(project_settings_name).decode("utf-8-sig"))
                if isinstance(obj, dict):
                    fsid = obj.get("filament_settings_id")
                    fcol = obj.get("filament_colour")
                    if isinstance(fsid, list):
                        result["filament_settings_id"] = fsid
                        result["project_filament_count"] = len(fsid)
                    if isinstance(fcol, list):
                        result["filament_colours"] = fcol
                        if result["project_filament_count"] is None:
                            result["project_filament_count"] = len(fcol)
            except Exception:
                pass

        gcode_names = sorted(
            n for n in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)
        )
        tools = set()
        for gname in gcode_names[:3]:
            try:
                text = zf.read(gname).decode("utf-8", errors="ignore")
            except Exception:
                continue

            # Conservative signals only. Ignore special tool values such as T255/T1000.
            for m in re.finditer(r"(?m)^\s*T(\d+)\s*(?:;.*)?$", text):
                tool = int(m.group(1))
                if 0 <= tool <= 15:
                    tools.add(tool)

            for m in re.finditer(r"(?m)^\s*M620\s+S(\d+)", text):
                tool = int(m.group(1))
                if 0 <= tool <= 15:
                    tools.add(tool)

        result["used_tool_ids"] = sorted(tools)

    count = result["project_filament_count"]
    result["multi_material_candidate"] = (
        (isinstance(count, int) and count >= 2)
        or len(result["used_tool_ids"]) >= 2
    )
    return result


def _scan_candidates() -> list[dict[str, Any]]:
    roots = [
        PROJECT_ROOT / "outputs" / "m3",
        PROJECT_ROOT / "outputs" / "m4",
    ]
    paths = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            paths.extend(root.rglob("*.gcode.3mf"))
        except Exception:
            pass

    unique = []
    seen = set()
    for p in sorted(paths, key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            rp = p.resolve()
        except Exception:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(rp)
        if len(unique) >= 20:
            break

    rows = []
    for p in unique:
        try:
            info = _inspect_project_filaments(p)
            info["mtime"] = p.stat().st_mtime
            rows.append(info)
        except Exception:
            pass
    return rows


def scan_only() -> int:
    rows = _scan_candidates()
    multi = [r for r in rows if r.get("multi_material_candidate")]

    print("=== M4 GUI-FREE FINAL ACCEPTANCE CANDIDATE SCAN ===")
    print(f"Candidates found: {len(rows)}")
    print(f"Multi-material candidates: {len(multi)}")
    print()

    for i, row in enumerate(rows[:10], 1):
        print(f"[{i}] {row['artifact']}")
        print(
            f"    project_filaments={row.get('project_filament_count')} "
            f"used_tools={row.get('used_tool_ids')} "
            f"multi_material_candidate={row.get('multi_material_candidate')}"
        )

    print()
    if multi:
        best = multi[0]
        print("BEST MULTI-MATERIAL CANDIDATE:")
        print(best["artifact"])
        print()
        print("Next live command template:")
        print(
            "powershell -ExecutionPolicy Bypass -File "
            r".\run_m4_final_gui_free_acceptance_v1010.ps1"
        )
        print()
        print("The runner will ask for this artifact path and an explicit AMS mapping.")
    else:
        print("No verified multi-material .gcode.3mf candidate was found.")
        print("Do not start a final AMS acceptance print yet.")
        print("A small two-material sliced project is the only missing print artifact.")
    return 0


def _extract_interesting_extra(print_obj: dict[str, Any]) -> dict[str, Any]:
    keys = ("ai", "detect", "inspect", "spaghetti", "halt")
    out = {}
    for k, v in print_obj.items():
        kl = str(k).lower()
        if any(marker in kl for marker in keys):
            out[str(k)] = v
    return out


def _extract_ams_state(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = f"{prefix}.{k}" if prefix else str(k)
            kl = str(k).lower()
            if any(x in kl for x in ("ams", "tray", "filament")):
                if isinstance(v, (str, int, float, bool)) or v is None:
                    out[kp] = v
            if isinstance(v, (dict, list)):
                out.update(_extract_ams_state(v, kp))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_extract_ams_state(v, f"{prefix}[{i}]"))
    return out


def _parse_mapping(text: str) -> list[int]:
    vals = [x.strip() for x in text.split(",") if x.strip()]
    if not vals:
        raise FinalAcceptanceError("AMS mapping cannot be empty.")
    try:
        result = [int(x) for x in vals]
    except ValueError as exc:
        raise FinalAcceptanceError("AMS mapping must be comma-separated integers.") from exc
    if not all(-1 <= x <= 255 for x in result):
        raise FinalAcceptanceError("AMS mapping values must be between -1 and 255.")
    return result


def _write_event(event: dict[str, Any]) -> None:
    with EVENTS_OUT.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def run_live(artifact: Path, mapping: list[int], monitor_seconds: int) -> int:
    import paho.mqtt.client as mqtt
    from am_print_executor.gate8fc_native_ai_correlation_v880 import correlate

    backend = _load_backend()
    identity = _load_identity()

    inspect = backend.inspect_gcode_3mf(artifact)
    mm = _inspect_project_filaments(Path(inspect["path"]))

    expected_count = mm.get("project_filament_count")
    if isinstance(expected_count, int) and expected_count > 0 and len(mapping) != expected_count:
        raise FinalAcceptanceError(
            f"AMS mapping length mismatch: project has {expected_count} filament preset(s), "
            f"mapping has {len(mapping)} value(s)."
        )

    if not mm.get("multi_material_candidate"):
        raise FinalAcceptanceError(
            "Selected artifact does not look multi-material. "
            "Final AMS acceptance is intentionally blocked."
        )

    if EVENTS_OUT.exists() or REPORT_OUT.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        archive = TASK_DIR / "archive" / f"before_v1010_{stamp}"
        archive.mkdir(parents=True, exist_ok=True)
        for p in (EVENTS_OUT, REPORT_OUT):
            if p.exists():
                p.replace(archive / p.name)
        print(f"[ARCHIVE] Previous V10.1 evidence preserved: {archive}")

    access_code = getpass.getpass(
        "X1C Access Code (hidden, never stored): "
    ).strip()
    if not access_code:
        raise FinalAcceptanceError("Access Code cannot be empty.")

    ip_address = identity["ip_address"]
    device_id = identity["device_id"]

    connected = threading.Event()
    subscribed = threading.Event()
    first_status = threading.Event()
    active = threading.Event()
    terminal = threading.Event()
    print_start_published = threading.Event()
    monitor_armed = threading.Event()
    callback_errors: list[str] = []

    last_snapshot: dict[str, Any] | None = None
    state_sequence: list[str] = []
    pre_start_state_sequence: list[str] = []
    event_count = 0
    xcam_seen = False
    ams_states: list[dict[str, Any]] = []
    raw_events: list[dict[str, Any]] = []
    active_job_identity: dict[str, str] = {}
    terminal_observation: dict[str, Any] = {}

    def reason_failed(reason_code: Any) -> bool:
        marker = getattr(reason_code, "is_failure", None)
        if marker is not None:
            return bool(marker)
        try:
            return int(reason_code) != 0
        except Exception:
            return str(reason_code).lower() not in {"0", "success"}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_failed(reason_code):
            callback_errors.append(f"MQTT connect rejected: {reason_code}")
            connected.set()
            return
        connected.set()
        client.subscribe(f"device/{device_id}/report", qos=0)

    def on_subscribe(client, userdata, mid, reason_codes, properties=None):
        if any(getattr(x, "is_failure", False) for x in (reason_codes or [])):
            callback_errors.append("MQTT subscription refused.")
        subscribed.set()

    def on_message(client, userdata, msg):
        nonlocal last_snapshot, event_count, xcam_seen
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="strict"))
        except Exception:
            return
        pobj = data.get("print") if isinstance(data, dict) else None
        if not isinstance(pobj, dict):
            return

        first_status.set()
        state = str(pobj.get("gcode_state") or "").upper()

        # Pre-publish status is baseline only; it cannot arm/end this run.
        if not print_start_published.is_set():
            if state and (not pre_start_state_sequence or pre_start_state_sequence[-1] != state):
                pre_start_state_sequence.append(state)
        else:
            if state and (not state_sequence or state_sequence[-1] != state):
                state_sequence.append(state)

        active_states = {"PREPARE", "RUNNING", "SLICING", "PAUSE", "PAUSED"}
        if print_start_published.is_set() and state in active_states:
            active.set()
            monitor_armed.set()
            for key in ("task_id", "subtask_id", "subtask_name", "gcode_file", "file"):
                value = pobj.get(key)
                if value not in (None, "", "0", 0):
                    active_job_identity.setdefault(key, str(value))

        terminal_candidate = state in {"FINISH", "FAILED"}
        if state == "IDLE":
            try:
                terminal_candidate = float(pobj.get("mc_percent") or 0) >= 100.0
            except Exception:
                terminal_candidate = False

        if monitor_armed.is_set() and terminal_candidate:
            identity_match = False
            # Strong identity only. Generic gcode_file/file paths such as
            # /data/Metadata/plate_1.gcode are shared by many jobs and must
            # never be sufficient to terminate the current run.
            strong_keys = ("task_id", "subtask_id", "subtask_name")
            for key in strong_keys:
                expected = active_job_identity.get(key)
                observed = pobj.get(key)
                if (
                    expected not in (None, "", "0", 0)
                    and observed not in (None, "", "0", 0)
                    and str(observed) == str(expected)
                ):
                    identity_match = True
                    break

            if identity_match:
                terminal_observation.clear()
                terminal_observation.update({
                    "state": state,
                    "task_id": pobj.get("task_id"),
                    "subtask_id": pobj.get("subtask_id"),
                    "subtask_name": pobj.get("subtask_name"),
                    "gcode_file": pobj.get("gcode_file"),
                    "mc_percent": pobj.get("mc_percent"),
                    "print_error": pobj.get("print_error"),
                    "hms": pobj.get("hms"),
                })
                terminal.set()

        xcam = pobj.get("xcam")
        if isinstance(xcam, dict):
            xcam_seen = True

        snapshot = {
            "gcode_state": pobj.get("gcode_state"),
            "print_error": pobj.get("print_error"),
            "hms": pobj.get("hms"),
            "layer_num": pobj.get("layer_num"),
            "total_layer_num": pobj.get("total_layer_num"),
            "xcam": xcam,
            "interesting_extra_fields": _extract_interesting_extra(pobj),
        }

        labels = []
        if last_snapshot is None:
            labels.append("initial_snapshot")
        else:
            if snapshot.get("xcam") != last_snapshot.get("xcam"):
                labels.append("xcam_changed")
            if snapshot.get("hms") != last_snapshot.get("hms"):
                labels.append("hms_changed")
            if snapshot.get("print_error") != last_snapshot.get("print_error"):
                labels.append("print_error_changed")
            if snapshot.get("gcode_state") != last_snapshot.get("gcode_state"):
                labels.append("printer_state_changed")
            if snapshot.get("layer_num") != last_snapshot.get("layer_num"):
                labels.append("layer_changed")
            if (
                snapshot.get("interesting_extra_fields")
                != last_snapshot.get("interesting_extra_fields")
            ):
                labels.append("native_ai_related_field_changed")

        if state in {"PREPARE", "RUNNING", "SLICING", "PAUSE", "PAUSED"}:
            labels.append("print_active")

        ams_state = _extract_ams_state(pobj)
        if ams_state and (not ams_states or ams_states[-1] != ams_state):
            ams_states.append(ams_state)

        event_count += 1
        event = {
            "event_index": event_count,
            "timestamp_unix": time.time(),
            "labels": sorted(set(labels)),
            "snapshot": snapshot,
            "raw_print_object": pobj,
        }
        raw_events.append(event)
        _write_event(event)
        last_snapshot = snapshot

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"nl-am-final-v1120-{int(time.time())}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    try:
        try:
            client.connect_timeout = 6.0
            client.reconnect_delay_set(min_delay=1, max_delay=3)
        except Exception:
            pass

        print("Connecting passive/native-AI monitor first...")
        client.connect_async(ip_address, 8883, keepalive=60)
        client.loop_start()

        if not connected.wait(45):
            raise FinalAcceptanceError("MQTT/TLS connection timeout.")
        if callback_errors:
            raise FinalAcceptanceError(callback_errors[-1])
        if not subscribed.wait(12):
            raise FinalAcceptanceError("MQTT subscribe timeout.")

        if not first_status.wait(8):
            pushall = {
                "pushing": {
                    "sequence_id": str(int(time.time() * 1000)),
                    "command": "pushall",
                    "version": 1,
                    "push_target": 1,
                }
            }
            info = client.publish(
                f"device/{device_id}/request",
                json.dumps(pushall, separators=(",", ":")),
                qos=1,
                retain=False,
            )
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise FinalAcceptanceError(
                    f"Read-only pushall publish failed locally rc={info.rc}"
                )
            info.wait_for_publish(timeout=8)
            if not info.is_published():
                raise FinalAcceptanceError(
                    "Read-only pushall did not complete locally."
                )
            if not first_status.wait(30):
                raise FinalAcceptanceError(
                    "MQTT connected/subscribed and read-only pushall was sent, "
                    "but no printer status was received."
                )

        print("[PASS] Native-AI/status monitor is listening.")
        print("Uploading audited/sliced artifact via FTPS...")
        upload = backend._ftps_upload_verified(Path(inspect["path"]), access_code)
        print("[PASS] FTPS upload + SHA256 verification complete.")

        sequence_id = str(int(time.time() * 1000))
        payload = backend.build_project_file_payload(
            remote_path=upload["remote_path"],
            gcode_entry=upload["gcode_entries"][0],
            sequence_id=sequence_id,
            use_ams=True,
            ams_mapping=mapping,
        )

        print("Starting print directly from Developer Mode backend (no Bambu Studio GUI)...")
        info = client.publish(
            f"device/{device_id}/request",
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            qos=0,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise FinalAcceptanceError(f"Print-start publish failed locally rc={info.rc}")
        info.wait_for_publish(timeout=8)
        if not info.is_published():
            raise FinalAcceptanceError("Print-start MQTT publish did not complete.")

        # Only now can status belong to this newly published run.
        print_start_published.set()

        if not active.wait(60):
            raise FinalAcceptanceError(
                "Print-start command was sent, but no active print state was observed within 60 s."
            )

        print("[PASS] Direct print start observed.")
        print("Monitoring until print terminal state or timeout...")

        terminal_reached = terminal.wait(max(60, monitor_seconds))
        if not terminal_reached:
            raise FinalAcceptanceError(
                "Monitoring timed out before a terminal state belonging to the current print was observed."
            )
        if terminal_observation.get("state") == "FAILED":
            raise FinalAcceptanceError(
                f"The current print entered FAILED state: {terminal_observation}"
            )

    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

    corr = correlate(raw_events)

    # Determine whether any AMS/tray/filament state actually changed during the run.
    ams_transition = len(ams_states) >= 2
    distinct_tray_values = {}
    for st in ams_states:
        for k, v in st.items():
            if "tray" in k.lower():
                distinct_tray_values.setdefault(k, set()).add(json.dumps(v, sort_keys=True))
    tray_transition_keys = sorted(
        k for k, vals in distinct_tray_values.items() if len(vals) >= 2
    )
    if tray_transition_keys:
        ams_transition = True

    final_status = (
        "gui_free_developer_mode_ams_native_ai_validated"
        if active.is_set() and xcam_seen and ams_transition
        else "gui_free_developer_mode_native_ai_validated_ams_transition_not_proven"
        if active.is_set() and xcam_seen
        else "final_acceptance_incomplete"
    )

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "FINAL-DEVELOPER-BACKEND",
        "version": "11.2.0",
        "created_unix": time.time(),
        "status": final_status,
        "project_root": str(PROJECT_ROOT),
        "device_id": device_id,
        "artifact": inspect,
        "artifact_multi_material_inspection": mm,
        "ams_mapping_logical": mapping,
        "ams_mapping_wire": json.dumps(mapping, separators=(",", ":")),
        "bambu_studio_gui_used": False,
        "developer_mode_direct_start": {
            "mqtt_publish_count": 1,
            "active_print_observed": active.is_set(),
            "pre_start_state_sequence": pre_start_state_sequence,
            "state_sequence": state_sequence,
            "active_job_identity": active_job_identity,
            "terminal_observation": terminal_observation,
            "terminal_observed": terminal.is_set(),
        },
        "native_ai": {
            "xcam_observed": xcam_seen,
            "event_count": event_count,
            "gate8fc_correlation": corr,
        },
        "ams": {
            "state_change_count": max(0, len(ams_states) - 1),
            "transition_observed": ams_transition,
            "tray_transition_keys": tray_transition_keys,
            "sample_states": ams_states[-10:],
        },
        "policy": {
            "access_code_stored": False,
            "bambu_studio_gui_required": False,
            "automatic_retry_print_publish": False,
            "print_start_publish_count": 1,
            "project_issued_pause_stop_resume_count": 0,
            "runtime_parameter_adjustment_count": 0,
        },
    }

    REPORT_OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print("=== M4 FINAL GUI-FREE ACCEPTANCE RESULT ===")
    print(f"Status: {final_status}")
    print(f"Active print observed: {active.is_set()}")
    print(f"Native xcam observed: {xcam_seen}")
    print(f"AMS transition observed: {ams_transition}")
    print(f"Gate8F-C: {corr.get('overall_interpretation')}")
    print(f"Events: {EVENTS_OUT}")
    print(f"Report: {REPORT_OUT}")

    return 0 if final_status == "gui_free_developer_mode_ams_native_ai_validated" else 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--artifact")
    parser.add_argument("--ams-mapping")
    parser.add_argument("--monitor-seconds", type=int, default=1200)
    args = parser.parse_args()

    if args.scan:
        return scan_only()

    if not args.artifact:
        raise FinalAcceptanceError("--artifact is required for live acceptance.")
    if not args.ams_mapping:
        raise FinalAcceptanceError("--ams-mapping is required for AMS live acceptance.")

    return run_live(
        Path(args.artifact),
        _parse_mapping(args.ams_mapping),
        args.monitor_seconds,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FinalAcceptanceError as exc:
        print(f"\n[FINAL ACCEPTANCE FAIL] {exc}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
