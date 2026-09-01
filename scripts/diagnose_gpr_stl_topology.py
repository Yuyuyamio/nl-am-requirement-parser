"""Reproduce the V1.3 STL boolean failure without touching source fixtures."""
from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from importlib.metadata import version
from pathlib import Path

import manifold3d
import numpy as np
import trimesh

from am_print_executor.general_printability_geometry_repair import build_printability_repair_request
from am_print_executor.local_self_support_envelope import (
    build_envelope_mesh, plan_local_self_support_envelopes,
)


def summary(mesh):
    return {
        "vertices": len(mesh.vertices),
        "unique_vertices": len(np.unique(mesh.vertices, axis=0)),
        "faces": len(mesh.faces),
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "body_count": int(mesh.body_count),
        "volume_mm3": float(mesh.volume),
        "bounds_mm": None if mesh.bounds is None else mesh.bounds.tolist(),
        "extents_mm": None if mesh.extents is None else mesh.extents.tolist(),
    }


def main():
    report = {"versions": {n: version(n) for n in ("trimesh", "manifold3d", "numpy")}}
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "source.stl"
        original = trimesh.creation.box(extents=(2, 8, 10))
        original.apply_translation((0, 0, 5))
        original.export(source)
        sha_before = hashlib.sha256(source.read_bytes()).hexdigest()
        gate = {
            "status": "blocked", "blockers": ["unsupported_extrusion_region"],
            "policy": {"line_width_mm": 0.4, "cell_mm": 0.4,
                       "measured_layer_height_mm": 0.2, "self_support_xy_mm": 0.26},
            "dangerous_layers": [{"z_mm": 5.0, "issues": [{
                "kind": "unsupported_extrusion_region", "area_mm2": 0.04,
                "xy_bounds_mm": [[1.05, -0.2], [1.25, 0.2]],
                "features": ["outer wall"],
            }]}],
        }
        request = build_printability_repair_request(source_geometry=source, gate_report=gate)
        plans = plan_local_self_support_envelopes(request)
        raw = trimesh.load(source, force="mesh", process=False)
        normalized = raw.copy()
        normalized.merge_vertices()
        normalized.remove_unreferenced_vertices()
        normalized.fix_normals()
        cases = {"original_in_memory": original, "stl_process_false": raw,
                 "stl_process_true": trimesh.load(source, force="mesh", process=True),
                 "stl_explicit_normalization": normalized}
        report["cases"] = {}
        for name, mesh in cases.items():
            m = manifold3d.Manifold(manifold3d.Mesh(
                np.asarray(mesh.vertices, dtype=np.float32),
                np.asarray(mesh.faces, dtype=np.uint32)))
            record = {"input": summary(mesh), "manifold_input_status": str(m.status()), "plans": {}}
            for plan in plans:
                envelope = build_envelope_mesh(plan)
                results = {"envelope": summary(envelope)}
                for operation in ("intersection", "union"):
                    for checked in (False, True):
                        key = f"{operation}_check_volume_{checked}"
                        try:
                            output = getattr(trimesh.boolean, operation)(
                                [mesh.copy(), envelope.copy()], engine="manifold", check_volume=checked)
                            results[key] = summary(output)
                            if output.extents is None:
                                try:
                                    np.asarray(output.extents, dtype=float)[0]
                                except IndexError as exc:
                                    results[key]["legacy_extents_error"] = str(exc)
                        except Exception as exc:
                            results[key] = {"error": f"{type(exc).__name__}: {exc}"}
                record["plans"][plan.strength] = results
            report["cases"][name] = record
        report["source_sha_before"] = sha_before
        report["source_sha_after"] = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = Path("outputs/gpr_v1_3_validation/stl_topology_diagnosis.json")
    frozen = Path("outputs/flat_base_acceptance/AUTO-20260825-143340-FLATBASE-RESUME/current_mouse.repaired.bed_centered.flat_base.stl")
    canonical = Path("outputs/printability_strategy_benchmark_v1/preflight_20260826_162302/canonical_geometry/real_current_mouse.stl")
    if frozen.is_file() and canonical.is_file():
        first = trimesh.load(frozen, force="mesh", process=True)
        second = trimesh.load(canonical, force="mesh", process=True)
        def triangle_keys(mesh):
            return {tuple(sorted(tuple(v) for v in triangle)) for triangle in mesh.triangles}
        comparison = {"frozen": summary(first), "benchmark": summary(second),
                      "exact_triangle_geometry_equal": triangle_keys(first) == triangle_keys(second),
                      "frozen_sha256": hashlib.sha256(frozen.read_bytes()).hexdigest(),
                      "benchmark_sha256": hashlib.sha256(canonical.read_bytes()).hexdigest()}
        artifact = frozen.parent / "resume_final_support.gcode.3mf"
        if artifact.exists():
            with zipfile.ZipFile(artifact) as archive:
                settings = json.loads(archive.read("Metadata/project_settings.config"))
                comparison["frozen_artifact_support"] = {k: settings.get(k) for k in
                    ("enable_support", "support_type", "support_threshold_angle")}
        report["frozen_mouse_comparison"] = comparison
        print("FROZEN_MOUSE", json.dumps(comparison))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    for name, record in report["cases"].items():
        print(name, json.dumps({"input": record["input"], "manifold_status": record["manifold_input_status"],
                               "minimal_union": record["plans"]["minimal"]["union_check_volume_False"]}))
    print("REPORT", destination)


if __name__ == "__main__":
    main()
