from pathlib import Path
from datetime import datetime
import argparse
import json
from am_print_executor.verified_print_preparation import finalize_verified_geometry

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
args = parser.parse_args()
base = args.directory.resolve()
out = base.parent / datetime.now().strftime("final_%Y%m%d_%H%M%S")
print("FINALIZE", out, flush=True)
receipt = finalize_verified_geometry(source=base / "candidate.stl", prior_artifact=base / "candidate.gcode.3mf",
    directory=out, studio_exe=Path("C:/Program Files/Bambu Studio/bambu-studio.exe"),
    machine_json=next((base / "profiles").glob("machine_*.json")), process_json=base / "process_input.json",
    filament_jsons=list((base / "profiles").glob("filament_*.json")))
print(json.dumps({k: receipt[k] for k in ("status", "before_orientation_status", "after_orientation_status", "dangerous_layer_count", "project", "geometry")}, indent=2), flush=True)
