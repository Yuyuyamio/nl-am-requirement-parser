"""Prepare a local print artifact without starting or contacting a printer."""
from pathlib import Path
import argparse
import json
from am_print_executor.verified_print_preparation import prepare_verified_print

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--height-mm",type=float)
parser.add_argument("--machine",type=Path)
parser.add_argument("--process",type=Path)
parser.add_argument("--filament",type=Path,action="append")
args = parser.parse_args()
print("PREPARE", args.source, flush=True)
result = prepare_verified_print(args.source, args.output,
    target_height_mm=args.height_mm,machine_json=args.machine,process_json=args.process,filament_jsons=args.filament,
    support_mode="detachable")
print(json.dumps({k: result[k] for k in ("status", "geometry_path", "preparation_directory", "auto_orient_applied", "source_unchanged")}, indent=2), flush=True)
