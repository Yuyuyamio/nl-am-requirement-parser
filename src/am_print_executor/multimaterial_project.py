from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import zipfile
import html
import os
import re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

from pathlib import Path
from typing import Any

from .multimaterial_job import (
    MultiMaterialJobError,
    load_multimaterial_job,
)


class MultiMaterialProjectError(RuntimeError):
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


def _signed_return_code(
    value: int,
) -> int:
    return (
        value - 2**32
        if value >= 2**31
        else value
    )


def discover_resolved_profile(
    task_dir: Path,
    prefix: str,
) -> Path:
    task_dir = (
        task_dir
        .expanduser()
        .resolve()
    )

    candidates = sorted(
        p
        for p in task_dir.rglob(
            f"{prefix}_*.json"
        )
        if (
            ".nl_am_bambu_cli_profiles"
            in p.parts
        )
    )

    if not candidates:
        raise MultiMaterialProjectError(
            f"No resolved {prefix} profile "
            f"found under {task_dir}"
        )

    by_hash: dict[
        str,
        list[Path],
    ] = {}

    for path in candidates:
        by_hash.setdefault(
            _sha256(path),
            [],
        ).append(path)

    if len(by_hash) != 1:
        raise MultiMaterialProjectError(
            f"Multiple distinct {prefix} "
            "profiles found. Runtime "
            "selection is ambiguous."
        )

    return candidates[0].resolve()


