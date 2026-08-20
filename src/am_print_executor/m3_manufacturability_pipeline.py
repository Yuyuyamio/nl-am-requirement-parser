from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from am_print_executor.bambu_headless_cli import (
    run_bambu_cli,
)
from am_print_executor.printable_orientation import (
    orient_for_printing,
)
from am_print_executor.multimaterial_project import (
    repair_bambu_model_settings_xml,
)
from am_print_executor.gcode_support_continuity import (
    inspect_gcode3mf_support_continuity,
)


class M3ManufacturabilityError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def build_conservative_process_profile(
    *,
    source_profile: Path,
    output_profile: Path,
) -> dict[str, Any]:
    """
    Project-wide conservative manufacturability policy.

    This is deliberately model-agnostic.
    No cat/dog/fixture-specific values are allowed here.
    """
    obj = json.loads(
        Path(source_profile).read_text(
            encoding="utf-8-sig"
        )
    )

    obj.update({
        "name":
            "NL-AM Conservative Printable Process",

        "enable_support":
            "1",

        # Conservative default:
        # continuous support rather than sparse tree support.
        "support_type":
            "normal(auto)",

        # Allow support to grow from model geometry,
        # not only directly from the bed.
        "support_on_build_plate_only":
            "0",

        "support_critical_regions_only":
            "0",

        "support_remove_small_overhang":
            "0",

        "support_threshold_angle":
            "30",

        "detect_floating_vertical_shell":
            "1",

        "detect_overhang_wall":
            "1",

        "bridge_no_support":
            "0",
    })

    output_profile.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_profile.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return obj


def inspect_gcode_3mf(
    path: Path,
    *,
    support_required: bool,
) -> dict[str, Any]:

    path = Path(path).resolve()

    blockers: list[str] = []

    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "blockers": blockers,
    }

    # Permanent fix:
    # missing artifact is an ordinary BLOCK,
    # never an uncaught ZipFile traceback.
    if not path.is_file():
        blockers.append(
            "slice_artifact_missing"
        )

        result["status"] = "blocked"

        return result

    result["size_bytes"] = (
        path.stat().st_size
    )

    result["sha256"] = _sha256(path)

    if not zipfile.is_zipfile(path):
        blockers.append(
            "artifact_not_valid_3mf_zip"
        )

        result["status"] = "blocked"

        return result

    with zipfile.ZipFile(path, "r") as z:
        bad = z.testzip()

        if bad is not None:
            blockers.append(
                "corrupt_zip_member:" + bad
            )

        names = z.namelist()

        gcode_entries = sorted(
            name
            for name in names
            if re.fullmatch(
                r"Metadata/plate_\d+\.gcode",
                name,
            )
        )

        result["gcode_entries"] = (
            gcode_entries
        )

        result["plate_count"] = len(
            gcode_entries
        )

        if len(gcode_entries) != 1:
            blockers.append(
                "expected_exactly_one_plate"
            )

        xml_members = [
            "3D/3dmodel.model",
            "Metadata/model_settings.config",
        ]

        xml_results = {}

        for name in xml_members:
            if name not in names:
                xml_results[name] = "missing"

                blockers.append(
                    "missing_member:" + name
                )

                continue

            try:
                ET.fromstring(
                    z.read(name)
                )

                xml_results[name] = "pass"

            except Exception as exc:
                xml_results[name] = (
                    "fail:" + str(exc)
                )

                blockers.append(
                    "invalid_xml:" + name
                )

        result["xml"] = xml_results

        settings = {}

        settings_name = (
            "Metadata/project_settings.config"
        )

        if settings_name not in names:
            blockers.append(
                "project_settings_missing"
            )

        else:
            try:
                settings = json.loads(
                    z.read(
                        settings_name
                    ).decode(
                        "utf-8-sig"
                    )
                )

            except Exception as exc:
                blockers.append(
                    "project_settings_invalid:"
                    + str(exc)
                )

        support = {
            "enable_support":
                str(
                    settings.get(
                        "enable_support",
                        "",
                    )
                ),

            "support_type":
                str(
                    settings.get(
                        "support_type",
                        "",
                    )
                ),

            "support_on_build_plate_only":
                str(
                    settings.get(
                        "support_on_build_plate_only",
                        "",
                    )
                ),

            "detect_floating_vertical_shell":
                str(
                    settings.get(
                        "detect_floating_vertical_shell",
                        "",
                    )
                ),

            "detect_overhang_wall":
                str(
                    settings.get(
                        "detect_overhang_wall",
                        "",
                    )
                ),
        }

        result["support_settings"] = support

        if support["enable_support"] != "1":
            blockers.append(
                "automatic_support_disabled"
            )

        if (
            support["support_type"]
            != "normal(auto)"
        ):
            blockers.append(
                "conservative_support_policy_not_applied"
            )

        if (
            support[
                "support_on_build_plate_only"
            ]
            != "0"
        ):
            blockers.append(
                "support_restricted_to_build_plate"
            )

        if (
            support[
                "detect_floating_vertical_shell"
            ]
            != "1"
        ):
            blockers.append(
                "floating_shell_detection_disabled"
            )

        if (
            support[
                "detect_overhang_wall"
            ]
            != "1"
        ):
            blockers.append(
                "overhang_detection_disabled"
            )

        support_count = 0
        support_interface_count = 0
        bridge_count = 0

        for name in gcode_entries:
            text = z.read(name).decode(
                "utf-8",
                errors="replace",
            )

            support_count += len(
                re.findall(
                    r"(?im)^;\s*"
                    r"(?:FEATURE|TYPE)\s*:\s*"
                    r"Support(?:\s|$)",
                    text,
                )
            )

            support_interface_count += len(
                re.findall(
                    r"(?im)^;\s*"
                    r"(?:FEATURE|TYPE)\s*:\s*"
                    r"Support interface",
                    text,
                )
            )

            bridge_count += len(
                re.findall(
                    r"(?im)^;\s*"
                    r"(?:FEATURE|TYPE)\s*:\s*"
                    r"Bridge(?:\s|$)",
                    text,
                )
            )

        result["support_feature_count"] = (
            support_count
        )

        result[
            "support_interface_count"
        ] = support_interface_count

        result["bridge_feature_count"] = (
            bridge_count
        )

        if (
            support_required
            and support_count == 0
            and support_interface_count == 0
        ):
            blockers.append(
                "support_required_but_not_generated"
            )

    result["status"] = (
        "pass"
        if not blockers
        else "blocked"
    )

    return result


