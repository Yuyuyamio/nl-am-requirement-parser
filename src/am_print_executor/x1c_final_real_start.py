from __future__ import annotations

import argparse
import getpass
import json
import ssl
import threading
import time
import uuid

from pathlib import Path


class FinalStartError(RuntimeError):
    pass


def load_json(path):
    obj = json.loads(
        Path(path).read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(obj, dict):
        raise FinalStartError(
            f"Expected JSON object: {path}"
        )

    return obj


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--upload-report",
        required=True,
    )

    parser.add_argument(
        "--preflight-report",
        required=True,
    )

    parser.add_argument(
        "--ip",
        required=True,
    )

    parser.add_argument(
        "--device-id",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    parser.add_argument(
        "--confirm-start",
        required=True,
    )

    args = parser.parse_args()

    if args.confirm_start != "START":
        raise FinalStartError(
            "Real print start was not explicitly armed."
        )

    upload = load_json(
        args.upload_report
    )

    preflight = load_json(
        args.preflight_report
    )

    if upload.get("status") != (
        "x1c_upload_only_verified"
    ):
        raise FinalStartError(
            "UPLOAD ONLY evidence is not PASS."
        )

    verification = upload.get(
        "verification",
        {},
    )

    if verification.get(
        "size_match"
    ) is not True:
        raise FinalStartError(
            "Remote SIZE check is not PASS."
        )

    if verification.get(
        "sha256_match"
    ) is not True:
        raise FinalStartError(
            "Remote SHA256 check is not PASS."
        )

    if preflight.get("status") != (
        "x1c_final_start_preflight_pass"
    ):
        raise FinalStartError(
            "Final start preflight is not PASS."
        )

    mapping = (
        preflight
        .get("ams", {})
        .get("mapping")
    )

    if mapping != [0, 3]:
        raise FinalStartError(
            "Expected live mapping [0,3], "
            f"observed {mapping!r}."
        )

    remote = upload.get(
        "remote",
        {}
    )

    remote_path = remote.get(
        "path"
    )

    entries = (
        upload
        .get("artifact", {})
        .get("gcode_entries", [])
    )

    if not remote_path:
        raise FinalStartError(
            "Remote path missing."
        )

    if len(entries) != 1:
        raise FinalStartError(
            "Expected exactly one plate G-code."
        )

    gcode_entry = entries[0]

    from am_print_executor import (
        developer_mode_backend_v1120
        as backend
    )

    wire_mapping = (
        list(mapping)
        + [-1] * (5 - len(mapping))
    )

    sequence_id = str(
        int(time.time() * 1000)
    )

    payload = (
        backend.build_project_file_payload(
            remote_path=remote_path,
            gcode_entry=gcode_entry,
            sequence_id=sequence_id,
            use_ams=True,
            ams_mapping=mapping,
        )
    )

    actual_wire = (
        payload
        .get("print", {})
        .get("ams_mapping")
    )

    if actual_wire != wire_mapping:
        raise FinalStartError(
            "Backend wire mapping mismatch: "
            f"expected={wire_mapping}, "
            f"actual={actual_wire}"
        )

    command = (
        payload
        .get("print", {})
        .get("command")
    )

    if command != "project_file":
        raise FinalStartError(
            f"Unexpected command: {command!r}"
        )

    access_code = getpass.getpass(
        "X1C Access Code "
        "(hidden, never stored): "
    ).strip()

    if not access_code:
        raise FinalStartError(
            "Access Code is empty."
        )

    import paho.mqtt.client as mqtt

    connected = threading.Event()
    subscribed = threading.Event()
    status_seen = threading.Event()
    active = threading.Event()

    state = {
        "error": None,
        "latest": None,
        "states": [],
        "xcam_seen": False,
    }

    def reason_failed(reason):
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
        if reason_failed(reason_code):
            state["error"] = (
                "MQTT connect rejected: "
                + str(reason_code)
            )

            connected.set()
            return

        connected.set()

        client.subscribe(
            f"device/{args.device_id}/report",
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

        state["latest"] = pobj

        gstate = str(
            pobj.get(
                "gcode_state"
            )
            or ""
        ).upper()

        if gstate:
            if (
                not state["states"]
                or state["states"][-1]
                != gstate
            ):
                state["states"].append(
                    gstate
                )

        if isinstance(
            pobj.get("xcam"),
            dict,
        ):
            state["xcam_seen"] = True

        if gstate in {
            "PREPARE",
            "RUNNING",
            "SLICING",
            "PAUSE",
            "PAUSED",
        }:
            active.set()

        status_seen.set()

    client = mqtt.Client(
        callback_api_version=
            mqtt.CallbackAPIVersion.VERSION2,
        client_id=(
            "nl-am-real-dog-"
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

    publish_count = 0

    try:
        client.connect_async(
            args.ip,
            8883,
            keepalive=60,
        )

        client.loop_start()

        if not connected.wait(30):
            raise FinalStartError(
                "MQTT connection timeout."
            )

        if state["error"]:
            raise FinalStartError(
                state["error"]
            )

        if not subscribed.wait(10):
            raise FinalStartError(
                "MQTT subscription timeout."
            )

        if not status_seen.wait(5):
            pushall = {
                "pushing": {
                    "sequence_id": str(
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
                f"device/{args.device_id}/request",
                json.dumps(
                    pushall,
                    separators=(",", ":"),
                ),
                qos=1,
                retain=False,
            )

            info.wait_for_publish(
                timeout=8
            )

            if not status_seen.wait(20):
                raise FinalStartError(
                    "No current printer status."
                )

        latest = state["latest"]

        idle = backend._strict_idle_check(
            latest
        )

        if not idle.get("passed"):
            raise FinalStartError(
                "Printer is no longer safely idle: "
                + json.dumps(
                    idle,
                    ensure_ascii=False,
                )
            )

        print()
        print(
            "FINAL REAL PRINT ARMED"
        )

        print(
            "Remote:",
            remote_path,
        )

        print(
            "Logical AMS mapping:",
            mapping,
        )

        print(
            "Wire AMS mapping:",
            actual_wire,
        )

        print()
        print(
            "Publishing ONE project_file "
            "start command..."
        )

        info = client.publish(
            f"device/{args.device_id}/request",
            json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            qos=0,
            retain=False,
        )

        publish_count = 1

        info.wait_for_publish(
            timeout=8
        )

        if not info.is_published():
            raise FinalStartError(
                "Print-start MQTT publish "
                "did not complete."
            )

        if not active.wait(90):
            raise FinalStartError(
                "Start command was published, "
                "but no active print state "
                "was observed within 90 seconds."
            )

        latest = state["latest"] or {}

        result = {
            "schema_version": "1.0.0",
            "module": "M4",
            "stage": "x1c_final_real_start",

            "status":
                "x1c_real_print_started",

            "device": {
                "device_id":
                    args.device_id,
                "ip_address":
                    args.ip,
            },

            "remote_artifact": {
                "path":
                    remote_path,

                "local_sha256":
                    upload["artifact"][
                        "sha256"
                    ],

                "remote_sha256":
                    upload["remote"][
                        "sha256"
                    ],
            },

            "ams": {
                "logical_mapping":
                    mapping,

                "wire_mapping":
                    actual_wire,
            },

            "print": {
                "start_publish_count":
                    publish_count,

                "active_state_observed":
                    True,

                "state_sequence":
                    state["states"],

                "current_state":
                    latest.get(
                        "gcode_state"
                    ),
            },

            "native_ai": {
                "xcam_observed":
                    state["xcam_seen"],
            },

            "policy": {
                "automatic_start_retry":
                    False,

                "pause_stop_resume_count":
                    0,

                "runtime_parameter_adjustment_count":
                    0,

                "access_code_stored":
                    False,
            },
        }

        out = Path(
            args.report
        ).resolve()

        out.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        out.write_text(
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
            "M4_X1C_REAL_PRINT_START=PASS"
        )

        print(
            "ACTIVE_STATE="
            + str(
                latest.get(
                    "gcode_state"
                )
            )
        )

        print(
            "X1C_NATIVE_AI_XCAM_OBSERVED="
            + str(
                state["xcam_seen"]
            )
        )

        return 0

    finally:
        try:
            client.loop_stop()
        except Exception:
            pass

        try:
            client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())

    except Exception as exc:
        print()
        print(
            "M4_X1C_REAL_PRINT_START=FAIL"
        )
        print(
            "ERROR="
            + type(exc).__name__
            + ": "
            + str(exc)
        )
        raise SystemExit(2)
