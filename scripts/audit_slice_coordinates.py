"""Compare source, committed 3MF placement and actual model extrusion bounds."""
from __future__ import annotations
import argparse
import json
import zipfile
from pathlib import Path
import numpy as np
import trimesh
from am_print_executor.rigid_multimaterial_project import parse_3mf_leaves
from am_print_executor.gcode_support_continuity import parse_extrusion_segments
from am_print_executor.gcode_printability_gate import _is_model

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    source = trimesh.load(args.source, force="mesh", process=True)
    leaves = parse_3mf_leaves(args.artifact)
    world = trimesh.util.concatenate([trimesh.Trimesh(vertices=leaf["vertices_world"], faces=leaf["faces"], process=True) for leaf in leaves])
    with zipfile.ZipFile(args.artifact) as archive:
        name = next(name for name in archive.namelist() if name.endswith("plate_1.gcode"))
        segments = [s for s in parse_extrusion_segments(archive.read(name).decode("utf-8")) if _is_model(s.feature)]
    xy = np.array([[s.x1, s.y1] for s in segments] + [[s.x2, s.y2] for s in segments])
    result = {"source_bounds": source.bounds.tolist(), "world_bounds": world.bounds.tolist(),
              "gcode_xy_bounds": [xy.min(axis=0).tolist(), xy.max(axis=0).tolist()],
              "source_center": source.bounds.mean(axis=0).tolist(), "world_center": world.bounds.mean(axis=0).tolist(),
              "part_transforms": [leaf["matrix"].tolist() for leaf in leaves]}
    args.artifact.with_name("slice_world_geometry.stl").write_bytes(world.export(file_type="stl"))
    args.artifact.with_name("coordinate_audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
