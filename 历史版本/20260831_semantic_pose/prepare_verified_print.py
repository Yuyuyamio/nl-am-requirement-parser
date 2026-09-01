"""Prepare a local print artifact without starting or contacting a printer."""
from pathlib import Path
import argparse
import json
from am_print_executor.verified_print_preparation import prepare_verified_print

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--allow-structural-changes", action="store_true")
args = parser.parse_args()
print("PREPARE", args.source, flush=True)
result = prepare_verified_print(args.source, args.output, allow_structural_changes=args.allow_structural_changes)
print(json.dumps({k: result[k] for k in ("status", "geometry_path", "preparation_directory", "auto_orient_applied", "source_unchanged")}, indent=2), flush=True)
