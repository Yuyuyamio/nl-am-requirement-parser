from __future__ import annotations

import argparse
import getpass
import json
import ssl
import threading
import time
import uuid

from pathlib import Path
from typing import Any


class FinalStartPreflightError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(obj, dict):
        raise FinalStartPreflightError(
            f"Expected JSON object: {path}"
        )

    return obj


def parse_mapping(text: str) -> list[int]:
    result = [
        int(item.strip())
        for item in text.split(",")
        if item.strip()
    ]

    if not result:
        raise FinalStartPreflightError(
            "Expected mapping is empty."
        )

    return result


def acquire_status(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
) -> tuple[dict[str, Any], int]:

    import paho.mqtt.client as mqtt

    connected = threading.Event()
    subscribed = threading.Event()
    status_seen = threading.Event()

    state = {
        "error": None,
        "print": None,
    }

    def failed(reason: Any) -> bool:
        marker = getattr(
            reason,
            "is_failure",
            None,
        )

        if marker is not None:
            return bool(marker)

        try:
            return int(reason) != 0
        except Exception:
            return str(reason).lower() not in {
                "0",
                "success",
            }

    def on_connect(
        client,
        userdata,
        flags,
        reason_code,
        properties=None,
    ):
        if failed(reason_code):
            state["error"] = (
                "MQTT connect rejected: "
                + str(reason_code)
            )
            connected.set()
            return

        connected.set()

        client.subscribe(
            f"device/{device_id}/report",
            qos=0,
        )

    def on_subscribe(
        client,
        userdata,
        mid,
        reason_codes,
        properties=None,
    ):
        subscribed.set()

    def on_message(
        client,
        userdata,
        msg,
    ):
        try:
            data = json.loads(
                msg.payload.decode(
                    "utf-8",
                    errors="strict",
                )
            )
        except Exception:
            return

        if not isinstance(data, dict):
            return

        pobj = data.get("print")

        if not isinstance(pobj, dict):
            return

        state["print"] = pobj
        status_seen.set()

    client = mqtt.Client(
        callback_api_version=
            mqtt.CallbackAPIVersion.VERSION2,
        client_id=(
            "nl-am-final-preflight-"
            + uuid.uuid4().hex[:10]
        ),
        protocol=mqtt.MQTTv311,
    )

    client.username_pw_set(
        "bblp",
        access_code,
    )

    client.tls_set(
        cert_reqs=ssl.CERT_NONE,
    )

    client.tls_insecure_set(True)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    pushall_count = 0

    try:
        client.connect_async(
            ip_address,
            8883,
            keepalive=60,
        )

        client.loop_start()

        if not connected.wait(20):
            raise FinalStartPreflightError(
                "MQTT connection timeout."
            )

        if state["error"]:
            raise FinalStartPreflightError(
                str(state["error"])
            )

        if not subscribed.wait(10):
            raise FinalStartPreflightError(
                "MQTT subscription timeout."
            )

        if not status_seen.wait(5):
            payload = {
                "pushing": {
                    "sequence_id":
                        str(
                            int(
                                time.time()
                                * 1000
                            )
                        ),
                    "command": "pushall",
                    "version": 1,
                    "push_target": 1,
                }
            }

            info = client.publish(
                f"device/{device_id}/request",
                json.dumps(
                    payload,
                    separators=(",", ":"),
                ),
                qos=1,
                retain=False,
            )

            pushall_count = 1

            info.wait_for_publish(
                timeout=8
            )

            if not info.is_published():
                raise FinalStartPreflightError(
                    "Read-only pushall "
                    "was not published."
                )

            if not status_seen.wait(20):
                raise FinalStartPreflightError(
                    "No printer status received."
                )

        pobj = state["print"]

        if not isinstance(pobj, dict):
            raise FinalStartPreflightError(
                "Printer status object missing."
            )

        return pobj, pushall_count

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
        "--ip",
        required=True,
    )

    parser.add_argument(
        "--device-id",
        required=True,
    )

    parser.add_argument(
        "--upload-report",
        required=True,
    )

    parser.add_argument(
        "--ams-report",
        required=True,
    )

    parser.add_argument(
        "--expected-mapping",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args()

    upload_path = Path(
        args.upload_report
    ).resolve()

    ams_path = Path(
        args.ams_report
    ).resolve()

    report_path = Path(
        args.report
    ).resolve()

    upload = load_json(upload_path)
    ams = load_json(ams_path)

    expected_mapping = parse_mapping(
        args.expected_mapping
    )

    if upload.get("status") != (
        "x1c_upload_only_verified"
    ):
        raise FinalStartPreflightError(
            "UPLOAD ONLY evidence "
            "is not PASS."
        )

    verification = upload.get(
        "verification",
        {},
    )

    if verification.get(
        "size_match"
    ) is not True:
        raise FinalStartPreflightError(
            "Remote SIZE evidence "
            "is not PASS."
        )

    if verification.get(
        "sha256_match"
    ) is not True:
        raise FinalStartPreflightError(
            "Remote SHA256 evidence "
            "is not PASS."
        )

    if ams.get("status") != (
        "ams_mapping_resolved"
    ):
        raise FinalStartPreflightError(
            "Live AMS mapping "
            "is not resolved."
        )

    resolution = ams.get(
        "resolution",
        {},
    )

    live_mapping = resolution.get(
        "ams_mapping_logical"
    )

    if live_mapping != expected_mapping:
        raise FinalStartPreflightError(
            "AMS mapping changed. "
            f"expected={expected_mapping} "
            f"live={live_mapping}"
        )

    blockers = resolution.get(
        "blockers",
        [],
    )

    if blockers:
        raise FinalStartPreflightError(
            f"AMS blockers present: {blockers}"
        )

    access_code = getpass.getpass(
        "X1C Access Code "
        "(hidden, never stored): "
    ).strip()

    if not access_code:
        raise FinalStartPreflightError(
            "Access Code is empty."
        )

    print_obj, pushall_count = (
        acquire_status(
            ip_address=args.ip,
            device_id=args.device_id,
            access_code=access_code,
        )
    )

    from am_print_executor import (
        developer_mode_backend_v1120
        as backend
    )

    idle = backend._strict_idle_check(
        print_obj
    )

    if not idle.get("passed"):
        raise FinalStartPreflightError(
            "Printer is not in a safe "
            "idle/terminal state: "
            + json.dumps(
                idle,
                ensure_ascii=False,
            )
        )

    result = {
        "schema_version": "1.0.0",
        "module": "M4",
        "stage":
            "x1c_final_start_preflight",

        "status":
            "x1c_final_start_preflight_pass",

        "device": {
            "device_id": args.device_id,
            "ip_address": args.ip,
        },

        "remote_artifact": {
            "path":
                upload["remote"]["path"],

            "size_match":
                verification[
                    "size_match"
                ],

            "sha256_match":
                verification[
                    "sha256_match"
                ],
        },

        "ams": {
            "mapping":
                live_mapping,

            "blockers":
                blockers,
        },

        "printer": {
            "gcode_state":
                print_obj.get(
                    "gcode_state"
                ),

            "print_error":
                print_obj.get(
                    "print_error"
                ),

            "idle_check":
                idle,
        },

        "policy": {
            "read_only_pushall_count":
                pushall_count,

            "project_file_publish_count":
                0,

            "printer_control_command_count":
                0,

            "print_started":
                False,

            "access_code_stored":
                False,
        },
    }

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_path.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        "FINAL_PRINTER_STATE="
        + str(
            print_obj.get(
                "gcode_state"
            )
        )
    )

    print(
        "FINAL_AMS_MAPPING="
        + ",".join(
            str(x)
            for x in live_mapping
        )
    )

    print(
        "REMOTE_SIZE_MATCH=True"
    )

    print(
        "REMOTE_SHA256_MATCH=True"
    )

    print(
        "M4_X1C_FINAL_START_PREFLIGHT=PASS"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())

    except Exception as exc:
        print()
        print(
            "M4_X1C_FINAL_START_PREFLIGHT=FAIL"
        )
        print(
            f"ERROR={type(exc).__name__}: {exc}"
        )
        raise SystemExit(2)
