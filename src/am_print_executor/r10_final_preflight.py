from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import ssl
import threading
import time
import uuid

from pathlib import Path
from typing import Any


ACTIVE_STATES = {
    "RUNNING",
    "PRINTING",
    "PREPARE",
    "SLICING",
    "PAUSE",
    "PAUSED",
}


class R10Error(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with Path(path).open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(
        Path(path).read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(obj, dict):
        raise R10Error(
            f"Expected JSON object: {path}"
        )

    return obj


def normalize_colour(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = (
        str(value)
        .strip()
        .upper()
        .replace("#", "")
    )

    if len(text) == 8:
        text = text[:6]

    if not re.fullmatch(
        r"[0-9A-F]{6}",
        text,
    ):
        return None

    return "#" + text


def has_ams_detail(
    value: Any,
) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            low = str(key).lower()

            if "tray" in low:
                return True

            if (
                low == "ams"
                and isinstance(
                    child,
                    (dict, list),
                )
            ):
                return True

            if has_ams_detail(child):
                return True

    elif isinstance(value, list):
        return any(
            has_ams_detail(child)
            for child in value
        )

    return False


def extract_trays(
    print_obj: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(
        value: Any,
        path: str,
        current_ams: str | None = None,
        current_tray: str | None = None,
    ) -> None:
        if isinstance(value, dict):
            ams_id = current_ams
            tray_id = current_tray

            # An AMS object normally owns a tray list.
            if (
                isinstance(
                    value.get("tray"),
                    list,
                )
                and value.get("id")
                is not None
            ):
                ams_id = str(
                    value.get("id")
                )

            tray_semantic = any(
                key in value
                for key in (
                    "tray_color",
                    "tray_type",
                    "tray_info_idx",
                    "tray_uuid",
                )
            )

            if tray_semantic:
                if value.get("id") is not None:
                    tray_id = str(
                        value.get("id")
                    )

                rows.append(
                    {
                        "ams_id":
                            ams_id,

                        "tray_id":
                            tray_id,

                        "tray_type":
                            value.get(
                                "tray_type"
                            ),

                        "tray_color":
                            normalize_colour(
                                value.get(
                                    "tray_color"
                                )
                            ),

                        "tray_info_idx":
                            value.get(
                                "tray_info_idx"
                            ),

                        "tray_uuid":
                            value.get(
                                "tray_uuid"
                            ),

                        "remain":
                            value.get(
                                "remain"
                            ),

                        "source_path":
                            path,
                    }
                )

            for key, child in value.items():
                if (
                    key == "tray"
                    and isinstance(
                        child,
                        list,
                    )
                ):
                    for index, item in enumerate(
                        child
                    ):
                        item_id = (
                            str(item.get("id"))
                            if (
                                isinstance(
                                    item,
                                    dict,
                                )
                                and item.get("id")
                                is not None
                            )
                            else str(index)
                        )

                        walk(
                            item,
                            f"{path}.tray[{index}]",
                            ams_id,
                            item_id,
                        )

                    continue

                walk(
                    child,
                    f"{path}.{key}",
                    ams_id,
                    tray_id,
                )

        elif isinstance(value, list):
            for index, child in enumerate(
                value
            ):
                walk(
                    child,
                    f"{path}[{index}]",
                    current_ams,
                    current_tray,
                )

    walk(
        print_obj,
        "print",
    )

    dedup: dict[
        tuple[Any, ...],
        dict[str, Any],
    ] = {}

    for row in rows:
        key = (
            row.get("ams_id"),
            row.get("tray_id"),
            row.get("tray_type"),
            row.get("tray_color"),
            row.get("tray_info_idx"),
        )

        old = dedup.get(key)

        if (
            old is None
            or len(
                row["source_path"]
            )
            < len(
                old["source_path"]
            )
        ):
            dedup[key] = row

    return list(
        dedup.values()
    )


def reason_failed(
    reason_code: Any,
) -> bool:
    marker = getattr(
        reason_code,
        "is_failure",
        None,
    )

    if marker is not None:
        return bool(marker)

    try:
        return int(reason_code) != 0
    except Exception:
        return (
            str(reason_code)
            .strip()
            .lower()
            not in {
                "0",
                "success",
            }
        )


def capture_fresh_status(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        import paho.mqtt.client as mqtt
    except Exception as exc:
        raise R10Error(
            "paho-mqtt is unavailable."
        ) from exc

    done = threading.Event()

    state: dict[str, Any] = {
        "connected": False,
        "subscribed": False,
        "print": None,
        "error": None,
    }

    topic = (
        f"device/{device_id}/report"
    )

    def on_connect(
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_failed(
            reason_code
        ):
            state["error"] = (
                "MQTT authentication/"
                f"connection failed: "
                f"{reason_code}"
            )
            done.set()
            return

        state["connected"] = True

        result, _mid = client.subscribe(
            topic,
            qos=0,
        )

        if result != 0:
            state["error"] = (
                "MQTT subscribe failed: "
                f"{result}"
            )
            done.set()

    def on_subscribe(
        client: Any,
        userdata: Any,
        mid: Any,
        reason_codes: Any,
        properties: Any,
    ) -> None:
        state["subscribed"] = True

    def on_message(
        client: Any,
        userdata: Any,
        message: Any,
    ) -> None:
        try:
            obj = json.loads(
                message.payload.decode(
                    "utf-8"
                )
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return

        if not isinstance(obj, dict):
            return

        print_obj = obj.get("print")

        if not isinstance(
            print_obj,
            dict,
        ):
            return

        # Final preflight requires a fresh
        # status packet with AMS/tray state.
        if not has_ams_detail(
            print_obj
        ):
            return

        state["print"] = print_obj
        done.set()

    def on_disconnect(
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if (
            state["print"] is None
            and reason_failed(
                reason_code
            )
        ):
            state["error"] = (
                "MQTT disconnected before "
                "fresh status arrived: "
                f"{reason_code}"
            )
            done.set()

    client = mqtt.Client(
        callback_api_version=(
            mqtt.CallbackAPIVersion.VERSION2
        ),
        client_id=(
            "nl-am-r10-"
            + uuid.uuid4().hex[:12]
        ),
        protocol=mqtt.MQTTv311,
        reconnect_on_failure=False,
    )

    client.username_pw_set(
        "bblp",
        access_code,
    )

    client.tls_set(
        cert_reqs=ssl.CERT_NONE
    )

    client.tls_insecure_set(
        True
    )

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    started = time.monotonic()

    try:
        client.connect_async(
            ip_address,
            8883,
            keepalive=60,
        )

        client.loop_start()

        done.wait(
            timeout_seconds
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

    elapsed = round(
        time.monotonic()
        - started,
        3,
    )

    if state["error"] is None:
        if not state["connected"]:
            state["error"] = (
                "Timed out before MQTT "
                "connection completed."
            )

        elif not state["subscribed"]:
            state["error"] = (
                "Connected but subscription "
                "did not complete."
            )

        elif state["print"] is None:
            state["error"] = (
                "Connected and subscribed, "
                "but no fresh AMS status "
                "packet arrived."
            )

    return {
        "connected":
            bool(
                state["connected"]
            ),

        "subscribed":
            bool(
                state["subscribed"]
            ),

        "message_received":
            state["print"]
            is not None,

        "elapsed_seconds":
            elapsed,

        "error":
            state["error"],

        "print":
            state["print"],
    }


def find_current_tray(
    trays: list[dict[str, Any]],
    ams_id: Any,
    tray_id: Any,
) -> dict[str, Any] | None:
    expected = (
        str(ams_id),
        str(tray_id),
    )

    matches = [
        row
        for row in trays
        if (
            str(row.get("ams_id")),
            str(row.get("tray_id")),
        )
        == expected
    ]

    if len(matches) != 1:
        return None

    return matches[0]


def run_preflight(
    *,
    job_dir: Path,
    expected_device_id: str,
    access_code: str,
    timeout_seconds: float = 45.0,
    material_confirmed: bool = False,
) -> dict[str, Any]:
    job_dir = (
        Path(job_dir)
        .expanduser()
        .resolve()
    )

    r7b_path = (
        job_dir
        / "r7_ams_readonly_snapshot.json"
    )

    r7_path = (
        job_dir
        / "r7_ams_mapping.json"
    )

    r8_path = (
        job_dir
        / "r8_preprint_dry_run.json"
    )

    r9_path = (
        job_dir
        / "r9_upload_only_verified.json"
    )

    report_path = (
        job_dir
        / "r10_final_preflight.json"
    )

    r7b = load_json(
        r7b_path
    )

    r7 = load_json(
        r7_path
    )

    r8 = load_json(
        r8_path
    )

    r9 = load_json(
        r9_path
    )

    ip_address = str(
        r7b.get("printer_ip")
        or ""
    ).strip()

    device_id = str(
        r7b.get("device_id")
        or ""
    ).strip()

    if not ip_address:
        raise R10Error(
            "R7B printer IP is missing."
        )

    if device_id != expected_device_id:
        raise R10Error(
            "R7B DEVICE_ID mismatch."
        )

    gcode_info = (
        r8.get("gcode")
        or {}
    )

    local_path = Path(
        str(
            gcode_info.get("path")
            or ""
        )
    )

    if not local_path.is_file():
        raise R10Error(
            "R8 approved G-code is missing."
        )

    local_sha = sha256_file(
        local_path
    )

    expected_sha = str(
        gcode_info.get("sha256")
        or ""
    )

    r9_local_sha = str(
        r9.get("local_sha256")
        or ""
    )

    r9_remote_sha = str(
        r9.get("remote_sha256")
        or ""
    )

    r9_pass = (
        r9.get("r9_gate")
        == "PASS"
        or r9.get("status")
        == "upload_only_verified"
    )

    remote_integrity = (
        r9.get(
            "remote_size_verified"
        ) is True
        and r9.get(
            "remote_sha256_verified"
        ) is True
        and r9_remote_sha
        == expected_sha
    )

    fresh = capture_fresh_status(
        ip_address=ip_address,
        device_id=device_id,
        access_code=access_code,
        timeout_seconds=timeout_seconds,
    )

    print_obj = (
        fresh.get("print")
        if isinstance(
            fresh.get("print"),
            dict,
        )
        else {}
    )

    trays = extract_trays(
        print_obj
    )

    gcode_state = str(
        print_obj.get(
            "gcode_state"
        )
        or ""
    ).strip().upper()

    print_error = (
        print_obj.get(
            "print_error"
        )
    )

    printer_not_active = (
        bool(gcode_state)
        and gcode_state
        not in ACTIVE_STATES
    )

    print_error_clear = (
        print_error
        in (
            None,
            0,
            "0",
            "",
        )
    )

    mapping_rows = (
        r7.get("mapping")
        or []
    )

    current_mapping = []
    mapping_failures = []

    for row in mapping_rows:
        selected = row.get(
            "selected"
        )

        if not isinstance(
            selected,
            dict,
        ):
            mapping_failures.append(
                "missing_selected_mapping"
            )
            continue

        current = find_current_tray(
            trays,
            selected.get(
                "ams_id"
            ),
            selected.get(
                "tray_id"
            ),
        )

        if current is None:
            mapping_failures.append(
                "physical_tray_missing:"
                + str(
                    row.get(
                        "logical_index"
                    )
                )
            )

            current_mapping.append(
                {
                    "logical_index":
                        row.get(
                            "logical_index"
                        ),

                    "expected":
                        selected,

                    "current":
                        None,

                    "match":
                        False,
                }
            )

            continue

        expected_type = str(
            selected.get(
                "tray_type"
            )
            or ""
        ).upper()

        current_type = str(
            current.get(
                "tray_type"
            )
            or ""
        ).upper()

        expected_colour = (
            normalize_colour(
                selected.get(
                    "tray_color"
                )
            )
        )

        current_colour = (
            normalize_colour(
                current.get(
                    "tray_color"
                )
            )
        )

        match = (
            expected_type
            == current_type
            and expected_colour
            == current_colour
        )

        if not match:
            mapping_failures.append(
                "tray_material_changed:"
                + str(
                    row.get(
                        "logical_index"
                    )
                )
            )

        current_mapping.append(
            {
                "logical_index":
                    row.get(
                        "logical_index"
                    ),

                "logical_filament":
                    row.get(
                        "logical_filament"
                    ),

                "expected":
                    selected,

                "current":
                    current,

                "match":
                    match,
            }
        )

    slice_filaments = (
        r8.get(
            "slice_filaments"
        )
        or []
    )

    material_requirements = []

    for index, row in enumerate(
        slice_filaments
    ):
        material_requirements.append(
            {
                "logical_index":
                    index,

                "type":
                    row.get("type"),

                "colour":
                    row.get("color"),

                "required_g":
                    row.get("used_g"),

                "required_m":
                    row.get("used_m"),
            }
        )

    checks = {
        "r7_mapping_pass":
            r7.get("status")
            == "ams_mapping_pass",

        "r8_preprint_pass":
            r8.get("status")
            == "preprint_dry_run_pass",

        "r9_upload_pass":
            r9_pass,

        "r9_remote_integrity":
            remote_integrity,

        "local_gcode_sha_unchanged":
            (
                local_sha
                == expected_sha
                == r9_local_sha
            ),

        "fresh_mqtt_connected":
            fresh.get(
                "connected"
            ) is True,

        "fresh_mqtt_subscribed":
            fresh.get(
                "subscribed"
            ) is True,

        "fresh_status_received":
            fresh.get(
                "message_received"
            ) is True,

        "printer_not_active":
            printer_not_active,

        "print_error_clear":
            print_error_clear,

        "ams_mapping_still_matches":
            (
                len(mapping_failures)
                == 0
                and len(
                    current_mapping
                )
                == len(
                    mapping_rows
                )
            ),
    }

    failed_checks = [
        name
        for name, passed
        in checks.items()
        if not passed
    ]

    automatic_gate = (
        len(failed_checks)
        == 0
    )

    final_gate = (
        automatic_gate
        and material_confirmed
    )

    if final_gate:
        status = (
            "final_preflight_pass"
        )

    elif automatic_gate:
        status = (
            "final_preflight_pending_"
            "material_confirmation"
        )

    else:
        status = (
            "final_preflight_fail"
        )

    payload = {
        "schema_version":
            "r10-final-preflight-v1",

        "status":
            status,

        "automatic_gate_pass":
            automatic_gate,

        "material_sufficient_"
        "manually_confirmed":
            material_confirmed,

        "final_gate_pass":
            final_gate,

        "printer": {
            "ip":
                ip_address,

            "device_id":
                device_id,

            "gcode_state":
                gcode_state,

            "print_error":
                print_error,

            "stg_cur":
                print_obj.get(
                    "stg_cur"
                ),

            "bed_temper":
                print_obj.get(
                    "bed_temper"
                ),

            "bed_target_temper":
                print_obj.get(
                    "bed_target_temper"
                ),

            "nozzle_temper":
                print_obj.get(
                    "nozzle_temper"
                ),

            "nozzle_target_temper":
                print_obj.get(
                    "nozzle_target_temper"
                ),

            "hms":
                print_obj.get(
                    "hms"
                ),
        },

        "fresh_status": {
            "connected":
                fresh.get(
                    "connected"
                ),

            "subscribed":
                fresh.get(
                    "subscribed"
                ),

            "message_received":
                fresh.get(
                    "message_received"
                ),

            "elapsed_seconds":
                fresh.get(
                    "elapsed_seconds"
                ),

            "error":
                fresh.get(
                    "error"
                ),
        },

        "current_trays":
            trays,

        "current_mapping":
            current_mapping,

        "mapping_failures":
            mapping_failures,

        "material_requirements":
            material_requirements,

        "checks":
            checks,

        "failed_checks":
            failed_checks,

        "artifact": {
            "local_path":
                str(local_path),

            "local_sha256":
                local_sha,

            "r9_remote_path":
                r9.get(
                    "remote_path"
                ),

            "r9_remote_sha256":
                r9_remote_sha,
        },

        "safety": {
            "network_used":
                True,

            "mqtt_subscribe_count":
                1,

            "mqtt_publish_count":
                0,

            "artifact_uploaded":
                False,

            "printer_command_sent":
                False,

            "print_started":
                False,

            "heating_command_sent":
                False,

            "motion_command_sent":
                False,

            "access_code_stored":
                False,
        },

        "next_gate":
            (
                "R11_REAL_START"
                if final_gate
                else None
            ),
    }

    report_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    payload["report_file"] = str(
        report_path
    )

    return payload


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--job-dir",
        required=True,
    )

    parser.add_argument(
        "--expected-device-id",
        required=True,
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
    )

    parser.add_argument(
        "--confirm-material-sufficient",
        action="store_true",
    )

    args = parser.parse_args()

    print(
        "=== R10 FINAL PREFLIGHT ==="
    )

    print(
        "Policy: MQTT subscribe-only."
    )

    print(
        "No upload, no printer command, "
        "no print start."
    )

    print()

    code = getpass.getpass(
        "X1C Access Code "
        "(hidden, never stored): "
    ).strip()

    if not code:
        raise R10Error(
            "Access Code is empty."
        )

    try:
        result = run_preflight(
            job_dir=Path(
                args.job_dir
            ),
            expected_device_id=(
                args.expected_device_id
            ),
            access_code=code,
            timeout_seconds=(
                args.timeout
            ),
            material_confirmed=(
                args.confirm_material_sufficient
            ),
        )

    finally:
        code = None

    print()
    print(
        "=== CURRENT PRINTER ==="
    )

    printer = result[
        "printer"
    ]

    print(
        "GCODE_STATE=",
        printer.get(
            "gcode_state"
        ),
    )

    print(
        "PRINT_ERROR=",
        printer.get(
            "print_error"
        ),
    )

    print(
        "BED=",
        printer.get(
            "bed_temper"
        ),
        "/ target",
        printer.get(
            "bed_target_temper"
        ),
    )

    print(
        "NOZZLE=",
        printer.get(
            "nozzle_temper"
        ),
        "/ target",
        printer.get(
            "nozzle_target_temper"
        ),
    )

    print()
    print(
        "=== CURRENT AMS MAPPING ==="
    )

    for row in result[
        "current_mapping"
    ]:
        current = row.get(
            "current"
        ) or {}

        print(
            "LOGICAL_"
            + str(
                row.get(
                    "logical_index"
                )
            ),
            "? AMS",
            current.get(
                "ams_id"
            ),
            "/ TRAY",
            current.get(
                "tray_id"
            ),
            "/",
            current.get(
                "tray_type"
            ),
            "/",
            current.get(
                "tray_color"
            ),
            "/ REMAIN=",
            current.get(
                "remain"
            ),
            "/ MATCH=",
            row.get(
                "match"
            ),
        )

    print()
    print(
        "=== MATERIAL REQUIRED ==="
    )

    for row in result[
        "material_requirements"
    ]:
        print(
            "LOGICAL_"
            + str(
                row.get(
                    "logical_index"
                )
            ),
            "REQUIRED_G=",
            row.get(
                "required_g"
            ),
            "REQUIRED_M=",
            row.get(
                "required_m"
            ),
        )

    print()
    print(
        "=== R10 CHECKS ==="
    )

    for name, passed in result[
        "checks"
    ].items():
        print(
            name.upper(),
            "=",
            passed,
        )

    print(
        "FAILED_CHECKS=",
        result[
            "failed_checks"
        ],
    )

    print()
    print(
        "MQTT_PUBLISH_COUNT=0"
    )

    print(
        "ARTIFACT_UPLOADED=False"
    )

    print(
        "PRINTER_COMMAND_SENT=False"
    )

    print(
        "PRINT_STARTED=False"
    )

    print(
        "AUTOMATIC_GATE_PASS=",
        result[
            "automatic_gate_pass"
        ],
    )

    print(
        "MATERIAL_CONFIRMED=",
        result[
            "material_sufficient_"
            "manually_confirmed"
        ],
    )

    print(
        "R10_FINAL_GATE=",
        (
            "PASS"
            if result[
                "final_gate_pass"
            ]
            else (
                "PENDING_MATERIAL_CONFIRMATION"
                if result[
                    "automatic_gate_pass"
                ]
                else "FAIL"
            )
        ),
    )

    print(
        "R10_REPORT=",
        result[
            "report_file"
        ],
    )

    # Pending manual material confirmation
    # is not an execution failure.
    return (
        0
        if result[
            "automatic_gate_pass"
        ]
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
