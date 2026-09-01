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


def prepare_verified_print(
    source: Path, output_path: Path, *, studio_exe: Path | None = None,
    machine_json: Path | None = None, process_json: Path | None = None,
    filament_jsons=None, allow_structural_changes: bool = False,
    critical_regions: tuple[dict, ...] = (),
) -> dict:
    """Prepare one solid with at most two repair rounds, then prove placement.

    Visible foundations/ribs and solid infill require explicit design opt-in.
    A blocked result remains reviewable but is never published at output_path.
    """
    import re
    import tempfile
    import numpy as np
    import trimesh
    from am_print_executor.bambu_profile_resolver import materialize_bambu_cli_profiles
    from am_print_executor.developer_mode_backend_v1120 import (
        discover_bambu_profiles, discover_bambu_studio, inspect_gcode_3mf,
    )
    from am_print_executor.flat_base_gate import ensure_flat_printing_base
    from am_print_executor.geometry_fidelity_gate import normalized_boolean_copy, mesh_validation, inspect_geometry_fidelity
    from am_print_executor.general_printability_geometry_repair import build_printability_repair_request
    from am_print_executor.local_anchored_growth import build_anchored_growth_envelopes
    from am_print_executor.printable_foundation import add_printable_foundation
    from am_print_executor.integrated_support_ribs import add_integrated_support_ribs

    source, output_path = Path(source).resolve(), Path(output_path).resolve()
    if output_path.exists():
        raise FileExistsError("Refusing to overwrite an earlier print artifact: " + str(output_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="verified_preparation_", dir=output_path.parent))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    mesh = normalized_boolean_copy(trimesh.load(source, force="mesh", process=False))
    if not mesh_validation(mesh)["valid"] or mesh.body_count != 1:
        raise ValueError("verified_preparation_requires_one_connected_solid")
    studio_exe = studio_exe or discover_bambu_studio()
    if studio_exe is None:
        raise ValueError("bambu_studio_not_found")
    studio_exe = Path(studio_exe).resolve()
    discovered = discover_bambu_profiles(studio_exe)
    resolved = materialize_bambu_cli_profiles(studio_exe=studio_exe,
        machine_json=Path(machine_json or discovered["machine"]),
        process_json=Path(process_json or discovered["process"]),
        filament_jsons=list(filament_jsons or [discovered["filament"]]), cache_dir=directory / "profiles")
    process = json.loads(Path(resolved["process"]).read_text(encoding="utf-8"))
    actions = []
    if allow_structural_changes:
        process.update(enable_support="1", support_type="normal(auto)", support_threshold_angle="30",
                       support_on_build_plate_only="0", support_critical_regions_only="0", support_remove_small_overhang="0",
                       support_interface_top_layers="3", support_interface_bottom_layers="2", support_interface_spacing="0.35",
                       support_top_z_distance="0.2", support_bottom_z_distance="0.2", support_base_pattern_spacing="2.0",
                       detect_floating_vertical_shell="1", detect_overhang_wall="1", bridge_no_support="0",
                       sparse_infill_density="100%", sparse_infill_pattern="zig-zag")
        actions.append({"method": "solid_infill_and_normal_auto_30", "changes_material_use": True})
    process_file = directory / "process.json"
    save_report(process_file, process)
    profiles = dict(studio_exe=studio_exe, machine_json=Path(resolved["machine"]),
                    process_json=process_file, filament_jsons=list(resolved["filaments"]))
    if critical_regions and not inspect_flat_printing_base(mesh)["base_flatness_passed"]:
        if not allow_structural_changes:
            raise ValueError("protected_model_requires_explicit_base_redesign")
        # Clipping is subtractive; do not clip a model with protected regions.
        mesh, base_action = add_printable_foundation(mesh, critical_regions=critical_regions)
    else:
        mesh, base_action = ensure_flat_printing_base(mesh, allow_foundation=allow_structural_changes,
                                                     critical_regions=critical_regions)
    actions.append(base_action)
    machine = json.loads(Path(resolved["machine"]).read_text(encoding="utf-8"))
    points = np.array([[float(v) for v in re.split("x", point)] for point in machine["printable_area"]])
    bed_center = (points.min(axis=0) + points.max(axis=0)) / 2
    translation = np.r_[bed_center - mesh.bounds.mean(axis=0)[:2], -mesh.bounds[0, 2]]
    mesh.apply_translation(translation)
    # Critical coordinates follow the same model placement.
    placed_critical = []
    for region in critical_regions:
        bounds = np.asarray(region.get("bounds_mm"), dtype=float)
        if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
            raise ValueError("unlocalized_critical_region")
        placed_critical.append({**region, "bounds_mm": (bounds + translation).tolist()})
    if np.any(mesh.bounds[0, :2] < points.min(axis=0) + 2) or np.any(mesh.bounds[1, :2] > points.max(axis=0) - 2):
        raise ValueError("model_does_not_fit_print_bed_with_margin")
    initial = directory / "prepared.stl"
    mesh.export(initial)
    current = slice_fixed_geometry(source=initial, directory=directory / "initial_slice", **profiles)
    rounds = []
    for number in range(1, 3):
        if current["gate"]["status"] == "pass" or not allow_structural_changes:
            break
        mesh = trimesh.load(current["geometry"], force="mesh", process=True)
        request = build_printability_repair_request(source_geometry=current["geometry"], gate_report=current["gate"],
                    flat_base_report=current["flat_base"], critical_regions=placed_critical)
        record = {"round": number, "coordinate_mapping": request.coordinate_mapping, "candidates": []}
        rounds.append(record)
        candidates = [current]
        # Prefer a small local ramp when the original fidelity budget accepts it.
        envelopes, plans = build_anchored_growth_envelopes(mesh, request.blocker_clusters,
                                                          layer_height_mm=float(process.get("layer_height", .2)))
        if envelopes:
            volume = trimesh.boolean.union(envelopes, engine="manifold", check_volume=True)
            candidate = trimesh.boolean.union([mesh, volume], engine="manifold", check_volume=True)
            fidelity = inspect_geometry_fidelity(source=mesh, candidate=candidate, repair_volume=volume,
                        budget=request.repair_budget, protected_base_clearance_z_mm=float(mesh.bounds[0, 2])+.6,
                        critical_regions=tuple(placed_critical))
            record["candidates"].append({"method": "local_anchored_growth", "plans": plans, "fidelity": fidelity})
            if fidelity["status"] == "pass":
                path = directory / f"round_{number}_local.stl"
                candidate.export(path)
                checked = slice_fixed_geometry(source=path, directory=directory / f"round_{number}_local_slice", **profiles)
                candidates.append(checked)
        if candidates[-1]["gate"]["status"] != "pass":
            try:
                # Explicit structural redesign is reported separately; it does
                # not turn a failed GPR fidelity check into a GPR PASS.
                candidate, action = add_integrated_support_ribs(mesh, request.blocker_clusters,
                                                                critical_regions=tuple(placed_critical))
            except ValueError as exc:
                if str(exc) != "rib_footprint_not_inside_foundation":
                    record["structural_blocker"] = str(exc)
                    break
                foundation, action_base = add_printable_foundation(mesh, critical_regions=tuple(placed_critical))
                candidate, action = add_integrated_support_ribs(foundation, request.blocker_clusters,
                                                                critical_regions=tuple(placed_critical))
                action["foundation"] = action_base
            path = directory / f"round_{number}_structural.stl"
            candidate.export(path)
            record["candidates"].append(action)
            checked = slice_fixed_geometry(source=path, directory=directory / f"round_{number}_structural_slice", **profiles)
            candidates.append(checked)
        current = min(candidates, key=lambda c: (c["gate"]["status"] != "pass",
                      c["gate"].get("dangerous_layer_count", 10**9), c["gate"].get("total_bad_area_mm2", float("inf"))))
    save_report(directory / "preparation.json", {"source": str(source), "source_sha256": source_hash,
                "structural_changes_authorized": allow_structural_changes, "actions": actions, "rounds": rounds})
    receipt = None
    if current["gate"]["status"] == "pass" and current["flat_base"]["base_flatness_passed"]:
        receipt = finalize_verified_geometry(source=current["geometry"], prior_artifact=current["artifact"],
                                               directory=directory / "final", **profiles)
        current = {"artifact": Path(receipt["artifact"]), "geometry": Path(receipt["geometry"])}
    accepted = bool(receipt and receipt["status"] == "pass")
    if accepted:
        if source_hash != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError("source_changed_during_preparation")
        with output_path.open("xb") as handle:
            handle.write(current["artifact"].read_bytes())
        current["artifact"] = output_path
    result = {"status": "slice_complete" if accepted else "blocked", "pipeline": "strict_slice_orient_reslice_v1",
              "artifact": inspect_gcode_3mf(current["artifact"]), "geometry_path": str(current["geometry"]),
              "auto_orient_applied": receipt is not None, "acceptance": receipt,
              "preparation_directory": str(directory), "source_unchanged": source_hash == hashlib.sha256(source.read_bytes()).hexdigest()}
    save_report(directory / "result.json", result)
    return result
