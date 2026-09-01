"""Build and slice a foundation candidate locally; never send a print job."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import trimesh

from am_print_executor.bambu_headless_cli import run_bambu_cli
from am_print_executor.bambu_profile_resolver import materialize_bambu_cli_profiles, flatten_system_profile
from am_print_executor.bambu_project_repair import repair_bambu_model_settings_xml
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.printable_foundation import add_printable_foundation

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--footprint-fraction", type=float, default=1.0)
    parser.add_argument("--solid", action="store_true")
    parser.add_argument("--keep-geometry", action="store_true")
    args = parser.parse_args()
    out = ROOT / "outputs/printable_foundation_validation" / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    mesh = trimesh.load(source, force="mesh", process=True)
    if args.keep_geometry:
        candidate, action = mesh.copy(), {"method": "preserve_geometry"}
    else:
        candidate, action = add_printable_foundation(mesh, footprint_fraction=args.footprint_fraction)
    stl = out / "candidate.stl"
    candidate.export(stl)
    save(out / "geometry.json", {"source": str(source), "source_sha256": source_hash,
                                 "action": action, "flat_base": inspect_flat_printing_base(stl)})
    studio = Path("C:/Program Files/Bambu Studio/bambu-studio.exe")
    profiles = studio.parent / "resources/profiles/BBL"
    process = flatten_system_profile(profiles / "process/0.20mm Standard @BBL X1C.json", category_root=profiles / "process")
    process.update(name="Verified stable-base candidate", enable_support="1", support_type="normal(auto)",
                   support_threshold_angle="30", support_on_build_plate_only="0", support_critical_regions_only="0",
                   support_remove_small_overhang="0", support_interface_top_layers="3", support_interface_bottom_layers="2",
                   support_interface_spacing="0.35", support_top_z_distance="0.2", support_bottom_z_distance="0.2",
                   support_base_pattern_spacing="2.0", detect_floating_vertical_shell="1", detect_overhang_wall="1", bridge_no_support="0")
    if args.solid:
        process.update(sparse_infill_density="100%", sparse_infill_pattern="rectilinear")
    process_path = out / "process_input.json"
    save(process_path, process)
    resolved = materialize_bambu_cli_profiles(studio_exe=studio,
        machine_json=profiles / "machine/Bambu Lab X1 Carbon 0.4 nozzle.json",
        process_json=process_path, filament_jsons=[profiles / "filament/Bambu PLA Basic @BBL X1C.json"],
        cache_dir=out / "profiles")
    artifact = out / "candidate.gcode.3mf"
    command = [str(studio), "--arrange", "0", "--ensure-on-bed", "--slice", "0", "--debug", "2",
               "--outputdir", str(out), "--export-3mf", artifact.name,
               "--load-settings", ";".join(map(str, [resolved["machine"], resolved["process"]])),
               "--load-filaments", ";".join(map(str, resolved["filaments"])), str(stl)]
    print("SLICE", out, flush=True)
    sliced = run_bambu_cli(command, expected_outputs=[artifact], cwd=out, timeout=600)
    save(out / "slice.json", asdict(sliced))
    if not sliced.success:
        raise RuntimeError(sliced.stderr[-3000:] + sliced.stdout[-3000:])
    repair_bambu_model_settings_xml(artifact)
    gate = inspect_final_gcode_printability(artifact, geometry_path=stl)
    save(out / "gate.json", gate)
    summary = {key: gate.get(key) for key in ("status", "blockers", "dangerous_layer_count", "total_bad_area_mm2")}
    summary.update(directory=str(out), source_sha_unchanged=source_hash == hashlib.sha256(source.read_bytes()).hexdigest())
    save(out / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
