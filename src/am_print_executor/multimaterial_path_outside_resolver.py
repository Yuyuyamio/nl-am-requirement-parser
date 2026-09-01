from __future__ import annotations

import argparse
import hashlib
import json
import zipfile

from pathlib import Path
from typing import Any

from am_print_executor.multimaterial_slice_preflight import (
    run_direct_project_slice,
)


class PathOutsideResolverError(RuntimeError):
    pass


PROJECT_SETTINGS = (
    "Metadata/project_settings.config"
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def clone_project_with_setting_overrides(
    *,
    source: Path,
    destination: Path,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()

    if not source.is_file():
        raise PathOutsideResolverError(
            f"Source project missing: {source}"
        )

    if not zipfile.is_zipfile(source):
        raise PathOutsideResolverError(
            "Source project is not a valid 3MF ZIP."
        )

    if source == destination:
        raise PathOutsideResolverError(
            "Diagnostic variant must not overwrite "
            "the source project."
        )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if destination.exists():
        destination.unlink()

    source_sha_before = _sha256(source)

    settings_seen = False
    changed = {}

    with zipfile.ZipFile(
        source,
        "r",
    ) as src, zipfile.ZipFile(
        destination,
        "w",
    ) as dst:

        for info in src.infolist():
            data = src.read(info.filename)

            if info.filename == PROJECT_SETTINGS:
                settings_seen = True

                try:
                    settings = json.loads(
                        data.decode(
                            "utf-8-sig"
                        )
                    )
                except Exception as exc:
                    raise PathOutsideResolverError(
                        "Cannot parse project settings."
                    ) from exc

                if not isinstance(
                    settings,
                    dict,
                ):
                    raise PathOutsideResolverError(
                        "Project settings root must "
                        "be an object."
                    )

                for key, value in (
                    overrides.items()
                ):
                    changed[key] = {
                        "before":
                            settings.get(key),
                        "after":
                            value,
                    }

                    settings[key] = value

                data = (
                    json.dumps(
                        settings,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")

            dst.writestr(
                info,
                data,
            )

    if not settings_seen:
        raise PathOutsideResolverError(
            "Project has no project_settings.config."
        )

    if not zipfile.is_zipfile(
        destination
    ):
        raise PathOutsideResolverError(
            "Generated diagnostic variant "
            "is not a valid ZIP."
        )

    source_sha_after = _sha256(source)

    if source_sha_after != source_sha_before:
        raise PathOutsideResolverError(
            "Source project changed while creating "
            "diagnostic variant."
        )

    return {
        "source":
            str(source),

        "source_sha256":
            source_sha_before,

        "variant":
            str(destination),

        "variant_sha256":
            _sha256(destination),

        "source_modified":
            False,

        "overrides":
            changed,
    }


def classify_prime_tower_ab(
    direct_result: dict[str, Any],
) -> dict[str, Any]:

    signed = direct_result[
        "exit"
    ]["signed"]

    succeeded = bool(
        direct_result.get(
            "direct_slice_succeeded"
        )
    )

    if succeeded:
        diagnosis = (
            "prime_tower_path_implicated"
        )

        interpretation = (
            "The original project failed with "
            "CLI_GCODE_PATH_OUTSIDE, while the "
            "same project with prime tower "
            "disabled sliced successfully. "
            "Prime/wipe tower generated path is "
            "therefore implicated."
        )

    elif signed == -104:
        diagnosis = (
            "prime_tower_not_sufficient"
        )

        interpretation = (
            "Disabling the prime tower did not "
            "remove CLI_GCODE_PATH_OUTSIDE. "
            "Investigate model placement, object "
            "brim/skirt, exclusion areas, or other "
            "generated paths."
        )

    else:
        diagnosis = (
            "prime_tower_variant_different_failure"
        )

        interpretation = (
            "Disabling the prime tower changed "
            "the failure mode. Use the returned "
            "Bambu CLI exit symbol as the next "
            "diagnostic gate."
        )

    return {
        "diagnosis":
            diagnosis,

        "interpretation":
            interpretation,

        "variant_exit":
            direct_result["exit"],

        "variant_slice_succeeded":
            succeeded,
    }


def diagnose_prime_tower(
    *,
    studio_exe: Path,
    project_path: Path,
    work_dir: Path,
) -> dict[str, Any]:

    work_dir = (
        work_dir
        .expanduser()
        .resolve()
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    variant = (
        work_dir
        / "prime_tower_disabled.project.3mf"
    )

    output = (
        work_dir
        / "prime_tower_disabled.gcode.3mf"
    )

    variant_info = (
        clone_project_with_setting_overrides(
            source=project_path,
            destination=variant,
            overrides={
                "enable_prime_tower": "0",
            },
        )
    )

    direct = run_direct_project_slice(
        studio_exe=studio_exe,
        project_path=variant,
        output_path=output,
    )

    result = classify_prime_tower_ab(
        direct
    )

    return {
        "schema_version":
            "1.0.0",

        "module":
            "M4",

        "stage":
            "path_outside_prime_tower_ab",

        "status":
            "diagnostic_complete",

        "variant":
            variant_info,

        "slice_result":
            direct,

        "ab_result":
            result,

        "policy": {
            "source_project_modified":
                False,

            "diagnostic_variant_only":
                True,

            "network_used":
                False,

            "printer_command_sent":
                False,

            "ams_mapping_used":
                False,

            "diagnostic_variant_allowed_for_print":
                False,
        },
    }


def cli_main(
    argv: list[str] | None = None,
) -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--studio",
        required=True,
    )

    parser.add_argument(
        "--project",
        required=True,
    )

    parser.add_argument(
        "--work-dir",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args(argv)

    try:
        result = diagnose_prime_tower(
            studio_exe=Path(
                args.studio
            ),
            project_path=Path(
                args.project
            ),
            work_dir=Path(
                args.work_dir
            ),
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
