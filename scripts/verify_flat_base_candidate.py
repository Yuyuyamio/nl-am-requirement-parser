from __future__ import annotations

import argparse
import json

from pathlib import Path

from am_print_executor.developer_mode_backend_v1120 import (
    discover_bambu_profiles,
    discover_bambu_studio,
    slice_with_bambu_cli,
)
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.gcode_printability_gate import (
    inspect_final_gcode_printability,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Slice one already Auto-Oriented local project and verify the "
            "flat-base and final G-code printability gates."
        )
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--process", type=Path, required=True)
    args = parser.parse_args()

    model = args.model.expanduser().resolve()
    project = args.project.expanduser().resolve()
    output = args.output.expanduser().resolve()
    process = args.process.expanduser().resolve()
    if project.suffix.lower() != ".3mf":
        parser.error("--project must be an already Auto-Oriented .3mf")

    studio = discover_bambu_studio()
    if studio is None:
        parser.error("Bambu Studio was not found")
    profiles = discover_bambu_profiles(Path(studio))
    sliced = slice_with_bambu_cli(
        project,
        output,
        studio_exe=Path(studio),
        machine_json=Path(profiles["machine"]),
        process_json=process,
        filament_jsons=[Path(profiles["filament"])],
    )
    flat_base = inspect_flat_printing_base(model)
    printability = inspect_final_gcode_printability(
        output,
        geometry_path=model,
    )
    passed = (
        flat_base["base_flatness_passed"]
        and printability["status"] == "pass"
    )
    print(
        json.dumps(
            {
                "status": "pass" if passed else "block",
                "flat_base_gate": flat_base,
                "printability_gate": printability,
                "slice": sliced,
                "auto_orient_invoked": False,
                "printer_contacted": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