def probe_cli_help(
    studio_exe: Path,
) -> dict[str, Any]:
    studio_exe = (
        studio_exe
        .expanduser()
        .resolve()
    )

    creationflags = 0

    if (
        os.name == "nt"
        and hasattr(
            subprocess,
            "CREATE_NO_WINDOW",
        )
    ):
        creationflags = (
            subprocess.CREATE_NO_WINDOW
        )

    try:
        proc = subprocess.run(
            [
                str(studio_exe),
                "--help",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=
                creationflags,
        )

    except Exception as exc:
        return {
            "available": False,
            "authoritative": False,
            "error":
                f"{type(exc).__name__}: {exc}",
        }

    text = (
        (proc.stdout or "")
        + "\n"
        + (proc.stderr or "")
    )

    flags = (
        "--load-settings",
        "--curr-bed-type",
        "--load-filaments",
        "--load-assemble-list",
        "--export-3mf",
    )

    return {
        "available": True,
        "authoritative": False,
        "returncode_raw":
            int(proc.returncode),
        "help_text_length":
            len(text),
        "flags": {
            flag: flag in text
            for flag in flags
        },
    }


def build_assemble_payload(
    job_manifest: Path,
) -> dict[str, Any]:
    try:
        job = load_multimaterial_job(
            job_manifest
        )
    except MultiMaterialJobError as exc:
        raise MultiMaterialProjectError(
            str(exc)
        ) from exc

    rows = []

    for obj in job["objects"]:
        positions = obj[
            "positions_mm"
        ]

        rows.append(
            {
                "path": (
                    Path(
                        obj["path"]
                    )
                    .resolve()
                    .as_posix()
                ),

                "count":
                    obj["count"],

                "filaments":
                    obj[
                        "project_filament_ids"
                    ],

                "assemble_index":
                    obj[
                        "assemble_index"
                    ],

                "pos_x": [
                    pos[0]
                    for pos in positions
                ],

                "pos_y": [
                    pos[1]
                    for pos in positions
                ],

                "pos_z": [
                    pos[2]
                    for pos in positions
                ],
            }
        )

    return {
        "plates": [
            {
                "plate_name":
                    "NL-AM MultiMaterial",

                "need_arrange":
                    False,

                "objects":
                    rows,
            }
        ]
    }



_MODEL_SETTINGS_MEMBER = "Metadata/model_settings.config"

_METADATA_LINE_RE = re.compile(
    r'^(?P<indent>\s*)'
    r'<metadata\s+'
    r'key="(?P<key>.*?)"\s+'
    r'value="(?P<value>.*)"\s*/>\s*$'
)


def _normalise_bambu_metadata_line(
    line: str,
) -> str:
    """
    Convert one Bambu model_settings metadata line into
    well-formed XML without changing its semantic value.

    This is intentionally generic: no printer names,
    materials, model names, or current dog fixture values
    are hardcoded here.
    """
    match = _METADATA_LINE_RE.match(line)

    if match is None:
        return line

    indent = match.group("indent")

    # First decode any already-valid entities, then emit one
    # canonical XML-escaped representation.  This prevents
    # double escaping of &quot;, &amp;, etc.
    key = html.unescape(
        match.group("key")
    )

    value = html.unescape(
        match.group("value")
    )

    return (
        indent
        + "<metadata key="
        + quoteattr(key)
        + " value="
        + quoteattr(value)
        + "/>"
    )


def repair_bambu_model_settings_xml(
    project_path: Path,
) -> dict[str, object]:
    """
    Validate Metadata/model_settings.config.

    Bambu Studio CLI can emit a project whose ZIP is valid
    while model_settings.config contains unescaped XML
    attribute characters.  Such a project cannot be opened
    again by Bambu Studio and later fails with CLI_DATA_FILE_ERROR.

    If XML is already valid this function is byte-preserving.
    If it is invalid, only model_settings.config is rewritten.
    Every other 3MF member is preserved.
    """
    project_path = (
        Path(project_path)
        .expanduser()
        .resolve()
    )

    if not project_path.is_file():
        raise RuntimeError(
            "Project missing before XML validation: "
            + str(project_path)
        )

    if not zipfile.is_zipfile(project_path):
        raise RuntimeError(
            "Project is not a valid ZIP/3MF: "
            + str(project_path)
        )

    with zipfile.ZipFile(
        project_path,
        "r",
    ) as source:

        names = source.namelist()

        if _MODEL_SETTINGS_MEMBER not in names:
            raise RuntimeError(
                "Project is missing "
                + _MODEL_SETTINGS_MEMBER
            )

        original_raw = source.read(
            _MODEL_SETTINGS_MEMBER
        )

    try:
        ET.fromstring(original_raw)

        return {
            "status":
                "model_settings_xml_valid",
            "repaired":
                False,
            "member":
                _MODEL_SETTINGS_MEMBER,
        }

    except ET.ParseError as initial_error:
        initial_error_text = str(initial_error)

    decoded = original_raw.decode(
        "utf-8-sig",
        errors="strict",
    )

    had_final_newline = decoded.endswith(
        ("\n", "\r")
    )

    normalised_lines = [
        _normalise_bambu_metadata_line(line)
        for line in decoded.splitlines()
    ]

    repaired_text = "\n".join(
        normalised_lines
    )

    if had_final_newline:
        repaired_text += "\n"

    repaired_raw = repaired_text.encode(
        "utf-8"
    )

    # A repair is never accepted unless a real XML parser
    # accepts the whole resulting document.
    try:
        ET.fromstring(repaired_raw)

    except ET.ParseError as repaired_error:
        raise RuntimeError(
            "model_settings.config remained invalid after "
            "canonical XML repair. "
            f"before={initial_error_text!r}; "
            f"after={str(repaired_error)!r}"
        ) from repaired_error

    tmp_path = project_path.with_name(
        project_path.name
        + ".model_settings_xml_repair.tmp"
    )

    if tmp_path.exists():
        tmp_path.unlink()

    try:
        with zipfile.ZipFile(
            project_path,
            "r",
        ) as source, zipfile.ZipFile(
            tmp_path,
            "w",
        ) as target:

            for info in source.infolist():
                data = source.read(
                    info.filename
                )

                if (
                    info.filename
                    == _MODEL_SETTINGS_MEMBER
                ):
                    data = repaired_raw

                # Reuse the original ZipInfo so member
                # names, timestamps, compression type and
                # attributes remain intact.
                target.writestr(
                    info,
                    data,
                )

        os.replace(
            tmp_path,
            project_path,
        )

    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    # Verify the bytes actually written back into the 3MF.
    with zipfile.ZipFile(
        project_path,
        "r",
    ) as verify_zip:

        verify_raw = verify_zip.read(
            _MODEL_SETTINGS_MEMBER
        )

        ET.fromstring(
            verify_raw
        )

    return {
        "status":
            "model_settings_xml_repaired",
        "repaired":
            True,
        "member":
            _MODEL_SETTINGS_MEMBER,
        "initial_parse_error":
            initial_error_text,
        "original_bytes":
            len(original_raw),
        "repaired_bytes":
            len(repaired_raw),
    }


def inspect_project_3mf(
    path: Path,
    *,
    expected_filament_count: int | None = None,
    expected_bed_type: str | None = None,
) -> dict[str, Any]:
    path = (
        path
        .expanduser()
        .resolve()
    )

    if not path.is_file():
        raise MultiMaterialProjectError(
            f"Project 3MF missing: {path}"
        )

    if not zipfile.is_zipfile(
        path
    ):
        raise MultiMaterialProjectError(
            f"Invalid 3MF ZIP: {path}"
        )

    with zipfile.ZipFile(
        path,
        "r",
    ) as zf:
        bad = zf.testzip()

        if bad is not None:
            raise MultiMaterialProjectError(
                f"Corrupt 3MF member: {bad}"
            )

        name = (
            "Metadata/"
            "project_settings.config"
        )

        if name not in zf.namelist():
            raise MultiMaterialProjectError(
                "Missing project_settings.config."
            )

        try:
            settings = json.loads(
                zf.read(name).decode(
                    "utf-8-sig"
                )
            )
        except Exception as exc:
            raise MultiMaterialProjectError(
                "Cannot parse project settings."
            ) from exc
    bed_type = settings.get(
        "curr_bed_type"
    )

    if expected_bed_type is not None:
        expected_bed_type = (
            expected_bed_type.strip()
        )

        if not expected_bed_type:
            raise MultiMaterialProjectError(
                "expected_bed_type must be "
                "a non-empty string."
            )

        if bed_type != expected_bed_type:
            raise MultiMaterialProjectError(
                "Project build plate mismatch: "
                f"expected={expected_bed_type!r}, "
                f"actual={bed_type!r}"
            )


    ids = settings.get(
        "filament_settings_id"
    )

    colours = settings.get(
        "filament_colour"
    )

    if not isinstance(ids, list):
        raise MultiMaterialProjectError(
            "filament_settings_id is not a list."
        )

    if expected_filament_count is None:
        expected_filament_count = len(ids)

    elif len(ids) != expected_filament_count:
        raise MultiMaterialProjectError(
            "Project filament count mismatch: "
            f"expected={expected_filament_count}, "
            f"actual={len(ids)}"
        )

    if not isinstance(
        colours,
        list,
    ):
        raise MultiMaterialProjectError(
            "filament_colour is not a list."
        )

    if (
        len(colours)
        != expected_filament_count
    ):
        raise MultiMaterialProjectError(
            "Project filament colour count mismatch."
        )

    return {
        "path":
            str(path),

        "sha256":
            _sha256(path),

        "size_bytes":
            path.stat().st_size,

        "project_filament_count":
            len(ids),

        "filament_settings_id":
            ids,

        "filament_colours":
            colours,
        "curr_bed_type":
            bed_type,
    }


def assemble_project(
    *,
    studio_exe: Path,
    job_manifest: Path | None = None,
    source_task_dir: Path,
    output_path: Path,
    build_plate: str,
    fixture_manifest: Path | None = None,
) -> dict[str, Any]:
    studio_exe = (
        studio_exe
        .expanduser()
        .resolve()
    )

    output_path = (
        output_path
        .expanduser()
        .resolve()
    )

    if (
        not isinstance(build_plate, str)
        or not build_plate.strip()
    ):
        raise MultiMaterialProjectError(
            "build_plate must be a "
            "non-empty string."
        )

    build_plate = build_plate.strip()


    if not studio_exe.is_file():
        raise MultiMaterialProjectError(
            f"Bambu Studio missing: "
            f"{studio_exe}"
        )

    if (
        (job_manifest is None)
        == (fixture_manifest is None)
    ):
        raise MultiMaterialProjectError(
            "Exactly one of job_manifest or "
            "fixture_manifest must be supplied."
        )

    manifest_path = (
        job_manifest
        if job_manifest is not None
        else fixture_manifest
    )

    assert manifest_path is not None

    try:
        job = load_multimaterial_job(
            manifest_path
        )
    except MultiMaterialJobError as exc:
        raise MultiMaterialProjectError(
            str(exc)
        ) from exc

    machine = discover_resolved_profile(
        source_task_dir,
        "machine",
    )

    process = discover_resolved_profile(
        source_task_dir,
        "process",
    )

    filament_paths = [
        Path(row["path"])
        for row
        in job["filament_profiles"]
    ]

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    assemble_json = (
        output_path.parent
        / (
            output_path.stem
            + ".assemble.json"
        )
    )

    payload = build_assemble_payload(
        manifest_path
    )

    assemble_json.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    if output_path.exists():
        output_path.unlink()

    command = [
        str(studio_exe),

        # Printability policy:
        # Bambu Studio itself chooses the printable orientation.
        "--orient",
        "1",

        "--arrange",
        "1",

        "--ensure-on-bed",

        # Do not rely on the process profile's support default.
        "--enable-support=1",
        "--support-type=tree(auto)",

        # Keep Bambu's own manufacturability detectors active.
        "--detect-floating-vertical-shell=1",
        "--detect-overhang-wall=1",
        "--bridge-no-support=0",

        "--load-settings",
        f"{machine};{process}",


        "--curr-bed-type",
        build_plate,
        "--load-filaments",
        ";".join(
            str(path)
            for path
            in filament_paths
        ),

        "--load-assemble-list",
        str(assemble_json),

        "--debug",
        "5",

        "--export-3mf",
        str(output_path),
    ]

    help_probe = probe_cli_help(
        studio_exe
    )

    creationflags = 0

    if (
        os.name == "nt"
        and hasattr(
            subprocess,
            "CREATE_NO_WINDOW",
        )
    ):
        creationflags = (
            subprocess.CREATE_NO_WINDOW
        )

    started = time.time()

    try:
        proc = subprocess.run(
            command,
            cwd=str(
                output_path.parent
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=
                creationflags,
        )

    except subprocess.TimeoutExpired as exc:
        raise MultiMaterialProjectError(
            "Bambu Studio assembly timed out."
        ) from exc

    raw_rc = int(
        proc.returncode
    )

    signed_rc = (
        _signed_return_code(
            raw_rc
        )
    )

    if signed_rc != 0:
        raise MultiMaterialProjectError(
            "Bambu Studio assembly failed.\n"
            f"returncode_raw={raw_rc}\n"
            f"returncode_signed={signed_rc}\n"
            f"stdout_tail={proc.stdout[-3000:]}\n"
            f"stderr_tail={proc.stderr[-3000:]}"
        )

    if not output_path.is_file():
        raise MultiMaterialProjectError(
            "Process returned success but exact "
            "requested output does not exist."
        )

    # A valid ZIP is not sufficient.  Bambu Studio can
    # emit malformed Metadata/model_settings.config.
    # Repair/validate that member before the project is
    # accepted by the canonical multimaterial pipeline.
    xml_repair = repair_bambu_model_settings_xml(
        output_path
    )

    from am_print_executor.bambu_project_xy_guard import (
        ProjectXYPlacementError,
        ensure_project_xy_on_bed,
    )

    try:
        xy_placement = ensure_project_xy_on_bed(
            output_path
        )
    except ProjectXYPlacementError as exc:
        raise MultiMaterialProjectError(
            "Bambu Auto Orient/Arrange produced "
            "invalid XY placement: "
            + str(exc)
        ) from exc

    inspection = inspect_project_3mf(
        output_path,
        expected_filament_count=
            job[
                "project_filament_count"
            ],
        expected_bed_type=build_plate,

    )

    return {
        "schema_version":
            "2.0.0",

        "module":
            "M4",

        "stage":
            "generic_multimaterial_project",

        "status":
            "multimaterial_project_assembled",

        "pipeline":
            "bambu_generic_assemble_v2",

        "job": {
            "source_manifest":
                job[
                    "source_manifest"
                ],

            "source_kind":
                job[
                    "source_kind"
                ],

            "object_count":
                len(
                    job["objects"]
                ),

            "project_filament_count":
                job[
                    "project_filament_count"
                ],

            "used_filament_ids":
                job[
                    "used_filament_ids"
                ],
        },

        "project":
            inspection,


        "build_plate": {
            "requested": build_plate,
            "resolved": inspection[
                "curr_bed_type"
            ],
            "validated": True,
        },
        "command":
            command,

        "returncode_raw":
            raw_rc,

        "returncode_signed":
            signed_rc,

        "elapsed_seconds":
            round(
                time.time()
                - started,
                3,
            ),

        # Backward-compatible stable result contract.
        # Runtime artifact validation remains authoritative.
        "capability_validation": {
            "authoritative_method":
                "runtime_artifact_validation",

            "runtime_validated":
                True,

            "help_probe":
                help_probe,

            "required_runtime_results": {
                "process_exit_zero":
                    signed_rc == 0,

                "exact_output_exists":
                    output_path.is_file(),

                "valid_3mf_zip":
                    True,

                "project_filament_count":
                    inspection[
                        "project_filament_count"
                    ],
            },
        },

        # New generic v2 field.
        "help_probe":
            help_probe,

        "policy": {
            "network_used":
                False,

            "printer_connected":
                False,

            "print_command_sent":
                False,

            # Canonical generic v2 name.
            "help_used_as_gate":
                False,

            # Backward-compatible alias retained for
            # callers/tests from the AMS-specific API.
            "help_text_used_as_gate":
                False,

            "stale_output_accepted":
                False,

            "input_paths_hardcoded":
                False,

            "request_id_hardcoded":
                False,

            "filament_count_hardcoded":
                False,
        },

        "stdout_tail":
            proc.stdout[-2000:],

        "stderr_tail":
            proc.stderr[-2000:],
    }


def cli_main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generic N-material "
            "Bambu project assembler."
        )
    )

    parser.add_argument(
        "--studio",
        required=True,
    )

    manifest_group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    manifest_group.add_argument(
        "--job-manifest",
    )

    # Backward compatibility only.
    manifest_group.add_argument(
        "--fixture-manifest",
    )

    parser.add_argument(
        "--source-task-dir",
        required=True,
    )

    parser.add_argument(
        "--output",
        required=True,
    )


    parser.add_argument(
        "--build-plate",
        required=True,
        help=(
            "Exact Bambu Studio curr_bed_type "
            "value. No implicit default is allowed."
        ),
    )

    args = parser.parse_args(
        argv
    )

    manifest = (
        args.job_manifest
        or args.fixture_manifest
    )

    try:
        result = assemble_project(
            studio_exe=Path(
                args.studio
            ),

            job_manifest=Path(
                manifest
            ),

            source_task_dir=Path(
                args.source_task_dir
            ),


            build_plate=args.build_plate,

            output_path=Path(
                args.output
            ),
        )

    except MultiMaterialProjectError as exc:
        print(
            json.dumps(
                {
                    "status":
                        "multimaterial_project_blocked",
                    "error":
                        str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        return 2

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
