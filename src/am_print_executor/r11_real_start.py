from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import json
import ssl
import threading
import time
import uuid

from pathlib import Path
from typing import Any


class R11StartError(RuntimeError):
    pass


START_STATES = {
    "PREPARE",
    "RUNNING",
    "PRINTING",
}


FAIL_STATES = {
    "FAILED",
}


def load_json(
    path: Path,
) -> dict[str, Any]:

    obj = json.loads(
        Path(path).read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(obj, dict):
        raise R11StartError(
            f"Expected JSON object: {path}"
        )

    return obj


def sha256_file(
    path: Path,
) -> str:

    digest = hashlib.sha256()

    with Path(path).open("rb") as handle:

        for block in iter(
            lambda:
                handle.read(
                    1024 * 1024
                ),
            b"",
        ):

            digest.update(
                block
            )

    return digest.hexdigest()


def recursive_values(
    value: Any,
    key_name: str,
) -> list[Any]:

    rows = []

    if isinstance(
        value,
        dict,
    ):

        for key, child in value.items():

            if str(key) == key_name:
                rows.append(child)

            rows.extend(
                recursive_values(
                    child,
                    key_name,
                )
            )

    elif isinstance(
        value,
        list,
    ):

        for child in value:

            rows.extend(
                recursive_values(
                    child,
                    key_name,
                )
            )

    return rows


def normalize_sequence_ids(
    value: Any,
) -> Any:

    if isinstance(
        value,
        dict,
    ):

        result = {}

        for key, child in value.items():

            if str(key) == "sequence_id":
                result[key] = "<SEQUENCE_ID>"

            else:
                result[key] = (
                    normalize_sequence_ids(
                        child
                    )
                )

        return result

    if isinstance(
        value,
        list,
    ):

        return [
            normalize_sequence_ids(
                child
            )
            for child in value
        ]

    return value


def replace_sequence_ids(
    value: Any,
    sequence_id: str,
) -> tuple[Any, int]:

    clone = copy.deepcopy(
        value
    )

    count = 0

    def walk(
        obj: Any,
    ) -> None:

        nonlocal count

        if isinstance(
            obj,
            dict,
        ):

            for key in list(
                obj.keys()
            ):

                if str(key) == "sequence_id":

                    obj[key] = (
                        sequence_id
                    )

                    count += 1

                else:

                    walk(
                        obj[key]
                    )

        elif isinstance(
            obj,
            list,
        ):

            for child in obj:

                walk(
                    child
                )

    walk(
        clone
    )

    return (
        clone,
        count,
    )


def mqtt_reason_failed(
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


def target_is_zero(
    value: Any,
) -> bool:

    if value in (
        None,
        "",
    ):
        return True

    try:
        return abs(
            float(value)
        ) <= 1.0

    except Exception:
        return False


def load_contract(
    job_dir: Path,
) -> dict[str, Any]:

    job_dir = (
        Path(job_dir)
        .expanduser()
        .resolve()
    )

    r7 = load_json(
        job_dir
        / "r7_ams_mapping.json"
    )

    r8 = load_json(
        job_dir
        / "r8_preprint_dry_run.json"
    )

    r9 = load_json(
        job_dir
        / "r9_upload_only_verified.json"
    )

    r10 = load_json(
        job_dir
        / "r10_final_preflight.json"
    )

    r11a = load_json(
        job_dir
        / "r11a_start_contract_audit.json"
    )

    if (
        r11a.get("status")
        != "start_contract_pass"
    ):
        raise R11StartError(
            "R11A start contract did not pass."
        )

    if (
        r10.get("status")
        != "final_preflight_pass"
        or r10.get(
            "final_gate_pass"
        ) is not True
        or r10.get(
            "material_sufficient_"
            "manually_confirmed"
        ) is not True
    ):
        raise R11StartError(
            "R10 final preflight is not PASS."
        )

    if (
        r9.get("status")
        != "upload_only_verified"
        or r9.get(
            "remote_size_verified"
        ) is not True
        or r9.get(
            "remote_sha256_verified"
        ) is not True
    ):
        raise R11StartError(
            "R9 upload evidence is not PASS."
        )

    artifact = (
        r11a.get("artifact")
        or {}
    )

    local_path = Path(
        str(
            artifact.get(
                "local_path"
            )
            or ""
        )
    )

    if not local_path.is_file():

        raise R11StartError(
            "R11A local print artifact "
            "is missing."
        )

    current_sha = (
        sha256_file(
            local_path
        )
    )

    r11a_sha = str(
        artifact.get(
            "sha256"
        )
        or ""
    )

    r9_remote_sha = str(
        r9.get(
            "remote_sha256"
        )
        or ""
    )

    if not (
        current_sha
        == r11a_sha
        == r9_remote_sha
    ):

        raise R11StartError(
            "Local/R11A/R9 SHA256 chain "
            "does not match."
        )

    payload = (
        r11a.get("payload")
    )

    if not isinstance(
        payload,
        dict,
    ):

        raise R11StartError(
            "R11A payload is missing."
        )

    commands = [
        str(value)
        for value in recursive_values(
            payload,
            "command",
        )
    ]

    if (
        commands.count(
            "project_file"
        ) != 1
        or any(
            value != "project_file"
            for value in commands
        )
    ):

        raise R11StartError(
            "R11A payload is not a single "
            "project_file command."
        )

    sequence_values = (
        recursive_values(
            payload,
            "sequence_id",
        )
    )

    if len(sequence_values) < 1:

        raise R11StartError(
            "R11A payload contains no "
            "sequence_id."
        )

    printer = (
        r10.get("printer")
        or {}
    )

    ip_address = str(
        printer.get("ip")
        or r9.get("printer_ip")
        or ""
    ).strip()

    device_id = str(
        printer.get("device_id")
        or r9.get("device_id")
        or ""
    ).strip()

    if not ip_address:
        raise R11StartError(
            "Printer IP missing."
        )

    if not device_id:
        raise R11StartError(
            "Printer DEVICE_ID missing."
        )

    mapping_rows = (
        r7.get("mapping")
        or []
    )

    return {
        "job_dir":
            job_dir,

        "r7":
            r7,

        "r8":
            r8,

        "r9":
            r9,

        "r10":
            r10,

        "r11a":
            r11a,

        "local_path":
            local_path,

        "sha256":
            current_sha,

        "remote_path":
            artifact.get(
                "remote_path"
            ),

        "gcode_entry":
            artifact.get(
                "gcode_entry"
            ),

        "ams_mapping":
            r11a.get(
                "ams_mapping"
            ),

        "mapping_rows":
            mapping_rows,

        "audited_payload":
            payload,

        "ip_address":
            ip_address,

        "device_id":
            device_id,
    }


def check_live_mapping(
    print_obj: dict[str, Any],
    mapping_rows: list[dict[str, Any]],
) -> tuple[
    bool,
    list[dict[str, Any]],
]:

    from am_print_executor.r10_final_preflight import (
        extract_trays,
        find_current_tray,
        normalize_colour,
    )

    trays = extract_trays(
        print_obj
    )

    result = []

    all_match = True

    for row in mapping_rows:

        selected = (
            row.get("selected")
            or {}
        )

        current = find_current_tray(
            trays,
            selected.get("ams_id"),
            selected.get("tray_id"),
        )

        match = False

        if current is not None:

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
            all_match = False

        result.append(
            {
                "logical_index":
                    row.get(
                        "logical_index"
                    ),

                "expected":
                    selected,

                "current":
                    current,

                "match":
                    match,
            }
        )

    return (
        all_match,
        result,
    )


def start_once(
    *,
    contract: dict[str, Any],
    access_code: str,
    start_timeout: float = 90.0,
) -> dict[str, Any]:

    try:
        import paho.mqtt.client as mqtt

    except Exception as exc:

        raise R11StartError(
            "paho-mqtt unavailable."
        ) from exc

    ip_address = (
        contract[
            "ip_address"
        ]
    )

    device_id = (
        contract[
            "device_id"
        ]
    )

    request_topic = (
        f"device/{device_id}/request"
    )

    report_topic = (
        f"device/{device_id}/report"
    )

    connected = (
        threading.Event()
    )

    subscribed = (
        threading.Event()
    )

    initial_ready = (
        threading.Event()
    )

    started = (
        threading.Event()
    )

    failed = (
        threading.Event()
    )

    state: dict[str, Any] = {
        "error":
            None,

        "latest_print":
            None,

        "initial_print":
            None,

        "events":
            [],

        "publish_count":
            0,

        "publish_rc":
            None,

        "sequence_id":
            None,
    }

    def record(
        print_obj: dict[str, Any],
    ) -> None:

        event = {
            "timestamp_unix":
                time.time(),

            "gcode_state":
                print_obj.get(
                    "gcode_state"
                ),

            "stg_cur":
                print_obj.get(
                    "stg_cur"
                ),

            "print_error":
                print_obj.get(
                    "print_error"
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

            "mc_percent":
                print_obj.get(
                    "mc_percent"
                ),

            "mc_remaining_time":
                print_obj.get(
                    "mc_remaining_time"
                ),
        }

        if (
            not state["events"]
            or state["events"][-1]
            != event
        ):
            state[
                "events"
            ].append(
                event
            )

    def on_connect(
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:

        if mqtt_reason_failed(
            reason_code
        ):

            state["error"] = (
                "MQTT connect failed: "
                f"{reason_code}"
            )

            failed.set()
            return

        connected.set()

        rc, _mid = (
            client.subscribe(
                report_topic,
                qos=0,
            )
        )

        if rc != 0:

            state["error"] = (
                "MQTT subscribe failed "
                f"locally rc={rc}"
            )

            failed.set()

    def on_subscribe(
        client: Any,
        userdata: Any,
        mid: Any,
        reason_codes: Any,
        properties: Any,
    ) -> None:

        subscribed.set()

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

        except Exception:
            return

        if not isinstance(
            obj,
            dict,
        ):
            return

        print_obj = (
            obj.get("print")
        )

        if not isinstance(
            print_obj,
            dict,
        ):
            return

        state[
            "latest_print"
        ] = print_obj

        record(
            print_obj
        )

        # Need AMS detail before final start.
        from am_print_executor.r10_final_preflight import (
            has_ams_detail,
        )

        if (
            state["initial_print"]
            is None
            and has_ams_detail(
                print_obj
            )
        ):

            state[
                "initial_print"
            ] = print_obj

            initial_ready.set()

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

        if (
            gcode_state
            in START_STATES
        ):

            started.set()

        if (
            gcode_state
            in FAIL_STATES
        ):

            state["error"] = (
                "Printer entered FAILED "
                "after real-start command."
            )

            failed.set()

        if print_error not in (
            None,
            "",
            0,
            "0",
        ):

            state["error"] = (
                "Printer reported print_error="
                + repr(
                    print_error
                )
            )

            failed.set()

    client = mqtt.Client(
        callback_api_version=(
            mqtt.CallbackAPIVersion.VERSION2
        ),
        client_id=(
            "nl-am-r11-"
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

    client.on_connect = (
        on_connect
    )

    client.on_subscribe = (
        on_subscribe
    )

    client.on_message = (
        on_message
    )

    try:

        client.connect_async(
            ip_address,
            8883,
            keepalive=60,
        )

        client.loop_start()

        if not connected.wait(
            20
        ):

            raise R11StartError(
                state["error"]
                or "MQTT connection timeout."
            )

        if not subscribed.wait(
            10
        ):

            raise R11StartError(
                "MQTT subscription timeout."
            )

        if not initial_ready.wait(
            45
        ):

            raise R11StartError(
                "No fresh pre-start printer/"
                "AMS state received."
            )

        initial = (
            state[
                "initial_print"
            ]
        )

        gcode_state = str(
            initial.get(
                "gcode_state"
            )
            or ""
        ).strip().upper()

        if gcode_state != "IDLE":

            raise R11StartError(
                "R11 blocked: printer is "
                f"{gcode_state!r}, not IDLE."
            )

        if initial.get(
            "print_error"
        ) not in (
            None,
            "",
            0,
            "0",
        ):

            raise R11StartError(
                "R11 blocked: print_error "
                "is not clear."
            )

        if not target_is_zero(
            initial.get(
                "bed_target_temper"
            )
        ):

            raise R11StartError(
                "R11 blocked: bed already "
                "has an active target."
            )

        if not target_is_zero(
            initial.get(
                "nozzle_target_temper"
            )
        ):

            raise R11StartError(
                "R11 blocked: nozzle already "
                "has an active target."
            )

        mapping_ok, live_mapping = (
            check_live_mapping(
                initial,
                contract[
                    "mapping_rows"
                ],
            )
        )

        if not mapping_ok:

            raise R11StartError(
                "R11 blocked: physical AMS "
                "mapping changed after R10."
            )

        print()
        print(
            "=== FINAL LIVE START CHECK ==="
        )

        print(
            "GCODE_STATE=",
            gcode_state,
        )

        print(
            "PRINT_ERROR=",
            initial.get(
                "print_error"
            ),
        )

        print(
            "BED_TARGET=",
            initial.get(
                "bed_target_temper"
            ),
        )

        print(
            "NOZZLE_TARGET=",
            initial.get(
                "nozzle_target_temper"
            ),
        )

        for row in live_mapping:

            current = (
                row.get("current")
                or {}
            )

            print(
                "LOGICAL_"
                + str(
                    row.get(
                        "logical_index"
                    )
                ),
                "-> AMS",
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
                "/ MATCH=",
                row.get(
                    "match"
                ),
            )

        print()
        print(
            "REMOTE_FILE=",
            contract[
                "remote_path"
            ],
        )

        print(
            "GCODE_ENTRY=",
            contract[
                "gcode_entry"
            ],
        )

        print(
            "AMS_MAPPING=",
            contract[
                "ams_mapping"
            ],
        )

        print(
            "SHA256=",
            contract[
                "sha256"
            ],
        )

        print()
        print(
            "This next action will START "
            "the physical X1C print."
        )

        print(
            "Type exactly START to send "
            "one project_file command."
        )

        confirmation = input(
            "Final confirmation: "
        ).strip()

        if confirmation != "START":

            raise R11StartError(
                "Real start cancelled."
            )

        sequence_id = str(
            int(
                time.time()
                * 1000
            )
        )

        runtime_payload, sequence_count = (
            replace_sequence_ids(
                contract[
                    "audited_payload"
                ],
                sequence_id,
            )
        )

        if sequence_count < 1:

            raise R11StartError(
                "No sequence_id replaced."
            )

        if (
            normalize_sequence_ids(
                runtime_payload
            )
            != normalize_sequence_ids(
                contract[
                    "audited_payload"
                ]
            )
        ):

            raise R11StartError(
                "Runtime payload differs "
                "from R11A beyond "
                "sequence_id."
            )

        state[
            "sequence_id"
        ] = sequence_id

        wire = json.dumps(
            runtime_payload,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        print()
        print(
            "=== SENDING ONE REAL START COMMAND ==="
        )

        info = client.publish(
            request_topic,
            wire,
            qos=0,
            retain=False,
        )

        state[
            "publish_count"
        ] += 1

        state[
            "publish_rc"
        ] = info.rc

        if info.rc != (
            mqtt.MQTT_ERR_SUCCESS
        ):

            raise R11StartError(
                "MQTT publish failed "
                f"locally rc={info.rc}"
            )

        info.wait_for_publish(
            timeout=8
        )

        if not info.is_published():

            raise R11StartError(
                "MQTT start command was "
                "not published."
            )

        deadline = (
            time.monotonic()
            + start_timeout
        )

        while (
            time.monotonic()
            < deadline
        ):

            if started.is_set():
                break

            if failed.is_set():
                break

            time.sleep(
                0.25
            )

        if not started.is_set():

            raise R11StartError(
                state["error"]
                or (
                    "Start command was sent "
                    "once, but PREPARE/RUNNING "
                    "was not observed. "
                    "No automatic retry was made."
                )
            )

        # Observe a few more seconds read-only.
        time.sleep(
            5
        )

        latest = (
            state[
                "latest_print"
            ]
            or {}
        )

        return {
            "status":
                "real_start_observed",

            "device_id":
                device_id,

            "printer_ip":
                ip_address,

            "remote_path":
                contract[
                    "remote_path"
                ],

            "gcode_entry":
                contract[
                    "gcode_entry"
                ],

            "artifact_sha256":
                contract[
                    "sha256"
                ],

            "ams_mapping":
                contract[
                    "ams_mapping"
                ],

            "sequence_id":
                state[
                    "sequence_id"
                ],

            "mqtt_publish_count":
                state[
                    "publish_count"
                ],

            "publish_rc":
                state[
                    "publish_rc"
                ],

            "start_observed":
                True,

            "latest_gcode_state":
                latest.get(
                    "gcode_state"
                ),

            "latest_stage":
                latest.get(
                    "stg_cur"
                ),

            "latest_print_error":
                latest.get(
                    "print_error"
                ),

            "events":
                state[
                    "events"
                ],

            "safety": {
                "artifact_uploaded":
                    False,

                "mqtt_publish_count":
                    state[
                        "publish_count"
                    ],

                "automatic_retry_count":
                    0,

                "start_command_count":
                    1,

                "access_code_stored":
                    False,
            },
        }

    finally:

        try:
            client.loop_stop()
        except Exception:
            pass

        try:
            client.disconnect()
        except Exception:
            pass


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--job-dir",
        required=True,
    )

    parser.add_argument(
        "--start-timeout",
        type=float,
        default=90.0,
    )

    args = parser.parse_args()

    job_dir = (
        Path(
            args.job_dir
        )
        .expanduser()
        .resolve()
    )

    report_path = (
        job_dir
        / "r11_real_start.json"
    )

    print(
        "=== R11B REAL START ==="
    )

    print(
        "R11A payload contract: required."
    )

    print(
        "R10 final preflight: required."
    )

    print(
        "Automatic retry: DISABLED."
    )

    print(
        "Maximum start publish count: 1."
    )

    contract = load_contract(
        job_dir
    )

    print()
    print(
        "CONTRACT_SHA256=",
        contract["sha256"],
    )

    print(
        "REMOTE_PATH=",
        contract["remote_path"],
    )

    print(
        "AMS_MAPPING=",
        contract["ams_mapping"],
    )

    print()
    print(
        "Enter X1C Access Code."
    )

    print(
        "Hidden input; never stored."
    )

    access_code = (
        getpass.getpass(
            "X1C Access Code: "
        ).strip()
    )

    if not access_code:

        raise R11StartError(
            "Access Code is empty."
        )

    try:

        result = start_once(
            contract=contract,
            access_code=access_code,
            start_timeout=(
                args.start_timeout
            ),
        )

    finally:

        access_code = None

    report = {
        "schema_version":
            "r11-real-start-v1",

        **result,
    }

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        "=== R11 RESULT ==="
    )

    print(
        "MQTT_PUBLISH_COUNT=",
        result[
            "mqtt_publish_count"
        ],
    )

    print(
        "AUTOMATIC_RETRY_COUNT=0"
    )

    print(
        "START_COMMAND_COUNT=1"
    )

    print(
        "START_OBSERVED=",
        result[
            "start_observed"
        ],
    )

    print(
        "LATEST_GCODE_STATE=",
        result[
            "latest_gcode_state"
        ],
    )

    print(
        "LATEST_STAGE=",
        result[
            "latest_stage"
        ],
    )

    print(
        "LATEST_PRINT_ERROR=",
        result[
            "latest_print_error"
        ],
    )

    print(
        "ARTIFACT_UPLOADED=False"
    )

    print(
        "ACCESS_CODE_STORED=False"
    )

    print(
        "R11_REPORT=",
        report_path,
    )

    print(
        "R11_REAL_START_GATE=PASS"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
