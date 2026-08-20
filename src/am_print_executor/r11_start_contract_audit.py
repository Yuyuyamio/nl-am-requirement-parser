from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import zipfile

from pathlib import Path
from typing import Any


class R11ContractError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(
        Path(path).read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(obj, dict):
        raise R11ContractError(
            f"Expected JSON object: {path}"
        )

    return obj


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with Path(path).open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def find_values(
    value: Any,
    key_name: str,
) -> list[Any]:
    found = []

    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) == key_name:
                found.append(child)

            found.extend(
                find_values(
                    child,
                    key_name,
                )
            )

    elif isinstance(value, list):
        for child in value:
            found.extend(
                find_values(
                    child,
                    key_name,
                )
            )

    return found


def run(
    job_dir: Path,
) -> dict[str, Any]:
    from am_print_executor import (
        developer_mode_backend_v1120
        as backend,
    )

    job_dir = Path(
        job_dir
    ).expanduser().resolve()

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

    r10_path = (
        job_dir
        / "r10_final_preflight.json"
    )

    report_path = (
        job_dir
        / "r11a_start_contract_audit.json"
    )

    r7 = load_json(r7_path)
    r8 = load_json(r8_path)
    r9 = load_json(r9_path)
    r10 = load_json(r10_path)

    # --------------------------------------------------------
    # Gate locks
    # --------------------------------------------------------

    r10_pass = (
        r10.get("status")
        == "final_preflight_pass"
        and r10.get(
            "automatic_gate_pass"
        ) is True
        and r10.get(
            "material_sufficient_manually_confirmed"
        ) is True
        and r10.get(
            "final_gate_pass"
        ) is True
    )

    r9_pass = (
        r9.get("status")
        == "upload_only_verified"
        and r9.get(
            "remote_size_verified"
        ) is True
        and r9.get(
            "remote_sha256_verified"
        ) is True
    )

    remote_path = str(
        r9.get("remote_path")
        or ""
    )

    remote_sha = str(
        r9.get("remote_sha256")
        or ""
    )

    # --------------------------------------------------------
    # Local artifact lock
    # --------------------------------------------------------

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
        raise R11ContractError(
            "Approved G-code package missing."
        )

    local_sha = sha256_file(
        local_path
    )

    expected_sha = str(
        gcode_info.get("sha256")
        or ""
    )

    artifact_lock = (
        local_sha
        == expected_sha
        == remote_sha
    )

    # --------------------------------------------------------
    # Resolve exact G-code entry inside 3MF
    # --------------------------------------------------------

    with zipfile.ZipFile(
        local_path,
        "r",
    ) as zf:
        gcode_entries = sorted(
            name
            for name in zf.namelist()
            if (
                name.startswith(
                    "Metadata/"
                )
                and name.endswith(
                    ".gcode"
                )
            )
        )

    if len(gcode_entries) != 1:
        raise R11ContractError(
            "Expected exactly one printable "
            f"G-code entry, got {gcode_entries!r}"
        )

    gcode_entry = (
        gcode_entries[0]
    )

    # --------------------------------------------------------
    # Derive AMS mapping from the FORMAL R7 mapping.
    #
    # Logical material order:
    #   logical 0 -> selected tray
    #   logical 1 -> selected tray
    #
    # No dog-specific hardcoded [0,3].
    # --------------------------------------------------------

    mappings = (
        r7.get("mapping")
        or []
    )

    ordered = sorted(
        mappings,
        key=lambda row:
            int(
                row.get(
                    "logical_index"
                )
            ),
    )

    ams_mapping = []

    physical_keys = []

    for row in ordered:
        selected = row.get(
            "selected"
        )

        if not isinstance(
            selected,
            dict,
        ):
            raise R11ContractError(
                "R7 mapping contains no "
                "selected physical tray."
            )

        ams_id = str(
            selected.get(
                "ams_id"
            )
        )

        tray_id = str(
            selected.get(
                "tray_id"
            )
        )

        # Current X1C contract: all mapped
        # materials are in the single AMS unit 0.
        # Do not silently encode multi-AMS layouts.
        if ams_id != "0":
            raise R11ContractError(
                "Current sender contract has "
                "not proven multi-AMS wire "
                f"encoding. Observed AMS={ams_id}"
            )

        try:
            tray_index = int(
                tray_id
            )
        except Exception as exc:
            raise R11ContractError(
                f"Invalid physical tray ID: "
                f"{tray_id!r}"
            ) from exc

        if tray_index not in {
            0, 1, 2, 3
        }:
            raise R11ContractError(
                "Physical tray is outside "
                f"X1C AMS range: {tray_index}"
            )

        ams_mapping.append(
            tray_index
        )

        physical_keys.append(
            (
                ams_id,
                tray_id,
            )
        )

    distinct_mapping = (
        len(ams_mapping) == 2
        and len(
            set(physical_keys)
        ) == 2
    )

    # --------------------------------------------------------
    # Find current official payload builder.
    # --------------------------------------------------------

    builder = getattr(
        backend,
        "build_project_file_payload",
        None,
    )

    builder_name = (
        "build_project_file_payload"
    )

    if not callable(builder):
        builder = getattr(
            backend,
            "_build_project_file_payload",
            None,
        )

        builder_name = (
            "_build_project_file_payload"
        )

    if not callable(builder):
        raise R11ContractError(
            "No project_file payload builder "
            "exists in backend v1120."
        )

    signature = inspect.signature(
        builder
    )

    required_names = {
        "remote_path",
        "gcode_entry",
        "sequence_id",
        "use_ams",
        "ams_mapping",
    }

    available_names = set(
        signature.parameters
    )

    missing = (
        required_names
        - available_names
    )

    if missing:
        raise R11ContractError(
            "Payload builder contract changed. "
            f"Missing parameters: "
            f"{sorted(missing)}"
        )

    # Builder is executed OFFLINE only.
    payload = builder(
        remote_path=remote_path,
        gcode_entry=gcode_entry,
        sequence_id="0",
        use_ams=True,
        ams_mapping=ams_mapping,
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise R11ContractError(
            "Payload builder returned "
            "non-dict payload."
        )

    # --------------------------------------------------------
    # Static safety audit of builder itself.
    # --------------------------------------------------------

    source = inspect.getsource(
        builder
    )

    tree = ast.parse(
        source
    )

    publish_count = 0

    for node in ast.walk(tree):
        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        func = node.func

        if (
            isinstance(
                func,
                ast.Attribute,
            )
            and func.attr
            == "publish"
        ):
            publish_count += 1

    commands = [
        str(value)
        for value in find_values(
            payload,
            "command",
        )
    ]

    urls = [
        str(value)
        for value in find_values(
            payload,
            "url",
        )
    ]

    params = [
        str(value)
        for value in find_values(
            payload,
            "param",
        )
    ]

    mapping_wire = (
        find_values(
            payload,
            "ams_mapping",
        )
    )

    project_file_only = (
        commands.count(
            "project_file"
        ) == 1
        and all(
            command
            == "project_file"
            for command in commands
        )
    )

    dangerous_commands = [
        command
        for command in commands
        if command in {
            "pause",
            "resume",
            "stop",
            "abort",
        }
    ]

    # --------------------------------------------------------
    # Formal R11A checks
    # --------------------------------------------------------

    checks = {
        "r10_final_preflight_pass":
            r10_pass,

        "r9_upload_verified":
            r9_pass,

        "artifact_sha_chain_locked":
            artifact_lock,

        "remote_path_present":
            bool(remote_path),

        "single_gcode_entry":
            len(
                gcode_entries
            ) == 1,

        "two_distinct_ams_trays":
            distinct_mapping,

        "payload_builder_available":
            callable(builder),

        "payload_builder_publish_count_zero":
            publish_count == 0,

        "payload_contains_project_file_only":
            project_file_only,

        "payload_contains_no_dangerous_command":
            len(
                dangerous_commands
            ) == 0,
    }

    failed = [
        name
        for name, value
        in checks.items()
        if not value
    ]

    gate = (
        len(failed) == 0
    )

    report = {
        "schema_version":
            "r11a-start-contract-v1",

        "status":
            (
                "start_contract_pass"
                if gate
                else "start_contract_fail"
            ),

        "checks":
            checks,

        "failed_checks":
            failed,

        "artifact": {
            "local_path":
                str(local_path),

            "sha256":
                local_sha,

            "remote_path":
                remote_path,

            "gcode_entry":
                gcode_entry,
        },

        "ams_mapping":
            ams_mapping,

        "physical_mapping":
            physical_keys,

        "payload_builder": {
            "module":
                backend.__name__,

            "name":
                builder_name,

            "signature":
                str(signature),

            "publish_call_count":
                publish_count,
        },

        "payload":
            payload,

        "payload_observations": {
            "commands":
                commands,

            "urls":
                urls,

            "params":
                params,

            "ams_mapping_wire":
                mapping_wire,
        },

        "safety": {
            "network_used":
                False,

            "mqtt_publish_count":
                0,

            "artifact_uploaded":
                False,

            "printer_command_sent":
                False,

            "print_started":
                False,
        },

        "next_gate":
            (
                "R11B_REAL_START"
                if gate
                else None
            ),
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

    report["report_file"] = str(
        report_path
    )

    return report


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--job-dir",
        required=True,
    )

    args = parser.parse_args()

    result = run(
        Path(
            args.job_dir
        )
    )

    print(
        "=== R11A REAL-START CONTRACT AUDIT ==="
    )

    print(
        "NETWORK_USED=False"
    )

    print(
        "PRINTER_COMMAND_SENT=False"
    )

    print(
        "PRINT_STARTED=False"
    )

    print()
    print(
        "REMOTE_PATH=",
        result[
            "artifact"
        ][
            "remote_path"
        ],
    )

    print(
        "GCODE_ENTRY=",
        result[
            "artifact"
        ][
            "gcode_entry"
        ],
    )

    print(
        "AMS_MAPPING=",
        result[
            "ams_mapping"
        ],
    )

    print(
        "PAYLOAD_BUILDER=",
        result[
            "payload_builder"
        ][
            "name"
        ],
    )

    print(
        "PAYLOAD_BUILDER_SIGNATURE=",
        result[
            "payload_builder"
        ][
            "signature"
        ],
    )

    print(
        "PAYLOAD_BUILDER_PUBLISH_COUNT=",
        result[
            "payload_builder"
        ][
            "publish_call_count"
        ],
    )

    print()
    print(
        "=== EXACT OFFLINE PAYLOAD ==="
    )

    print(
        json.dumps(
            result[
                "payload"
            ],
            ensure_ascii=False,
            indent=2,
        )
    )

    print()
    print(
        "=== R11A CHECKS ==="
    )

    for name, value in result[
        "checks"
    ].items():
        print(
            name.upper(),
            "=",
            value,
        )

    print(
        "FAILED_CHECKS=",
        result[
            "failed_checks"
        ],
    )

    print(
        "R11A_REPORT=",
        result[
            "report_file"
        ],
    )

    print(
        "R11A_START_CONTRACT_GATE=",
        "PASS"
        if result[
            "status"
        ]
        == "start_contract_pass"
        else "FAIL",
    )

    return (
        0
        if result[
            "status"
        ]
        == "start_contract_pass"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
