"""Local-only slicing and final acceptance; never upload or start a printer."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import hashlib
import json

from am_print_executor.bambu_auto_orient import auto_orient_with_bambu_cli
from am_print_executor.bambu_headless_cli import run_bambu_cli
from am_print_executor.bambu_project_repair import repair_bambu_model_settings_xml
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.slice_geometry_frame import placed_mesh_from_project, verify_source_matches_slice


def save_report(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def slice_fixed_geometry(*, source: Path, directory: Path, studio_exe: Path,
                         machine_json: Path, process_json: Path, filament_jsons) -> dict:
    """No implicit orientation; inspect the geometry actually committed by Bambu."""
    directory.mkdir(parents=True, exist_ok=False)
    artifact = directory / "candidate.gcode.3mf"
    command = [str(studio_exe), "--arrange", "0", "--ensure-on-bed", "--slice", "0", "--debug", "2",
               "--outputdir", str(directory), "--export-3mf", artifact.name,
               "--load-settings", f"{machine_json};{process_json}",
               "--load-filaments", ";".join(map(str, filament_jsons)), str(source)]
    result = run_bambu_cli(command, expected_outputs=[artifact], cwd=directory, timeout=600)
    save_report(directory / "slice.json", asdict(result))
    if not result.success:
        raise RuntimeError(f"Bambu slice failed; see {directory / 'slice.json'} and result.json")
    repair_bambu_model_settings_xml(artifact)
    geometry = directory / "candidate.stl"
    placed = placed_mesh_from_project(artifact)
    placed.export(geometry)
    flat_base = inspect_flat_printing_base(placed)
    gate = inspect_final_gcode_printability(artifact, geometry_path=geometry)
    save_report(directory / "flat_base.json", flat_base)
    save_report(directory / "gate.json", gate)
    return {"artifact": artifact, "geometry": geometry, "gate": gate, "flat_base": flat_base}


def finalize_verified_geometry(*, source: Path, prior_artifact: Path, directory: Path,
                               studio_exe: Path, machine_json: Path, process_json: Path,
                               filament_jsons) -> dict:
    """Recompute a strict PASS before Auto Orient, and another PASS after it."""
    import trimesh
    directory.mkdir(parents=True, exist_ok=False)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    verify_source_matches_slice(trimesh.load(source, force="mesh", process=True), prior_artifact)
    prior_gate = inspect_final_gcode_printability(prior_artifact, geometry_path=source)
    save_report(directory / "before_orientation_gate.json", prior_gate)
    if prior_gate["status"] != "pass":
        raise ValueError("strict_printability_must_pass_before_auto_orient")
    project = directory / "auto_oriented.3mf"
    profiles = dict(studio_exe=studio_exe, machine_json=machine_json,
                    process_json=process_json, filament_jsons=list(filament_jsons))
    oriented = auto_orient_with_bambu_cli(source_model=source, output_path=project, **profiles)
    save_report(directory / "auto_orient.json", oriented)
    final = slice_fixed_geometry(source=project, directory=directory / "final_slice", **profiles)
    accepted = final["gate"]["status"] == "pass" and final["flat_base"]["base_flatness_passed"]
    receipt = {"status": "pass" if accepted else "blocked", "source": str(source),
               "source_sha256": source_hash, "source_unchanged": source_hash == hashlib.sha256(source.read_bytes()).hexdigest(),
               "project": str(project), "artifact": str(final["artifact"]), "geometry": str(final["geometry"]),
               "flat_base": final["flat_base"], "auto_orient": oriented,
               "before_orientation_status": prior_gate["status"],
               "after_orientation_status": final["gate"]["status"],
               "dangerous_layer_count": final["gate"]["dangerous_layer_count"],
               "physical_print_performed": False, "printer_upload_performed": False}
    save_report(directory / "acceptance.json", receipt)
    return receipt
