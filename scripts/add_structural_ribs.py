"""Build an explicitly redesigned candidate, never print or waive a gate."""
from pathlib import Path
from datetime import datetime
import argparse
import json
import trimesh
from am_print_executor.general_printability_geometry_repair import build_printability_repair_request
from am_print_executor.integrated_support_ribs import add_integrated_support_ribs
from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame, verify_source_matches_slice

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
args = parser.parse_args()
source_path = args.directory / "candidate.stl"
source = trimesh.load(source_path, force="mesh", process=True)
artifact = args.directory / "candidate.gcode.3mf"
verify_source_matches_slice(source, artifact)
gate = gate_report_in_geometry_frame(json.loads((args.directory / "gate.json").read_text(encoding="utf-8")), artifact)
request = build_printability_repair_request(source_geometry=source_path, gate_report=gate)
candidate, action = add_integrated_support_ribs(source, request.blocker_clusters)
out = Path(__file__).resolve().parents[1] / "outputs/printable_foundation_validation" / datetime.now().strftime("structural_%Y%m%d_%H%M%S")
out.mkdir(parents=True, exist_ok=False)
candidate.export(out / "candidate.stl")
(out / "action.json").write_text(json.dumps(action, indent=2), encoding="utf-8")
print(out, flush=True)
