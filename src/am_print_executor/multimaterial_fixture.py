from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from am_print_executor.bambu_headless_cli import (
    run_bambu_cli,
)


DEFAULT_GRAY = "#A6A9AA"
DEFAULT_YELLOW = "#F4EE2A"


class MultiMaterialFixtureError(RuntimeError):
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


def _write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_name(
        path.name + ".tmp"
    )

    tmp.write_text(
        text,
        encoding=encoding,
    )

    tmp.replace(path)


def _write_box_stl(
    path: Path,
    *,
    x0: float,
    y0: float,
    z0: float,
    x1: float,
    y1: float,
    z1: float,
    name: str,
) -> None:
    vertices = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]

    triangles = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]

    lines = [
        f"solid {name}"
    ]

    for a, b, c in triangles:
        lines.append(
            "  facet normal 0 0 0"
        )
        lines.append(
            "    outer loop"
        )

        for index in (a, b, c):
            x, y, z = vertices[index]

            lines.append(
                f"      vertex {x:g} {y:g} {z:g}"
            )

        lines.append(
            "    endloop"
        )
        lines.append(
            "  endfacet"
        )

    lines.append(
        f"endsolid {name}"
    )

    _write_text(
        path,
        "\n".join(lines) + "\n",
        encoding="ascii",
    )


