from __future__ import annotations

import argparse
import hashlib
import json
import zipfile

from pathlib import Path
from typing import Any

from .ams_mapping import to_x1c_wire_mapping


class X1CPreprintDryRunError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()

    if not path.is_file():
        raise X1CPreprintDryRunError(
            f"Required report missing: {path}"
        )

    try:
        obj = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception as exc:
        raise X1CPreprintDryRunError(
            f"Cannot parse JSON report: {path}"
        ) from exc

    if not isinstance(obj, dict):
        raise X1CPreprintDryRunError(
            f"JSON report is not an object: {path}"
        )

    return obj


def inspect_artifact(
    artifact: Path,
) -> dict[str, Any]:
    artifact = (
        artifact
        .expanduser()
        .resolve()
    )

    if not artifact.is_file():
        raise X1CPreprintDryRunError(
            f"Artifact missing: {artifact}"
        )

    if not zipfile.is_zipfile(artifact):
        raise X1CPreprintDryRunError(
            "Artifact is not a valid GCode3MF ZIP."
        )

    with zipfile.ZipFile(
        artifact,
        "r",
    ) as zf:
        entries = [
            name
            for name in zf.namelist()
            if name.lower().endswith(
                ".gcode"
            )
        ]

        if len(entries) != 1:
            raise X1CPreprintDryRunError(
                "Expected exactly one internal "
                f"G-code entry, got {len(entries)}."
            )

        settings_name = (
            "Metadata/"
            "project_settings.config"
        )

        if settings_name not in zf.namelist():
            raise X1CPreprintDryRunError(
                "Missing project_settings.config."
            )

        try:
            settings = json.loads(
                zf.read(
                    settings_name
                ).decode(
                    "utf-8-sig"
                )
            )
        except Exception as exc:
            raise X1CPreprintDryRunError(
                "Cannot parse project settings."
            ) from exc

    return {
        "path": str(artifact),
        "sha256": _sha256(artifact),
        "size_bytes":
            artifact.stat().st_size,
        "gcode_entries": entries,
        "curr_bed_type":
            settings.get(
                "curr_bed_type"
            ),
    }


def build_preprint_report(
    *,
    artifact: Path,
    toolpath_report: Path,
    ams_report: Path,
    build_plate: str,
) -> dict[str, Any]:

    if (
        not isinstance(build_plate, str)
        or not build_plate.strip()
    ):
        raise X1CPreprintDryRunError(
            "build_plate must be a "
            "non-empty string."
        )

    build_plate = build_plate.strip()

    artifact_info = inspect_artifact(
        artifact
    )

    actual_bed = artifact_info[
        "curr_bed_type"
    ]

    if actual_bed != build_plate:
        raise X1CPreprintDryRunError(
            "Artifact build plate mismatch: "
            f"expected={build_plate!r}, "
            f"actual={actual_bed!r}"
        )

    toolpath = _load_json(
        toolpath_report
    )

    if toolpath.get("status") != (
        "multimaterial_toolchange_validated"
    ):
        raise X1CPreprintDryRunError(
            "Toolpath validation is not PASS."
        )

    expected_tool_ids = (
        toolpath.get(
            "expected_tool_ids"
        )
    )

    filament_count = (
        toolpath.get(
            "project_filament_count"
        )
    )

    if not isinstance(
        expected_tool_ids,
        list,
    ):
        raise X1CPreprintDryRunError(
            "Toolpath report is missing "
            "expected_tool_ids."
        )

    if not isinstance(
        filament_count,
        int,
    ):
        raise X1CPreprintDryRunError(
            "Toolpath report is missing "
            "project_filament_count."
        )

    if (
        len(expected_tool_ids)
        != filament_count
    ):
        raise X1CPreprintDryRunError(
            "Toolpath filament/tool count "
            "is inconsistent."
        )

    toolpath_sha = (
        toolpath.get(
            "artifact",
            {}
        ).get(
            "sha256"
        )
        if isinstance(
            toolpath.get("artifact"),
            dict,
        )
        else None
    )

    if (
        toolpath_sha is not None
        and toolpath_sha
        != artifact_info["sha256"]
    ):
        raise X1CPreprintDryRunError(
            "Toolpath report SHA256 does not "
            "match current artifact."
        )

    ams = _load_json(
        ams_report
    )

    if ams.get("status") != (
        "ams_mapping_resolved"
    ):
        raise X1CPreprintDryRunError(
            "AMS inventory resolution "
            "is not PASS."
        )

    resolution = ams.get(
        "resolution"
    )

    if not isinstance(
        resolution,
        dict,
    ):
        raise X1CPreprintDryRunError(
            "AMS resolution object missing."
        )

    logical_mapping = (
        resolution.get(
            "ams_mapping_logical"
        )
    )

    blockers = resolution.get(
        "blockers"
    )

    if not isinstance(
        logical_mapping,
        list,
    ):
        raise X1CPreprintDryRunError(
            "AMS logical mapping missing."
        )

    if len(logical_mapping) != (
        filament_count
    ):
        raise X1CPreprintDryRunError(
            "AMS mapping count does not match "
            "project filament count."
        )

    if not isinstance(
        blockers,
        list,
    ):
        raise X1CPreprintDryRunError(
            "AMS blockers field missing."
        )

    if blockers:
        raise X1CPreprintDryRunError(
            "AMS resolution contains blockers: "
            + repr(blockers)
        )

    if (
        len(set(logical_mapping))
        != len(logical_mapping)
    ):
        raise X1CPreprintDryRunError(
            "AMS mapping does not use distinct "
            "physical slots."
        )

    wire_mapping = (
        to_x1c_wire_mapping(
            logical_mapping
        )
    )

    inventory = ams.get(
        "inventory",
        {}
    )

    slots = (
        inventory.get(
            "slots",
            []
        )
        if isinstance(
            inventory,
            dict,
        )
        else []
    )

    selected_inventory = []

    for wire_slot in logical_mapping:
        matches = [
            row
            for row in slots
            if isinstance(row, dict)
            and row.get(
                "wire_slot"
            ) == wire_slot
        ]

        if len(matches) != 1:
            raise X1CPreprintDryRunError(
                "Resolved AMS slot does not map "
                "to exactly one inventory row: "
                f"wire_slot={wire_slot!r}, "
                f"matches={len(matches)}"
            )

        selected_inventory.append(
            matches[0]
        )

    return {
        "ams": {
            "blockers":
                blockers,
            "logical_mapping":
                logical_mapping,
            "selected_inventory":
                selected_inventory,
            "x1c_wire_mapping":
                wire_mapping,
        },

        "artifact": {
            "path":
                artifact_info["path"],
            "sha256":
                artifact_info["sha256"],
        },

        "build_plate": {
            "expected":
                build_plate,
            "actual":
                actual_bed,
            "validated":
                True,
        },

        "module":
            "M4",

        "policy": {
            "artifact_uploaded":
                False,
            "mqtt_publish_count":
                0,
            "network_used":
                False,
            "print_started":
                False,
            "printer_command_sent":
                False,
        },

        "schema_version":
            "1.1.0",

        "stage":
            "x1c_preprint_dry_run",

        "status":
            "x1c_preprint_dry_run_pass",

        "toolpath": {
            "expected_tool_ids":
                expected_tool_ids,
            "missing_m620_commands":
                toolpath.get(
                    "missing_m620_commands"
                ),
            "missing_tool_commands":
                toolpath.get(
                    "missing_tool_commands"
                ),
            "passed":
                True,
            "project_filament_count":
                filament_count,
            "real_tool_transition_observed":
                toolpath.get(
                    "real_tool_transition_observed"
                ),
        },
    }


