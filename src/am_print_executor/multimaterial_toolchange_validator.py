from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile

from collections import Counter
from pathlib import Path
from typing import Any


class ToolchangeValidationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def validate_multimaterial_gcode(
    path: Path,
) -> dict[str, Any]:

    path = path.expanduser().resolve()

    if not path.is_file():
        raise ToolchangeValidationError(
            f"G-code 3MF missing: {path}"
        )

    if not zipfile.is_zipfile(path):
        raise ToolchangeValidationError(
            "Artifact is not a valid 3MF ZIP."
        )

    with zipfile.ZipFile(path, "r") as zf:
        bad = zf.testzip()

        if bad is not None:
            raise ToolchangeValidationError(
                f"Corrupt member: {bad}"
            )

        names = zf.namelist()

        gcode_entries = sorted(
            name
            for name in names
            if re.fullmatch(
                r"Metadata/plate_\d+\.gcode",
                name,
            )
        )

        if len(gcode_entries) != 1:
            raise ToolchangeValidationError(
                "Expected exactly one plate G-code; "
                f"found {gcode_entries}"
            )

        settings_name = (
            "Metadata/project_settings.config"
        )

        if settings_name not in names:
            raise ToolchangeValidationError(
                "project_settings.config missing."
            )

        settings = json.loads(
            zf.read(
                settings_name
            ).decode(
                "utf-8-sig"
            )
        )

        gcode = zf.read(
            gcode_entries[0]
        ).decode(
            "utf-8",
            errors="replace",
        )

    filament_ids = settings.get(
        "filament_settings_id"
    )

    filament_colours = settings.get(
        "filament_colour"
    )

    if not isinstance(
        filament_ids,
        list,
    ):
        raise ToolchangeValidationError(
            "filament_settings_id is not a list."
        )

    filament_count = len(
        filament_ids
    )

    if filament_count < 2:
        raise ToolchangeValidationError(
            "Artifact is not multi-material."
        )

    if not isinstance(
        filament_colours,
        list,
    ):
        raise ToolchangeValidationError(
            "filament_colour is not a list."
        )

    if len(filament_colours) != filament_count:
        raise ToolchangeValidationError(
            "Filament colour count mismatch."
        )

    expected_tool_ids = list(
        range(filament_count)
    )

    tool_counts: Counter[int] = Counter()
    m620_counts: Counter[int] = Counter()

    tool_sequence: list[int] = []

    for raw_line in gcode.splitlines():
        # Remove comments before matching commands.
        command = raw_line.split(
            ";",
            1,
        )[0].strip()

        if not command:
            continue

        tool_match = re.fullmatch(
            r"T(\d+)(?:\s*)",
            command,
        )

        if tool_match:
            tool_id = int(
                tool_match.group(1)
            )

            tool_counts[
                tool_id
            ] += 1

            if (
                tool_id in expected_tool_ids
                and (
                    not tool_sequence
                    or tool_sequence[-1] != tool_id
                )
            ):
                tool_sequence.append(
                    tool_id
                )

        for match in re.finditer(
            r"(?:^|\s)M620\s+S(\d+)",
            command,
        ):
            m620_counts[
                int(match.group(1))
            ] += 1

    missing_t = [
        tool_id
        for tool_id in expected_tool_ids
        if tool_counts[tool_id] == 0
    ]

    missing_m620 = [
        tool_id
        for tool_id in expected_tool_ids
        if m620_counts[tool_id] == 0
    ]

    distinct_tools_seen = sorted(
        {
            tool_id
            for tool_id in tool_sequence
            if tool_id in expected_tool_ids
        }
    )

    real_transition_observed = (
        len(distinct_tools_seen) >= 2
    )

    passed = (
        not missing_t
        and not missing_m620
        and real_transition_observed
    )

    return {
        "schema_version":
            "1.0.0",

        "module":
            "M4",

        "stage":
            "multimaterial_toolchange_validation",

        "status":
            (
                "multimaterial_toolchange_validated"
                if passed
                else "multimaterial_toolchange_blocked"
            ),

        "artifact": {
            "path":
                str(path),

            "sha256":
                _sha256(path),

            "size_bytes":
                path.stat().st_size,

            "gcode_entry":
                gcode_entries[0],
        },

        "project_filament_count":
            filament_count,

        "filament_settings_id":
            filament_ids,

        "filament_colours":
            filament_colours,

        "expected_tool_ids":
            expected_tool_ids,

        "tool_command_counts": {
            str(key): value
            for key, value
            in sorted(
                tool_counts.items()
            )
        },

        "m620_filament_counts": {
            str(key): value
            for key, value
            in sorted(
                m620_counts.items()
            )
        },

        "tool_transition_sequence":
            tool_sequence,

        "distinct_expected_tools_seen":
            distinct_tools_seen,

        "missing_tool_commands":
            missing_t,

        "missing_m620_commands":
            missing_m620,

        "real_tool_transition_observed":
            real_transition_observed,

        "passed":
            passed,

        "policy": {
            "network_used":
                False,

            "printer_command_sent":
                False,

            "ams_mapping_used":
                False,
        },
    }


def cli_main(
    argv: list[str] | None = None,
) -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Validate real multi-material "
            "Tn and M620 commands in a "
            "Bambu .gcode.3mf."
        )
    )

    parser.add_argument(
        "--artifact",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args(argv)

    try:
        result = (
            validate_multimaterial_gcode(
                Path(args.artifact)
            )
        )

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
            if result["passed"]
            else 3
        )

    except Exception as exc:
        print(
            json.dumps(
                {
                    "status":
                        "toolchange_validation_error",

                    "error":
                        (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
