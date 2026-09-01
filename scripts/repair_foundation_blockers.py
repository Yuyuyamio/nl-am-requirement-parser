"""One bounded, coordinate-corrected local union round (no slicer calls)."""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path
import trimesh
from am_print_executor.general_printability_geometry_repair import build_printability_repair_request
from am_print_executor.local_self_support_envelope import plan_local_self_support_envelopes, build_envelope_mesh
from am_print_executor.local_anchored_growth import build_anchored_growth_envelopes
from am_print_executor.geometry_fidelity_gate import inspect_geometry_fidelity
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame, verify_source_matches_slice

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    base = args.directory.resolve()
    source_path = base / "candidate.stl"
    source = trimesh.load(source_path, force="mesh", process=True)
    artifact = base / "candidate.gcode.3mf"
    frame_check = verify_source_matches_slice(source, artifact)
    gate = gate_report_in_geometry_frame(json.loads((base / "gate.json").read_text(encoding="utf-8")), artifact)
    request = build_printability_repair_request(source_geometry=source_path, gate_report=gate,
                                               flat_base_report=inspect_flat_printing_base(source))
    out = ROOT / "outputs/printable_foundation_validation" / datetime.now().strftime("repair_%Y%m%d_%H%M%S")
    out.mkdir(parents=True)
    report = {"coordinate_check": frame_check, "coordinate_mapping": gate["coordinate_mapping"], "candidates": []}
    for strength, margin in (("minimal", .2), ("balanced", .3), ("strong", .4)):
        envelopes, selected = build_anchored_growth_envelopes(source, request.blocker_clusters, margin_mm=margin)
        if not envelopes:
            continue
        repair_volume = trimesh.boolean.union(envelopes, engine="manifold")
        candidate = trimesh.boolean.union([source, repair_volume], engine="manifold")
        fidelity = inspect_geometry_fidelity(source=source, candidate=candidate, repair_volume=repair_volume,
            budget=request.repair_budget, protected_base_clearance_z_mm=float(source.bounds[0, 2])+.6)
        record = {"strength": strength, "plans": selected, "fidelity": fidelity}
        if fidelity["status"] == "pass":
            path = out / f"{strength}.stl"
            candidate.export(path)
            record["geometry"] = str(path)
        report["candidates"].append(record)
        print(strength, len(envelopes), fidelity["status"], fidelity["blockers"], flush=True)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("OUTPUT", out, flush=True)

if __name__ == "__main__":
    main()