def _load_profile(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MultiMaterialFixtureError(
            f"Filament profile missing: {path}"
        )

    try:
        obj = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except json.JSONDecodeError as exc:
        raise MultiMaterialFixtureError(
            f"Invalid filament JSON: {path}"
        ) from exc

    if not isinstance(obj, dict):
        raise MultiMaterialFixtureError(
            "Filament profile root must be a JSON object."
        )

    return obj


def discover_source_filament(
    task_dir: Path,
) -> Path:
    task_dir = task_dir.resolve()

    candidates = sorted(
        p
        for p in task_dir.rglob(
            "filament_*.json"
        )
        if (
            ".nl_am_bambu_cli_profiles"
            in p.parts
        )
    )

    if not candidates:
        raise MultiMaterialFixtureError(
            "No resolved CLI filament profile "
            f"found under {task_dir}"
        )

    hashes: dict[str, list[Path]] = {}

    for path in candidates:
        hashes.setdefault(
            _sha256(path),
            [],
        ).append(path)

    if len(hashes) != 1:
        pretty = "\n".join(
            str(p)
            for p in candidates
        )

        raise MultiMaterialFixtureError(
            "Multiple different resolved filament "
            "profiles were found. Pass "
            "--source-profile explicitly.\n"
            + pretty
        )

    return candidates[0]


def probe_studio_cli(
    studio_exe: Path,
) -> dict[str, Any]:
    studio_exe = studio_exe.resolve()

    if not studio_exe.is_file():
        raise MultiMaterialFixtureError(
            f"Bambu Studio missing: {studio_exe}"
        )

    cli_result = run_bambu_cli(
        [
            str(studio_exe),
            "--help",
        ],
        timeout=60,
    )

    text = (
        cli_result.stdout
        + "\n"
        + cli_result.stderr
    )

    flags = {
        "--load-settings":
            "--load-settings" in text,
        "--load-filaments":
            "--load-filaments" in text,
        "--slice":
            "--slice" in text,
        "--export-3mf":
            "--export-3mf" in text,
        "--assemble":
            "--assemble" in text,
        "--load-filament-ids":
            "--load-filament-ids" in text,
        "--allow-multicolor-oneplate":
            "--allow-multicolor-oneplate"
            in text,
    }

    documented_core_ready = all(
        flags[name]
        for name in (
            "--load-settings",
            "--load-filaments",
            "--slice",
            "--export-3mf",
        )
    )

    multicolor_assembly_ready = all(
        flags[name]
        for name in (
            "--assemble",
            "--load-filament-ids",
            "--allow-multicolor-oneplate",
        )
    )

    return {
        "return_code": cli_result.raw_exit,
        "flags": flags,
        "documented_core_ready":
            documented_core_ready,
        "multicolor_assembly_ready":
            multicolor_assembly_ready,
    }


def prepare_fixture(
    *,
    output_dir: Path,
    source_profile: Path,
    studio_exe: Path | None = None,
    gray_colour: str = DEFAULT_GRAY,
    yellow_colour: str = DEFAULT_YELLOW,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    source_profile = (
        source_profile.resolve()
    )

    source = _load_profile(
        source_profile
    )

    gray_stl = (
        output_dir
        / "gray_base.stl"
    )

    yellow_stl = (
        output_dir
        / "yellow_top.stl"
    )

    _write_box_stl(
        gray_stl,
        x0=-15,
        y0=-15,
        z0=0,
        x1=15,
        y1=15,
        z1=4,
        name="gray_base",
    )

    _write_box_stl(
        yellow_stl,
        x0=-10,
        y0=-10,
        z0=4,
        x1=10,
        y1=10,
        z1=8,
        name="yellow_top",
    )

    gray = copy.deepcopy(
        source
    )

    yellow = copy.deepcopy(
        source
    )

    gray["name"] = (
        "NL-AM AMS Fixture Gray PLA"
    )

    yellow["name"] = (
        "NL-AM AMS Fixture Yellow PLA"
    )

    gray["filament_colour"] = [
        gray_colour
    ]

    yellow["filament_colour"] = [
        yellow_colour
    ]

    gray_profile = (
        output_dir
        / "filament_gray_full.json"
    )

    yellow_profile = (
        output_dir
        / "filament_yellow_full.json"
    )

    _write_text(
        gray_profile,
        json.dumps(
            gray,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    _write_text(
        yellow_profile,
        json.dumps(
            yellow,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    capability = None

    if studio_exe is not None:
        capability = probe_studio_cli(
            studio_exe
        )

    manifest = {
        "schema_version": "1.0.0",
        "module": "M4",
        "stage":
            "permanent_multimaterial_fixture",
        "status":
            "multimaterial_fixture_prepared",
        "source_filament_profile": {
            "path": str(
                source_profile
            ),
            "sha256": _sha256(
                source_profile
            ),
        },
        "objects": [
            {
                "path": str(
                    gray_stl
                ),
                "project_filament_id": 1,
                "expected_tool_id": 0,
                "colour": gray_colour,
                "role": "base",
            },
            {
                "path": str(
                    yellow_stl
                ),
                "project_filament_id": 2,
                "expected_tool_id": 1,
                "colour": yellow_colour,
                "role": "top",
            },
        ],
        "filament_profiles": [
            str(gray_profile),
            str(yellow_profile),
        ],
        "expected_project_filament_count": 2,
        "expected_tool_ids": [
            0,
            1,
        ],
        "expected_wire_mapping": [
            0,
            3,
            -1,
            -1,
            -1,
        ],
        "bambu_cli_capability":
            capability,
        "policy": {
            "network_used": False,
            "printer_connected": False,
            "print_command_sent": False,
            "access_code_requested": False,
            "historical_print_artifact_reused":
                False,
        },
    }

    manifest_path = (
        output_dir
        / "fixture_manifest.json"
    )

    _write_text(
        manifest_path,
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    manifest["manifest_path"] = str(
        manifest_path
    )

    return manifest


def cli_main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the permanent NL-AM "
            "two-filament AMS validation fixture."
        )
    )

    parser.add_argument(
        "--output-dir",
        required=True,
    )

    source = parser.add_mutually_exclusive_group(
        required=True
    )

    source.add_argument(
        "--source-profile",
    )

    source.add_argument(
        "--source-task-dir",
    )

    parser.add_argument(
        "--studio",
    )

    parser.add_argument(
        "--gray-colour",
        default=DEFAULT_GRAY,
    )

    parser.add_argument(
        "--yellow-colour",
        default=DEFAULT_YELLOW,
    )

    args = parser.parse_args(
        argv
    )

    try:
        if args.source_profile:
            source_profile = Path(
                args.source_profile
            )
        else:
            source_profile = (
                discover_source_filament(
                    Path(
                        args.source_task_dir
                    )
                )
            )

        result = prepare_fixture(
            output_dir=Path(
                args.output_dir
            ),
            source_profile=source_profile,
            studio_exe=(
                Path(args.studio)
                if args.studio
                else None
            ),
            gray_colour=args.gray_colour,
            yellow_colour=args.yellow_colour,
        )

    except MultiMaterialFixtureError as exc:
        print(
            json.dumps(
                {
                    "status":
                        "multimaterial_fixture_blocked",
                    "error": str(exc),
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