def run(
    *,
    artifact: Path,
    toolpath_report: Path,
    ams_report: Path,
    build_plate: str,
    report: Path,
) -> dict[str, Any]:

    result = build_preprint_report(
        artifact=artifact,
        toolpath_report=toolpath_report,
        ams_report=ams_report,
        build_plate=build_plate,
    )

    report = (
        report
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

    return result


def cli_main(
    argv: list[str] | None = None,
) -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Offline X1C preprint dry-run "
            "evidence builder."
        )
    )

    parser.add_argument(
        "--artifact",
        required=True,
    )

    parser.add_argument(
        "--toolpath-report",
        required=True,
    )

    parser.add_argument(
        "--ams-report",
        required=True,
    )

    parser.add_argument(
        "--build-plate",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args(
        argv
    )

    report_path = Path(
        args.report
    )

    try:
        result = run(
            artifact=Path(
                args.artifact
            ),
            toolpath_report=Path(
                args.toolpath_report
            ),
            ams_report=Path(
                args.ams_report
            ),
            build_plate=
                args.build_plate,
            report=
                report_path,
        )

    except X1CPreprintDryRunError as exc:
        blocked = {
            "module": "M4",
            "stage":
                "x1c_preprint_dry_run",
            "status":
                "x1c_preprint_dry_run_blocked",
            "error":
                str(exc),
            "policy": {
                "artifact_uploaded":
                    False,
                "mqtt_publish_count":
                    0,
                "network_used":
                    False,
                "print_started":
                    False,
                "printer_command_sent":
                    False,
            },
        }

        report_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        report_path.write_text(
            json.dumps(
                blocked,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        print(
            json.dumps(
                blocked,
                ensure_ascii=False,
                indent=2,
            )
        )

        return 2

    print(
        json.dumps(
            {
                "status":
                    result["status"],
                "artifact_sha256":
                    result[
                        "artifact"
                    ]["sha256"],
                "build_plate":
                    result[
                        "build_plate"
                    ]["actual"],
                "logical_mapping":
                    result[
                        "ams"
                    ][
                        "logical_mapping"
                    ],
                "x1c_wire_mapping":
                    result[
                        "ams"
                    ][
                        "x1c_wire_mapping"
                    ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