def run_pipeline(
    *,
    input_stl: Path,
    studio_exe: Path,
    machine_profile: Path,
    process_profile: Path,
    filament_profile: Path,
    output_dir: Path,
) -> dict[str, Any]:

    input_stl = Path(input_stl).resolve()
    studio_exe = Path(studio_exe).resolve()
    output_dir = Path(output_dir).resolve()

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    oriented_stl = (
        output_dir
        / "m3_printable_oriented.stl"
    )

    orientation_report = (
        output_dir
        / "m3_orientation_report.json"
    )

    conservative_process = (
        output_dir
        / "m3_conservative_process.json"
    )

    artifact = (
        output_dir
        / "m3_printable.gcode.3mf"
    )

    report_path = (
        output_dir
        / "m3_manufacturability_report.json"
    )

    blockers: list[str] = []

    report: dict[str, Any] = {
        "schema_version": "0.1.0",
        "module": "M3",
        "input_stl": str(input_stl),
        "output_dir": str(output_dir),
        "blockers": blockers,
    }

    try:
        orientation = orient_for_printing(
            input_path=input_stl,
            output_path=oriented_stl,
            report_path=orientation_report,
        )

    except Exception as exc:
        blockers.append(
            "orientation_failed:"
            + repr(exc)
        )

        report["status"] = "blocked"

        report_path.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        return report

    report["orientation"] = orientation

    selected = orientation["selected"]

    if (
        orientation.get("status")
        != "printable_orientation_pass"
    ):
        blockers.append(
            "orientation_gate_not_passed"
        )

    if selected.get("blockers"):
        blockers.append(
            "orientation_has_blockers"
        )

    build_conservative_process_profile(
        source_profile=process_profile,
        output_profile=conservative_process,
    )

    # Remove stale output before every attempt.
    if artifact.exists():
        artifact.unlink()

    cmd = [
        str(studio_exe),

        # Our own validated orientation is authoritative.
        # Never let Bambu orient it again.
        "--arrange",
        "0",

        "--ensure-on-bed",

        "--slice",
        "0",

        "--debug",
        "2",

        "--outputdir",
        str(output_dir),

        "--export-3mf",
        artifact.name,

        "--load-settings",
        ";".join([
            str(
                Path(
                    machine_profile
                ).resolve()
            ),
            str(
                conservative_process.resolve()
            ),
        ]),

        "--load-filaments",
        str(
            Path(
                filament_profile
            ).resolve()
        ),

        str(oriented_stl),
    ]

    slice_result = run_bambu_cli(
        cmd,
        expected_outputs=[artifact],
        cwd=output_dir,
        timeout=1800,
    )

    report["slice"] = {
        "raw_exit":
            slice_result.raw_exit,

        "signed_exit":
            slice_result.signed_exit,

        "outputs_exist":
            slice_result.outputs_exist,

        "success":
            slice_result.success,

        "stdout_tail":
            slice_result.stdout[-5000:],

        "stderr_tail":
            slice_result.stderr[-5000:],
    }

    if not slice_result.success:
        blockers.append(
            "headless_slice_failed"
        )

    # Permanent fail-fast:
    # never pass a nonexistent path into ZipFile.
    if not artifact.is_file():
        blockers.append(
            "slice_artifact_not_created"
        )

        report["status"] = "blocked"

        report_path.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        return report

    try:
        xml_repair = (
            repair_bambu_model_settings_xml(
                artifact
            )
        )

        report["xml_repair"] = (
            xml_repair
        )

    except Exception as exc:
        blockers.append(
            "xml_repair_failed:"
            + repr(exc)
        )

    artifact_gate = inspect_gcode_3mf(
        artifact,
        support_required=bool(
            selected.get(
                "support_required"
            )
        ),
    )

    report["artifact"] = artifact_gate

    blockers.extend(
        blocker
        for blocker in artifact_gate[
            "blockers"
        ]
        if blocker not in blockers
    )

    continuity_gate = (
        inspect_gcode3mf_support_continuity(
            artifact
        )
    )

    report[
        "support_continuity"
    ] = continuity_gate

    blockers.extend(
        "support_continuity:"
        + blocker
        for blocker
        in continuity_gate.get(
            "blockers",
            [],
        )
        if (
            "support_continuity:"
            + blocker
        )
        not in blockers
    )

    report["status"] = (
        "complete"
        if not blockers
        else "blocked"
    )

    report["next_module"] = (
        "M4"
        if not blockers
        else None
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    return report


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input-stl",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--studio",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--machine",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--process",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--filament",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    report = run_pipeline(
        input_stl=args.input_stl,
        studio_exe=args.studio,
        machine_profile=args.machine,
        process_profile=args.process,
        filament_profile=args.filament,
        output_dir=args.output_dir,
    )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )

    return (
        0
        if report["status"] == "complete"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
