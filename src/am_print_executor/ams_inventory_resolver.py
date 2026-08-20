from __future__ import annotations

import argparse
import getpass
import json
import re
import ssl
import time
import uuid
import zipfile

from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt


class AmsInventoryError(RuntimeError):
    pass


_HEX = re.compile(
    r"^#?([0-9A-Fa-f]{6})(?:[0-9A-Fa-f]{2})?$"
)


def normalize_colour(
    value: Any,
) -> str | None:
    if not isinstance(value, str):
        return None

    value = value.strip()

    match = _HEX.fullmatch(value)

    if match is None:
        return None

    return "#" + match.group(1).upper()


def normalize_material(
    value: Any,
) -> str | None:
    if not isinstance(value, str):
        return None

    value = value.strip().upper()

    return value or None


def standard_ams_wire_slot(
    ams_id: int,
    slot_id: int,
) -> int:
    if ams_id < 0 or slot_id < 0:
        raise AmsInventoryError(
            "AMS and slot IDs must be non-negative."
        )

    if slot_id > 3:
        raise AmsInventoryError(
            "Standard AMS slot ID must be 0..3."
        )

    return ams_id * 4 + slot_id


def extract_inventory(
    payload: dict[str, Any],
) -> dict[str, Any] | None:

    root = payload.get(
        "print",
        payload,
    )

    if not isinstance(root, dict):
        return None

    ams_block = root.get("ams")

    if not isinstance(ams_block, dict):
        return None

    units = ams_block.get("ams")

    if not isinstance(units, list):
        return None

    tray_exist_bits_raw = (
        ams_block.get("tray_exist_bits")
    )

    tray_exist_bits = None

    if isinstance(
        tray_exist_bits_raw,
        str,
    ):
        try:
            tray_exist_bits = int(
                tray_exist_bits_raw,
                16,
            )
        except ValueError:
            tray_exist_bits = None

    slots = []

    for unit in units:
        if not isinstance(unit, dict):
            continue

        try:
            ams_id = int(
                str(unit.get("id", ""))
            )
        except ValueError:
            continue

        trays = unit.get("tray")

        if not isinstance(trays, list):
            continue

        for tray in trays:
            if not isinstance(tray, dict):
                continue

            try:
                slot_id = int(
                    str(tray.get("id", ""))
                )
            except ValueError:
                continue

            if not 0 <= slot_id <= 3:
                continue

            wire_slot = (
                standard_ams_wire_slot(
                    ams_id,
                    slot_id,
                )
            )

            material = normalize_material(
                tray.get("tray_type")
            )

            colour = normalize_colour(
                tray.get("tray_color")
            )

            if tray_exist_bits is not None:
                exists = bool(
                    tray_exist_bits
                    & (1 << wire_slot)
                )
            else:
                exists = bool(
                    material
                    or colour
                    or tray.get(
                        "tray_info_idx"
                    )
                    or tray.get(
                        "setting_id"
                    )
                )

            slots.append(
                {
                    "ams_id":
                        ams_id,

                    "slot_id":
                        slot_id,

                    "display_slot":
                        (
                            f"{chr(ord('A') + ams_id)}"
                            f"{slot_id + 1}"
                        ),

                    "wire_slot":
                        wire_slot,

                    "exists":
                        exists,

                    "material":
                        material,

                    "colour":
                        colour,

                    "tray_info_idx":
                        tray.get(
                            "tray_info_idx"
                        ),

                    "filament_setting_id":
                        tray.get(
                            "setting_id"
                        ),

                    "sub_brand":
                        tray.get(
                            "tray_sub_brands"
                        ),

                    "remain_percent":
                        tray.get(
                            "remain"
                        ),

                    "remain_g":
                        tray.get(
                            "remain_g"
                        ),

                    "identity_ready":
                        bool(
                            material
                            and colour
                        ),
                }
            )

    slots.sort(
        key=lambda row: row[
            "wire_slot"
        ]
    )

    return {
        "tray_exist_bits":
            tray_exist_bits_raw,

        "slot_count":
            len(slots),

        "slots":
            slots,
    }


