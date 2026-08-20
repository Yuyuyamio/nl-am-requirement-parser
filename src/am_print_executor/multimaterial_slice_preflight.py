from __future__ import annotations

import argparse
import json
import subprocess
import time
import zipfile

from pathlib import Path
from typing import Any


class SlicePreflightError(RuntimeError):
    pass


BAMBU_CLI_EXIT_CODES = {
    0: "CLI_SUCCESS",
    -1: "CLI_ENVIRONMENT_ERROR",
    -2: "CLI_INVALID_PARAMS",
    -3: "CLI_FILE_NOTFOUND",
    -5: "CLI_CONFIG_FILE_ERROR",
    -13: "CLI_EXPORT_3MF_ERROR",
    -17: "CLI_PROCESS_NOT_COMPATIBLE",
    -18: "CLI_INVALID_VALUES_IN_3MF",
    -21: "CLI_OBJECT_ARRANGE_FAILED",
    -50: "CLI_NO_SUITABLE_OBJECTS",
    -51: "CLI_VALIDATE_ERROR",
    -52: "CLI_OBJECTS_PARTLY_INSIDE",
    -61: "CLI_FILAMENT_NOT_MATCH_BED_TYPE",
    -66: "CLI_FILAMENT_CAN_NOT_MAP",
    -68: "CLI_FILAMENTS_NOT_SUPPORTED_BY_EXTRUDER",
    -100: "CLI_SLICING_ERROR",
    -101: "CLI_GCODE_PATH_CONFLICTS",
    -102: "CLI_GCODE_PATH_IN_UNPRINTABLE_AREA",
    -103: "CLI_FILAMENT_UNPRINTABLE_ON_FIRST_LAYER",
    -104: "CLI_GCODE_PATH_OUTSIDE",
    -105: "CLI_GCODE_IN_WRAPPING_DETECT_AREA",
}


def signed_windows_exit_code(
    value: int,
) -> int:
    if value > 0x7FFFFFFF:
        return value - 0x100000000

    return value


def classify_bambu_exit_code(
    value: int,
) -> dict[str, Any]:
    signed = signed_windows_exit_code(
        value
    )

    return {
        "raw": value,
        "signed": signed,
        "symbol":
            BAMBU_CLI_EXIT_CODES.get(
                signed,
                "UNKNOWN_BAMBU_CLI_EXIT",
            ),
    }


