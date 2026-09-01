from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import ssl
import socket
import threading
import time
import uuid

from pathlib import Path
from typing import Any


class R7AMSError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise R7AMSError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise R7AMSError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise R7AMSError(f"Expected JSON object: {path}")
    return obj


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalize_color(value: Any) -> str | None:
    text = str(value or "").strip().upper().lstrip("#")
    if len(text) >= 6:
        text = text[:6]
    if len(text) != 6:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return "#" + text


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def extract_ams_trays(print_obj: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract physical AMS trays from X1C pushall/status payload.

    Expected shape commonly includes:
      print.ams.ams[<ams_index>].tray[<tray_index>]
    but field names are treated defensively.
    """
    ams_root = print_obj.get("ams")
    if not isinstance(ams_root, dict):
        return []

    units = ams_root.get("ams")
    if not isinstance(units, list):
        return []

    rows: list[dict[str, Any]] = []

    for unit_index, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue

        raw_unit_id = unit.get("id", unit_index)
        try:
            unit_id = int(raw_unit_id)
        except (TypeError, ValueError):
            unit_id = unit_index

        trays = unit.get("tray")
        if not isinstance(trays, list):
            continue

        for tray_index, tray in enumerate(trays):
            if not isinstance(tray, dict):
                continue

            raw_tray_id = tray.get("id", tray_index)
            try:
                tray_id_local = int(raw_tray_id)
            except (TypeError, ValueError):
                tray_id_local = tray_index

            # For the first AMS this is exactly 0..3, matching the X1C wire mapping.
            global_tray_id = unit_id * 4 + tray_id_local

            row = {
                "ams_index": unit_id,
                "tray_index": tray_id_local,
                "global_tray_id": global_tray_id,
                "human_slot": global_tray_id + 1,
                "tray_color_raw": tray.get("tray_color"),
                "tray_color": normalize_color(tray.get("tray_color")),
                "tray_type": str(tray.get("tray_type") or "").strip().upper(),
                "tray_sub_brands": str(tray.get("tray_sub_brands") or "").strip(),
                "tray_info_idx": tray.get("tray_info_idx"),
                "tray_id_name": tray.get("tray_id_name"),
                "tray_uuid": tray.get("tray_uuid"),
                "tray_weight": tray.get("tray_weight"),
                "remain": tray.get("remain"),
                "k": tray.get("k"),
                "n": tray.get("n"),
            }
            rows.append(row)

    return rows


def match_material(
    trays: list[dict[str, Any]],
    *,
    logical_id: int,
    semantic_label: str,
    expected_color: str,
    expected_type: str = "PLA",
) -> dict[str, Any]:
    expected_color = normalize_color(expected_color)
    expected_type = expected_type.strip().upper()

    candidates = [
        row for row in trays
        if row.get("tray_color") == expected_color
        and row.get("tray_type") == expected_type
    ]

    return {
        "logical_filament_id": logical_id,
        "semantic_label": semantic_label,
        "expected_color": expected_color,
        "expected_type": expected_type,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "unique_match": len(candidates) == 1,
        "selected": candidates[0] if len(candidates) == 1 else None,
    }


def reason_failed(reason_code: Any) -> bool:
    marker = getattr(reason_code, "is_failure", None)
    if marker is not None:
        try:
            return bool(marker)
        except Exception:
            pass
    return str(reason_code).strip().lower() not in {"0", "success"}



def tcp_tls_preflight(
    *,
    ip_address: str,
    port: int = 8883,
    attempts: int = 3,
) -> dict[str, Any]:
    tcp_pass = False
    tls_pass = False
    tcp_errors: list[str] = []
    tls_errors: list[str] = []

    for attempt in range(1, attempts + 1):
        raw = None
        try:
            raw = socket.create_connection(
                (ip_address, port),
                timeout=8,
            )
            tcp_pass = True

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            ssock = ctx.wrap_socket(
                raw,
                server_hostname=ip_address,
            )
            raw = None

            try:
                tls_version = ssock.version()
                tls_pass = True
                return {
                    "tcp_pass": True,
                    "tls_pass": True,
                    "tls_version": tls_version,
                    "attempts_used": attempt,
                    "tcp_errors": tcp_errors,
                    "tls_errors": tls_errors,
                }
            finally:
                ssock.close()

        except Exception as exc:
            text = f"attempt {attempt}: {type(exc).__name__}: {exc}"
            if not tcp_pass:
                tcp_errors.append(text)
            else:
                tls_errors.append(text)

        finally:
            if raw is not None:
                try:
                    raw.close()
                except Exception:
                    pass

        time.sleep(1.0)

    return {
        "tcp_pass": tcp_pass,
        "tls_pass": tls_pass,
        "tls_version": None,
        "attempts_used": attempts,
        "tcp_errors": tcp_errors,
        "tls_errors": tls_errors,
    }


def mqtt_connect_once(
    *,
    mqtt: Any,
    ip_address: str,
    device_id: str,
    access_code: str,
    connect_timeout: float = 18.0,
) -> tuple[Any, dict[str, Any], threading.Event, threading.Event, threading.Event]:
    connected = threading.Event()
    subscribed = threading.Event()
    received = threading.Event()

    state: dict[str, Any] = {
        "error": None,
        "reason": None,
        "messages_seen": 0,
        "best_print": None,
        "best_tray_count": -1,
        "status_request_publish_count": 0,
    }

    report_topic = f"device/{device_id}/report"

    def on_connect(client, userdata, flags, reason_code, properties=None):
        state["reason"] = str(reason_code)

        if reason_failed(reason_code):
            state["error"] = f"MQTT connect rejected: {reason_code}"
            connected.set()
            return

        connected.set()
        client.subscribe(report_topic, qos=0)

    def on_subscribe(client, userdata, mid, reason_codes, properties=None):
        subscribed.set()

    def on_message(client, userdata, msg):
        if msg.topic != report_topic:
            return

        try:
            payload = json.loads(
                msg.payload.decode("utf-8", errors="strict")
            )
        except Exception:
            return

        pobj = payload.get("print")

        if not isinstance(pobj, dict):
            return

        state["messages_seen"] += 1
        trays = extract_ams_trays(pobj)

        if len(trays) > state["best_tray_count"]:
            state["best_tray_count"] = len(trays)
            state["best_print"] = pobj

        if len(trays) > 0:
            received.set()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"nl-am-r7-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
    )

    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    try:
        client.reconnect_delay_set(
            min_delay=1,
            max_delay=3,
        )
    except Exception:
        pass

    client.connect_async(
        ip_address,
        8883,
        keepalive=60,
    )

    client.loop_start()

    if not connected.wait(connect_timeout):
        try:
            client.loop_stop()
        except Exception:
            pass

        try:
            client.disconnect()
        except Exception:
            pass

        raise R7AMSError(
            "MQTT_CONNACK_TIMEOUT"
        )

    if state["error"]:
        try:
            client.loop_stop()
        except Exception:
            pass

        try:
            client.disconnect()
        except Exception:
            pass

        raise R7AMSError(
            str(state["error"])
        )

    return (
        client,
        state,
        connected,
        subscribed,
        received,
    )

def read_live_status(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
) -> dict[str, Any]:
    try:
        import paho.mqtt.client as mqtt
    except Exception as exc:
        raise R7AMSError(
            f"paho-mqtt import failed: {exc}"
        ) from exc

    preflight = tcp_tls_preflight(
        ip_address=ip_address,
        port=8883,
        attempts=3,
    )

    if not preflight["tcp_pass"]:
        raise R7AMSError(
            "TCP_8883_UNREACHABLE: "
            + "; ".join(preflight["tcp_errors"])
        )

    if not preflight["tls_pass"]:
        raise R7AMSError(
            "TLS_HANDSHAKE_FAILED: "
            + "; ".join(preflight["tls_errors"])
        )

    last_error: Exception | None = None

    for mqtt_attempt in range(1, 4):
        client = None

        try:
            (
                client,
                state,
                _connected,
                subscribed,
                received,
            ) = mqtt_connect_once(
                mqtt=mqtt,
                ip_address=ip_address,
                device_id=device_id,
                access_code=access_code,
                connect_timeout=18.0,
            )

            if not subscribed.wait(10):
                raise R7AMSError(
                    "MQTT_SUBSCRIBE_TIMEOUT"
                )

            received.wait(5)

            if not received.is_set():
                request = {
                    "pushing": {
                        "sequence_id": str(
                            int(time.time() * 1000)
                        ),
                        "command": "pushall",
                        "version": 1,
                        "push_target": 1,
                    }
                }

                info = client.publish(
                    f"device/{device_id}/request",
                    json.dumps(
                        request,
                        separators=(",", ":"),
                    ),
                    qos=1,
                    retain=False,
                )

                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise R7AMSError(
                        "READ_ONLY_PUSHALL_LOCAL_FAIL="
                        + str(info.rc)
                    )

                info.wait_for_publish(
                    timeout=8,
                )

                if not info.is_published():
                    raise R7AMSError(
                        "READ_ONLY_PUSHALL_NOT_PUBLISHED"
                    )

                state[
                    "status_request_publish_count"
                ] = 1

                if not received.wait(30):
                    raise R7AMSError(
                        "NO_AMS_STATUS_AFTER_PUSHALL"
                    )

            pobj = state["best_print"]

            if not isinstance(pobj, dict):
                raise R7AMSError(
                    "NO_USABLE_AMS_SNAPSHOT"
                )

            return {
                "messages_seen":
                    state["messages_seen"],

                "status_request_publish_count":
                    state[
                        "status_request_publish_count"
                    ],

                "print_object":
                    pobj,

                "trays":
                    extract_ams_trays(pobj),

                "network_preflight":
                    preflight,

                "mqtt_attempt_used":
                    mqtt_attempt,

                "mqtt_connect_reason":
                    state.get("reason"),
            }

        except Exception as exc:
            last_error = exc
            time.sleep(2.0)

        finally:
            if client is not None:
                try:
                    client.loop_stop()
                except Exception:
                    pass

                try:
                    client.disconnect()
                except Exception:
                    pass

    raise R7AMSError(
        "MQTT_RETRIES_EXHAUSTED: "
        + repr(last_error)
    )


def run_r7(
    *,
    ip_address: str,
    device_id: str,
    r6_report_path: Path,
    expected_gcode_sha: str,
    report_path: Path,
) -> dict[str, Any]:
    r6 = load_json(r6_report_path)

    if r6.get("status") != "r6_toolchange_material_consistency_pass":
        raise R7AMSError("Upstream R6 is not PASS.")

    r6_sha = (
        r6.get("artifact", {})
        .get("gcode_sha256")
    )

    if not isinstance(r6_sha, str):
        raise R7AMSError("R6 report lacks G-code SHA256.")

    if r6_sha.lower() != expected_gcode_sha.lower():
        raise R7AMSError(
            "R6 G-code SHA does not match the locked R4/R5 artifact."
        )

    logical = r6.get("logical_materials")
    if not isinstance(logical, dict):
        raise R7AMSError("R6 report lacks logical_materials.")

    l0 = logical.get("0")
    l1 = logical.get("1")

    if not isinstance(l0, dict) or not isinstance(l1, dict):
        raise R7AMSError("R6 logical material rows 0/1 are missing.")

    print("Enter current X1C LAN Access Code.")
    print("Input is hidden and is NOT written to disk.")
    access_code = getpass.getpass("X1C Access Code: ").strip()

    if not access_code:
        raise R7AMSError("Access Code is empty.")

    live = read_live_status(
        ip_address=ip_address,
        device_id=device_id,
        access_code=access_code,
    )

    trays = live["trays"]

    gray = match_material(
        trays,
        logical_id=0,
        semantic_label="Gray",
        expected_color=l0.get("expected_colour"),
        expected_type="PLA",
    )

    yellow = match_material(
        trays,
        logical_id=1,
        semantic_label="Yellow",
        expected_color=l1.get("expected_colour"),
        expected_type="PLA",
    )

    selected_ids = []
    if gray["selected"] is not None:
        selected_ids.append(gray["selected"]["global_tray_id"])
    if yellow["selected"] is not None:
        selected_ids.append(yellow["selected"]["global_tray_id"])

    distinct = (
        len(selected_ids) == 2
        and len(set(selected_ids)) == 2
    )

    wire_mapping = None
    if gray["unique_match"] and yellow["unique_match"] and distinct:
        wire_mapping = [
            gray["selected"]["global_tray_id"],
            yellow["selected"]["global_tray_id"],
            -1,
            -1,
            -1,
        ]

    checks = {
        "r6_pass":
            r6.get("status") == "r6_toolchange_material_consistency_pass",

        "gcode_sha_locked":
            r6_sha.lower() == expected_gcode_sha.lower(),

        "ams_trays_observed":
            len(trays) > 0,

        "gray_unique_live_match":
            gray["unique_match"],

        "yellow_unique_live_match":
            yellow["unique_match"],

        "gray_yellow_distinct_trays":
            distinct,

        "wire_mapping_constructed":
            wire_mapping is not None,
    }

    failed = [
        key for key, value in checks.items()
        if not value
    ]

    status = (
        "r7_ams_mapping_pass"
        if not failed
        else "r7_ams_mapping_fail"
    )

    report = {
        "schema_version": "r7-live-ams-mapping-v2",
        "module": "M4",
        "stage": "R7_AMS_MAPPING",
        "status": status,
        "device": {
            "device_id": device_id,
            "ip_address": ip_address,
            "mqtt_tls_port": 8883,
        },
        "upstream": {
            "r6_report": str(Path(r6_report_path).resolve()),
            "r6_status": r6.get("status"),
            "gcode_sha256": r6_sha,
        },
        "live_snapshot": {
            "messages_seen": live["messages_seen"],
            "status_request_publish_count":
                live["status_request_publish_count"],
            "trays": trays,
            "network_preflight":
                live.get("network_preflight"),
            "mqtt_attempt_used":
                live.get("mqtt_attempt_used"),
            "mqtt_connect_reason":
                live.get("mqtt_connect_reason"),
        },
        "logical_to_physical": {
            "0": gray,
            "1": yellow,
        },
        "ams_mapping_two_logical_filaments": (
            selected_ids if len(selected_ids) == 2 else None
        ),
        "x1c_wire_mapping": wire_mapping,
        "checks": checks,
        "failed_checks": failed,
        "policy": {
            "old_ams_snapshot_reused": False,
            "remain_interpreted_as_grams": False,
            "access_code_stored": False,
            "mapping_is_live_snapshot_bound": True,
        },
        "safety": {
            "network_used": True,
            "printer_contacted": True,
            "read_only_status_request_only": True,
            "ftps_upload_count": 0,
            "print_start_command_count": 0,
            "pause_command_count": 0,
            "stop_command_count": 0,
            "resume_command_count": 0,
            "parameter_adjustment_count": 0,
        },
        "next_gate": (
            "R8_MATERIAL_CAPACITY_PREFLIGHT"
            if status == "r7_ams_mapping_pass"
            else None
        ),
    }

    write_json(report_path, report)
    report["report_path"] = str(Path(report_path).resolve())

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "R7 live read-only X1C AMS snapshot and logical-to-physical "
            "material mapping."
        )
    )

    parser.add_argument("--ip", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--r6-report", required=True)
    parser.add_argument("--expected-gcode-sha", required=True)
    parser.add_argument("--report", required=True)

    args = parser.parse_args()

    result = run_r7(
        ip_address=args.ip,
        device_id=args.device_id,
        r6_report_path=Path(args.r6_report),
        expected_gcode_sha=args.expected_gcode_sha,
        report_path=Path(args.report),
    )

    print()
    print("=== R7 LIVE AMS MAPPING V2 ===")
    print("DEVICE_ID=", result["device"]["device_id"])
    print("PRINTER_IP=", result["device"]["ip_address"])
    pre = result["live_snapshot"].get("network_preflight") or {}
    print("TCP_8883_PASS=", pre.get("tcp_pass"))
    print("TLS_HANDSHAKE_PASS=", pre.get("tls_pass"))
    print("TLS_VERSION=", pre.get("tls_version"))
    print("MQTT_ATTEMPT_USED=", result["live_snapshot"].get("mqtt_attempt_used"))
    print("MQTT_CONNECT_REASON=", result["live_snapshot"].get("mqtt_connect_reason"))
    print("MQTT_MESSAGES_SEEN=", result["live_snapshot"]["messages_seen"])
    print(
        "READ_ONLY_PUSHALL_COUNT=",
        result["live_snapshot"]["status_request_publish_count"],
    )
    print()

    for row in result["live_snapshot"]["trays"]:
        print(
            "TRAY="
            + json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    print()
    print(
        "LOGICAL_0_GRAY=",
        result["logical_to_physical"]["0"],
    )
    print(
        "LOGICAL_1_YELLOW=",
        result["logical_to_physical"]["1"],
    )
    print(
        "AMS_MAPPING_TWO_LOGICAL_FILAMENTS=",
        result["ams_mapping_two_logical_filaments"],
    )
    print(
        "X1C_WIRE_MAPPING=",
        result["x1c_wire_mapping"],
    )
    print()
    print("FAILED_CHECKS=", result["failed_checks"])
    print("OLD_AMS_SNAPSHOT_REUSED=False")
    print("ACCESS_CODE_STORED=False")
    print("FTPS_UPLOAD_COUNT=0")
    print("PRINT_START_COMMAND_COUNT=0")
    print("PARAMETER_ADJUSTMENT_COUNT=0")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("REPORT=", result["report_path"])

    return 0 if result["status"] == "r7_ams_mapping_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