def read_project_requirements(
    artifact: Path,
) -> list[dict[str, Any]]:

    artifact = (
        artifact
        .expanduser()
        .resolve()
    )

    if not artifact.is_file():
        raise AmsInventoryError(
            f"Artifact missing: {artifact}"
        )

    if not zipfile.is_zipfile(
        artifact
    ):
        raise AmsInventoryError(
            "Artifact is not a valid 3MF ZIP."
        )

    with zipfile.ZipFile(
        artifact,
        "r",
    ) as zf:

        name = (
            "Metadata/project_settings.config"
        )

        if name not in zf.namelist():
            raise AmsInventoryError(
                "project_settings.config missing."
            )

        settings = json.loads(
            zf.read(name).decode(
                "utf-8-sig"
            )
        )

    ids = settings.get(
        "filament_settings_id"
    )

    colours = settings.get(
        "filament_colour"
    )

    materials = settings.get(
        "filament_type"
    )

    if not isinstance(ids, list):
        raise AmsInventoryError(
            "filament_settings_id missing."
        )

    if not isinstance(colours, list):
        raise AmsInventoryError(
            "filament_colour missing."
        )

    if not isinstance(materials, list):
        raise AmsInventoryError(
            "filament_type missing."
        )

    count = len(ids)

    if not (
        len(colours) == count
        and len(materials) == count
    ):
        raise AmsInventoryError(
            "Project filament metadata "
            "length mismatch."
        )

    requirements = []

    for index in range(count):

        colour = normalize_colour(
            colours[index]
        )

        material = normalize_material(
            materials[index]
        )

        if colour is None:
            raise AmsInventoryError(
                "Project contains an invalid "
                f"filament colour: {colours[index]!r}"
            )

        if material is None:
            raise AmsInventoryError(
                "Project contains an invalid "
                f"filament type: {materials[index]!r}"
            )

        requirements.append(
            {
                "logical_filament_id":
                    index + 1,

                "gcode_tool_id":
                    index,

                "settings_id":
                    ids[index],

                "material":
                    material,

                "colour":
                    colour,
            }
        )

    return requirements


def resolve_mapping(
    *,
    requirements: list[dict[str, Any]],
    inventory: dict[str, Any],
) -> dict[str, Any]:

    available = [
        slot
        for slot in inventory[
            "slots"
        ]
        if (
            slot["exists"]
            and slot["identity_ready"]
        )
    ]

    candidates: dict[int, list[int]] = {}

    for requirement in requirements:

        matches = [
            slot["wire_slot"]
            for slot in available
            if (
                slot["material"]
                == requirement["material"]
                and slot["colour"]
                == requirement["colour"]
            )
        ]

        candidates[
            requirement[
                "logical_filament_id"
            ]
        ] = matches

    solutions: list[list[int]] = []

    def search(
        index: int,
        used: set[int],
        mapping: list[int],
    ) -> None:

        if len(solutions) > 1:
            return

        if index == len(requirements):
            solutions.append(
                list(mapping)
            )
            return

        logical_id = requirements[
            index
        ]["logical_filament_id"]

        for slot in candidates[
            logical_id
        ]:

            if slot in used:
                continue

            used.add(slot)
            mapping.append(slot)

            search(
                index + 1,
                used,
                mapping,
            )

            mapping.pop()
            used.remove(slot)

    search(
        0,
        set(),
        [],
    )

    if len(solutions) == 1:

        mapping = solutions[0]

        status = (
            "ams_mapping_resolved"
        )

        blockers = []

    elif len(solutions) == 0:

        mapping = None

        status = (
            "ams_mapping_blocked"
        )

        blockers = [
            "no_complete_exact_assignment"
        ]

    else:

        mapping = None

        status = (
            "ams_mapping_blocked"
        )

        blockers = [
            "ambiguous_exact_assignment"
        ]

    return {
        "status":
            status,

        "requirements":
            requirements,

        "candidate_wire_slots":
            {
                str(key): value
                for key, value
                in candidates.items()
            },

        "ams_mapping_logical":
            mapping,

        "blockers":
            blockers,

        "policy": {
            "exact_material_match_required":
                True,

            "exact_colour_match_required":
                True,

            "guessing_allowed":
                False,

            "distinct_physical_slots_required":
                True,
        },
    }