def inspect_project_metadata(
    project_path: Path,
) -> dict[str, Any]:
    project_path = (
        project_path
        .expanduser()
        .resolve()
    )

    if not project_path.is_file():
        raise SlicePreflightError(
            f"Project missing: {project_path}"
        )

    if not zipfile.is_zipfile(
        project_path
    ):
        raise SlicePreflightError(
            f"Project is not a valid 3MF ZIP: "
            f"{project_path}"
        )

    with zipfile.ZipFile(
        project_path,
        "r",
    ) as zf:
        bad = zf.testzip()

        if bad is not None:
            raise SlicePreflightError(
                f"Corrupt project member: {bad}"
            )

        settings_name = (
            "Metadata/project_settings.config"
        )

        if settings_name not in zf.namelist():
            raise SlicePreflightError(
                "Project has no "
                "Metadata/project_settings.config."
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
            raise SlicePreflightError(
                "Cannot parse "
                "project_settings.config."
            ) from exc

    filament_ids = settings.get(
        "filament_settings_id"
    )

    filament_colours = settings.get(
        "filament_colour"
    )

    return {
        "path":
            str(project_path),

        "project_filament_count":
            (
                len(filament_ids)
                if isinstance(
                    filament_ids,
                    list,
                )
                else None
            ),

        "filament_settings_id":
            filament_ids,

        "filament_colours":
            filament_colours,

        "tower_settings": {
            key: value
            for key, value in settings.items()
            if any(
                token in key.lower()
                for token in (
                    "prime",
                    "wipe",
                    "tower",
                    "flush",
                )
            )
        },
    }


def run_direct_project_slice(
    *,
    studio_exe: Path,
    project_path: Path,
    output_path: Path,
    debug_level: int = 5,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    studio_exe = (
        studio_exe
        .expanduser()
        .resolve()
    )

    project_path = (
        project_path
        .expanduser()
        .resolve()
    )

    output_path = (
        output_path
        .expanduser()
        .resolve()
    )

    if not studio_exe.is_file():
        raise SlicePreflightError(
            f"Bambu Studio missing: {studio_exe}"
        )

    # Validate the input before invoking Studio.
    metadata = inspect_project_metadata(
        project_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_path.exists():
        output_path.unlink()

    command = [
        str(studio_exe),
        "--slice",
        "0",
        "--debug",
        str(debug_level),
        "--export-3mf",
        str(output_path),
        str(project_path),
    ]

    creationflags = 0

    if (
        hasattr(
            subprocess,
            "CREATE_NO_WINDOW",
        )
    ):
        creationflags = (
            subprocess.CREATE_NO_WINDOW
        )

    started = time.time()

    proc = subprocess.run(
        command,
        cwd=str(output_path.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        creationflags=creationflags,
    )

    exit_info = classify_bambu_exit_code(
        proc.returncode
    )

    output_exists = (
        output_path.is_file()
    )

    succeeded = (
        exit_info["signed"] == 0
        and output_exists
    )

    return {
        "status":
            "direct_project_slice_observed",

        "project":
            metadata,

        "command":
            command,

        "elapsed_seconds":
            round(
                time.time() - started,
                3,
            ),

        "exit":
            exit_info,

        "output_path":
            str(output_path),

        "output_exists":
            output_exists,

        "direct_slice_succeeded":
            succeeded,

        "stdout_tail":
            proc.stdout[-4000:],

        "stderr_tail":
            proc.stderr[-4000:],

        "network_used":
            False,

        "printer_command_sent":
            False,
    }


def classify_ab_result(
    *,
    override_exit: int,
    direct_result: dict[str, Any],
) -> dict[str, Any]:
    override = classify_bambu_exit_code(
        override_exit
    )

    direct_exit = direct_result[
        "exit"
    ]

    direct_ok = bool(
        direct_result.get(
            "direct_slice_succeeded"
        )
    )

    if (
        override["signed"] == -104
        and direct_ok
    ):
        diagnosis = (
            "override_profile_path_regression"
        )

        interpretation = (
            "The assembled project can slice by "
            "itself, but the profile-override "
            "slice path fails. Investigate the "
            "secondary --load-settings/"
            "--load-filaments path."
        )

    elif (
        override["signed"] == -104
        and direct_exit["signed"] == -104
    ):
        diagnosis = (
            "project_layout_or_generated_path_outside"
        )

        interpretation = (
            "The project fails even without "
            "secondary profile overrides. "
            "Investigate object placement, "
            "printable-area constraints, "
            "prime/wipe tower, brim, skirt, "
            "or other generated paths."
        )

    elif direct_ok:
        diagnosis = (
            "direct_project_slice_passed"
        )

        interpretation = (
            "Direct project slicing passed."
        )

    else:
        diagnosis = (
            "different_direct_slice_failure"
        )

        interpretation = (
            "Direct slicing failed with a "
            "different Bambu CLI exit code. "
            "Use the recorded symbol as the "
            "next root-cause gate."
        )

    return {
        "diagnosis":
            diagnosis,

        "interpretation":
            interpretation,

        "override_exit":
            override,

        "direct_exit":
            direct_exit,

        "direct_slice_succeeded":
            direct_ok,
    }


def write_report(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path = (
        path
        .expanduser()
        .resolve()
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def cli_main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reproducible Bambu multimaterial "
            "slice A/B preflight."
        )
    )

    parser.add_argument(
        "--studio",
        required=True,
    )

    parser.add_argument(
        "--project",
        required=True,
    )

    parser.add_argument(
        "--direct-output",
        required=True,
    )

    parser.add_argument(
        "--observed-override-exit",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args(
        argv
    )

    try:
        direct = run_direct_project_slice(
            studio_exe=Path(
                args.studio
            ),
            project_path=Path(
                args.project
            ),
            output_path=Path(
                args.direct_output
            ),
        )

        ab = classify_ab_result(
            override_exit=
                args.observed_override_exit,
            direct_result=direct,
        )

        report = {
            "schema_version":
                "1.0.0",

            "module":
                "M4",

            "stage":
                "multimaterial_slice_preflight",

            "status":
                "diagnostic_complete",

            "direct":
                direct,

            "ab_result":
                ab,

            "policy": {
                "network_used":
                    False,

                "printer_command_sent":
                    False,

                "ams_mapping_used":
                    False,

                "project_modified":
                    False,
            },
        }

        write_report(
            Path(args.report),
            report,
        )

        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            )
        )

        # A slicing failure is an observed diagnostic
        # result, not a failure of this diagnostic tool.
        return 0

    except Exception as exc:
        print(
            json.dumps(
                {
                    "status":
                        "diagnostic_error",

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