def capture_passive_inventory(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
    duration_seconds: int,
) -> dict[str, Any]:

    if not access_code:
        raise AmsInventoryError(
            "Access Code cannot be empty."
        )

    topic = (
        f"device/{device_id}/report"
    )

    observations: list[
        dict[str, Any]
    ] = []

    connected = False
    connection_error = None

    def on_connect(
        client,
        userdata,
        flags,
        reason_code,
        properties=None,
    ):
        nonlocal connected
        nonlocal connection_error

        try:
            rc = int(reason_code)
        except Exception:
            rc = getattr(
                reason_code,
                "value",
                1,
            )

        if rc != 0:
            connection_error = (
                f"MQTT connect failed: "
                f"{reason_code}"
            )
            return

        connected = True

        client.subscribe(
            topic,
            qos=0,
        )

    def on_message(
        client,
        userdata,
        message,
    ):
        try:
            payload = json.loads(
                message.payload.decode(
                    "utf-8",
                    errors="strict",
                )
            )
        except Exception:
            return

        if not isinstance(
            payload,
            dict,
        ):
            return

        inventory = extract_inventory(
            payload
        )

        if (
            inventory is not None
            and inventory["slot_count"] > 0
        ):
            observations.append(
                inventory
            )

    client_id = (
        "nl-am-ams-inventory-"
        + uuid.uuid4().hex[:10]
    )

    try:
        client = mqtt.Client(
            callback_api_version=
                mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
    except (AttributeError, TypeError):
        client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
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
    client.on_message = on_message

    client.connect(
        ip_address,
        8883,
        keepalive=30,
    )

    client.loop_start()

    try:
        deadline = (
            time.time()
            + duration_seconds
        )

        while time.time() < deadline:

            if connection_error:
                raise AmsInventoryError(
                    connection_error
                )

            # Keep listening after the first AMS report
            # briefly so a fuller push_status can replace
            # an earlier partial report.
            if observations:
                time.sleep(2.0)
                break

            time.sleep(0.2)

    finally:
        client.loop_stop()

        try:
            client.disconnect()
        except Exception:
            pass

    if not connected:
        raise AmsInventoryError(
            "MQTT connection was not established."
        )

    if not observations:
        raise AmsInventoryError(
            "No passive AMS inventory report "
            "was observed. No pushall request "
            "was sent."
        )

    # Prefer the observation with the largest number
    # of ready physical slots.
    observations.sort(
        key=lambda inv: (
            sum(
                1
                for slot in inv["slots"]
                if slot["identity_ready"]
            ),
            inv["slot_count"],
        ),
        reverse=True,
    )

    return observations[0]


def cli_main(
    argv: list[str] | None = None,
) -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Passively read X1C AMS inventory "
            "and resolve logical project "
            "filaments to exact physical slots."
        )
    )

    parser.add_argument(
        "--artifact",
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
        "--duration",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args(argv)

    access_code = getpass.getpass(
        "Enter X1C Access Code "
        "(hidden, never stored): "
    )

    try:
        requirements = (
            read_project_requirements(
                Path(args.artifact)
            )
        )

        inventory = (
            capture_passive_inventory(
                ip_address=args.ip,
                device_id=args.device_id,
                access_code=access_code,
                duration_seconds=
                    args.duration,
            )
        )

        resolution = resolve_mapping(
            requirements=requirements,
            inventory=inventory,
        )

        result = {
            "schema_version":
                "1.0.0",

            "module":
                "M4",

            "stage":
                "ams_inventory_resolution",

            "status":
                resolution["status"],

            "device": {
                "device_id":
                    args.device_id,

                "ip_address":
                    args.ip,
            },

            "inventory":
                inventory,

            "resolution":
                resolution,

            "policy": {
                "passive_subscription_only":
                    True,

                "mqtt_publish_count":
                    0,

                "pushall_sent":
                    False,

                "printer_command_sent":
                    False,

                "print_started":
                    False,

                "access_code_stored":
                    False,
            },
        }

        report = (
            Path(args.report)
            .expanduser()
            .resolve()
        )

        report.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        report.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )

        return (
            0
            if result["status"]
            == "ams_mapping_resolved"
            else 3
        )

    except Exception as exc:
        failure = {
            "schema_version":
                "1.0.0",

            "module":
                "M4",

            "stage":
                "ams_inventory_resolution",

            "status":
                "ams_inventory_error",

            "error_type":
                type(exc).__name__,

            "error":
                str(exc),

            "device": {
                "device_id":
                    getattr(args, "device_id", None),

                "ip_address":
                    getattr(args, "ip", None),
            },

            "policy": {
                "passive_subscription_only":
                    True,

                "mqtt_publish_count":
                    0,

                "pushall_sent":
                    False,

                "printer_command_sent":
                    False,

                "print_started":
                    False,

                "access_code_stored":
                    False,
            },
        }

        # Failure evidence is just as important as success
        # evidence. Always persist the report when --report
        # was supplied.
        try:
            report = (
                Path(args.report)
                .expanduser()
                .resolve()
            )

            report.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            report.write_text(
                json.dumps(
                    failure,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            failure[
                "report_written"
            ] = True

            failure[
                "report_path"
            ] = str(report)

        except Exception as report_exc:
            failure[
                "report_written"
            ] = False

            failure[
                "report_write_error"
            ] = (
                f"{type(report_exc).__name__}: "
                f"{report_exc}"
            )

        print(
            json.dumps(
                failure,
                ensure_ascii=False,
                indent=2,
            )
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
