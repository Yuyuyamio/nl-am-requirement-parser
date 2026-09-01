from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


class GeometryAuditError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def lname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def attr_local(el: ET.Element, name: str) -> str | None:
    for k, v in el.attrib.items():
        if lname(k) == name:
            return v
    return None


def matrix3mf(value: str | None) -> np.ndarray:
    if not value:
        return np.eye(4, dtype=float)
    nums = [float(x) for x in value.split()]
    if len(nums) != 12:
        raise GeometryAuditError(f"Invalid 3MF transform: {value!r}")
    return np.array([
        [nums[0], nums[1], nums[2], 0.0],
        [nums[3], nums[4], nums[5], 0.0],
        [nums[6], nums[7], nums[8], 0.0],
        [nums[9], nums[10], nums[11], 1.0],
    ], dtype=float)


def transform_points(vertices: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    h = np.hstack([vertices, np.ones((len(vertices), 1), dtype=float)])
    return (h @ matrix)[:, :3]


def normalize_part(current: str, ref: str | None) -> str:
    if not ref:
        return current
    ref = ref.replace("\\", "/")
    if ref.startswith("/"):
        return ref.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(current), ref))


def load_mesh(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise GeometryAuditError(f"Could not load mesh: {path}")
    return mesh


def mesh_summary(mesh: trimesh.Trimesh) -> dict[str, Any]:
    return {
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "bounds": np.asarray(mesh.bounds, dtype=float).round(6).tolist(),
        "extents": np.asarray(mesh.extents, dtype=float).round(6).tolist(),
        "volume_abs": float(abs(mesh.volume)),
        "watertight": bool(mesh.is_watertight),
        "connected_components": int(len(mesh.split(only_watertight=False))),
        "centroid_vertices": np.asarray(mesh.vertices, dtype=float).mean(axis=0).round(6).tolist(),
    }


def partition_planes(source: trimesh.Trimesh, a: trimesh.Trimesh, b: trimesh.Trimesh, tol: float = 0.05):
    sb = np.asarray(source.bounds, dtype=float)
    ab = np.asarray(a.bounds, dtype=float)
    bb = np.asarray(b.bounds, dtype=float)
    out = []
    for axis, name in enumerate(("X", "Y", "Z")):
        for left, right, order in (
            (ab[1, axis], bb[0, axis], "region0_below_region1"),
            (bb[1, axis], ab[0, axis], "region1_below_region0"),
        ):
            if abs(left - right) > tol:
                continue
            low = min(ab[0, axis], bb[0, axis])
            high = max(ab[1, axis], bb[1, axis])
            if abs(low - sb[0, axis]) > tol or abs(high - sb[1, axis]) > tol:
                continue
            span = sb[1, axis] - sb[0, axis]
            ratio = None if abs(span) < 1e-12 else (((left + right) / 2 - sb[0, axis]) / span)
            out.append({
                "axis": name,
                "plane": round((left + right) / 2, 6),
                "cut_ratio": None if ratio is None else round(float(ratio), 6),
                "order": order,
            })
    return out


def parse_3mf(path: Path):
    if not zipfile.is_zipfile(path):
        raise GeometryAuditError(f"Not a 3MF ZIP: {path}")

    parts: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(path, "r") as zf:
        model_names = [n for n in zf.namelist() if n.lower().endswith(".model")]
        for name in model_names:
            root = ET.fromstring(zf.read(name))
            objects = {}
            resources = next((c for c in root if lname(c.tag) == "resources"), None)
            if resources is not None:
                for obj in resources:
                    if lname(obj.tag) != "object":
                        continue
                    oid = str(obj.attrib.get("id"))
                    mesh_node = next((c for c in obj if lname(c.tag) == "mesh"), None)
                    comps_node = next((c for c in obj if lname(c.tag) == "components"), None)
                    mesh = None
                    comps = []

                    if mesh_node is not None:
                        verts_node = next((c for c in mesh_node if lname(c.tag) == "vertices"), None)
                        tris_node = next((c for c in mesh_node if lname(c.tag) == "triangles"), None)
                        verts, faces = [], []
                        if verts_node is not None:
                            for v in verts_node:
                                if lname(v.tag) == "vertex":
                                    verts.append([float(v.attrib["x"]), float(v.attrib["y"]), float(v.attrib["z"])])
                        if tris_node is not None:
                            for t in tris_node:
                                if lname(t.tag) == "triangle":
                                    faces.append([int(t.attrib["v1"]), int(t.attrib["v2"]), int(t.attrib["v3"])])
                        mesh = {"vertices": np.asarray(verts, dtype=float), "faces": np.asarray(faces, dtype=np.int64)}

                    if comps_node is not None:
                        for comp in comps_node:
                            if lname(comp.tag) != "component":
                                continue
                            comps.append({
                                "objectid": str(comp.attrib["objectid"]),
                                "path": attr_local(comp, "path"),
                                "matrix": matrix3mf(comp.attrib.get("transform")),
                            })

                    objects[oid] = {"mesh": mesh, "components": comps}

            build_items = []
            build = next((c for c in root if lname(c.tag) == "build"), None)
            if build is not None:
                for item in build:
                    if lname(item.tag) == "item":
                        build_items.append({
                            "objectid": str(item.attrib["objectid"]),
                            "path": attr_local(item, "path"),
                            "matrix": matrix3mf(item.attrib.get("transform")),
                        })

            parts[name] = {"objects": objects, "build": build_items}

    root_part = "3D/3dmodel.model" if "3D/3dmodel.model" in parts else None
    if root_part is None:
        candidates = [n for n, d in parts.items() if d["build"]]
        if len(candidates) != 1:
            raise GeometryAuditError(f"Cannot uniquely locate root model part: {candidates}")
        root_part = candidates[0]

    leaves = []

    def resolve(part_name: str, object_id: str, outer: np.ndarray, stack):
        key = (part_name, object_id)
        if key in stack:
            raise GeometryAuditError(f"Recursive component reference: {key}")
        part = parts.get(part_name)
        if part is None:
            raise GeometryAuditError(f"Referenced part missing: {part_name}")
        obj = part["objects"].get(object_id)
        if obj is None:
            raise GeometryAuditError(f"Object {object_id} missing in {part_name}")

        if obj["mesh"] is not None:
            leaves.append({
                "part": part_name,
                "object_id": object_id,
                "vertices": obj["mesh"]["vertices"],
                "faces": obj["mesh"]["faces"],
                "matrix": outer,
            })
            return

        for comp in obj["components"]:
            target = normalize_part(part_name, comp["path"])
            resolve(target, comp["objectid"], comp["matrix"] @ outer, stack + (key,))

    for item in parts[root_part]["build"]:
        target = normalize_part(root_part, item["path"])
        resolve(target, item["objectid"], item["matrix"], tuple())

    return root_part, leaves


def leaf_mesh(leaf) -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=transform_points(leaf["vertices"], leaf["matrix"]),
        faces=leaf["faces"],
        process=False,
    )


def contact_metrics(mesh: trimesh.Trimesh, tol: float = 0.05):
    v = np.asarray(mesh.vertices, dtype=float)
    if len(v) == 0:
        return {"min_z": None, "vertices_near_bed": 0, "bed_triangle_area_mm2": 0.0}

    near = np.abs(v[:, 2]) <= tol
    area = 0.0

    if len(mesh.faces):
        tri = v[np.asarray(mesh.faces, dtype=np.int64)]
        mask = np.max(np.abs(tri[:, :, 2]), axis=1) <= tol
        sel = tri[mask]
        if len(sel):
            cross = np.cross(sel[:, 1] - sel[:, 0], sel[:, 2] - sel[:, 0])
            area = float(0.5 * np.linalg.norm(cross, axis=1).sum())

    return {
        "min_z": float(v[:, 2].min()),
        "vertices_near_bed": int(near.sum()),
        "bed_triangle_area_mm2": area,
    }


def project_audit(path: Path):
    root_part, leaves = parse_3mf(path)
    leaf_rows = []
    world_meshes = []

    for i, leaf in enumerate(leaves):
        m = leaf_mesh(leaf)
        world_meshes.append(m)
        leaf_rows.append({
            "leaf_index": i,
            "part": leaf["part"],
            "object_id": leaf["object_id"],
            "world_bounds": np.asarray(m.bounds, dtype=float).round(6).tolist(),
            "contact": contact_metrics(m),
            "matrix": np.asarray(leaf["matrix"], dtype=float).round(9).tolist(),
            "centroid": np.asarray(m.vertices, dtype=float).mean(axis=0).round(6).tolist(),
        })

    transform_cmp = {"comparable": len(leaves) == 2}
    if len(leaves) == 2:
        diff = np.abs(leaves[0]["matrix"] - leaves[1]["matrix"])
        transform_cmp.update({
            "identical_within_1e-6": bool(np.max(diff) <= 1e-6),
            "max_matrix_difference": float(np.max(diff)),
            "leaf0_translation": leaves[0]["matrix"][3, :3].round(6).tolist(),
            "leaf1_translation": leaves[1]["matrix"][3, :3].round(6).tolist(),
        })

    centroid_distance = None
    if len(world_meshes) == 2:
        c0 = np.asarray(world_meshes[0].vertices).mean(axis=0)
        c1 = np.asarray(world_meshes[1].vertices).mean(axis=0)
        centroid_distance = float(np.linalg.norm(c1 - c0))

    combined = trimesh.util.concatenate(world_meshes)
    try:
        combined.merge_vertices()
    except Exception:
        pass

    pieces = combined.split(only_watertight=False)
    comp_rows = []
    for i, piece in enumerate(pieces):
        cm = contact_metrics(piece)
        comp_rows.append({
            "component_index": i,
            "bounds": np.asarray(piece.bounds, dtype=float).round(6).tolist(),
            "min_z": cm["min_z"],
            "vertices_near_bed": cm["vertices_near_bed"],
            "bed_triangle_area_mm2": cm["bed_triangle_area_mm2"],
            "floating_candidate": cm["min_z"] is not None and cm["min_z"] > 0.05,
        })

    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "root_part": root_part,
        "leaf_count": len(leaves),
        "leaves": leaf_rows,
        "transform_comparison": transform_cmp,
        "leaf_centroid_distance": centroid_distance,
        "combined_component_count": len(comp_rows),
        "components": comp_rows,
        "floating_candidates": [r["component_index"] for r in comp_rows if r["floating_candidate"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-stl", required=True)
    parser.add_argument("--region-stl", action="append", required=True)
    parser.add_argument("--r2-project", required=True)
    parser.add_argument("--r4-project", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    if len(args.region_stl) != 2:
        raise GeometryAuditError("Exactly two --region-stl arguments are required.")

    source_path = Path(args.source_stl).resolve()
    region_paths = [Path(x).resolve() for x in args.region_stl]
    r2_path = Path(args.r2_project).resolve()
    r4_path = Path(args.r4_project).resolve()
    report_path = Path(args.report).resolve()

    for p in [source_path, *region_paths, r2_path, r4_path]:
        if not p.is_file():
            raise GeometryAuditError(f"Missing input: {p}")

    source = load_mesh(source_path)
    regions = [load_mesh(p) for p in region_paths]
    source_summary = mesh_summary(source)
    region_summaries = [mesh_summary(m) for m in regions]

    source_vol = abs(float(source.volume))
    region_vol_sum = sum(abs(float(m.volume)) for m in regions)
    volume_error = None if source_vol <= 1e-12 else abs(region_vol_sum - source_vol) / source_vol

    planes = partition_planes(source, regions[0], regions[1])

    c0 = np.asarray(regions[0].vertices).mean(axis=0)
    c1 = np.asarray(regions[1].vertices).mean(axis=0)
    r1_centroid_distance = float(np.linalg.norm(c1 - c0))

    r2 = project_audit(r2_path)
    r4 = project_audit(r4_path)

    flags = []
    if planes:
        flags.append("R1_AXIS_PLANE_PARTITION_DETECTED")

    for label, data in (("R2", r2), ("R4", r4)):
        tc = data["transform_comparison"]
        if tc.get("comparable") and not tc.get("identical_within_1e-6"):
            flags.append(f"{label}_RELATIVE_TRANSFORM_BROKEN")
        if data["floating_candidates"]:
            flags.append(f"{label}_FLOATING_COMPONENT_CANDIDATE")

    report = {
        "schema_version": "r1g-geometry-forensic-v2",
        "mode": "READ_ONLY_FORENSIC",
        "source": {"path": str(source_path), "sha256": sha256_file(source_path), **source_summary},
        "regions": [{"path": str(p), "sha256": sha256_file(p), **s} for p, s in zip(region_paths, region_summaries)],
        "r1_partition_analysis": {
            "axis_plane_candidates": planes,
            "source_volume": source_vol,
            "region_volume_sum": region_vol_sum,
            "volume_error_ratio": volume_error,
            "region_centroid_distance": r1_centroid_distance,
        },
        "r2": r2,
        "r4": r4,
        "root_cause_flags": flags,
        "safety": {
            "files_modified": False,
            "network_used": False,
            "printer_contacted": False,
            "artifact_uploaded": False,
            "print_started": False,
        },
    }

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=== R1G GEOMETRY FORENSIC AUDIT ===")
    print("MODE=READ_ONLY")
    print("SOURCE_COMPONENTS=", source_summary["connected_components"])
    print("SOURCE_BOUNDS=", source_summary["bounds"])

    for i, s in enumerate(region_summaries):
        print(f"REGION_{i}_BOUNDS=", s["bounds"])
        print(f"REGION_{i}_VOLUME=", s["volume_abs"])

    print("R1_PARTITION_PLANES=", planes)
    print("R1_VOLUME_ERROR_RATIO=", volume_error)
    print("R1_REGION_CENTROID_DISTANCE=", r1_centroid_distance)

    for label, data in (("R2", r2), ("R4", r4)):
        print()
        print(f"=== {label} ===")
        print(f"{label}_LEAF_COUNT=", data["leaf_count"])
        print(f"{label}_TRANSFORM_COMPARISON=", data["transform_comparison"])
        print(f"{label}_LEAF_CENTROID_DISTANCE=", data["leaf_centroid_distance"])
        print(f"{label}_COMBINED_COMPONENT_COUNT=", data["combined_component_count"])
        print(f"{label}_FLOATING_CANDIDATES=", data["floating_candidates"])

        for leaf in data["leaves"]:
            print(f"{label}_LEAF_{leaf['leaf_index']}_BOUNDS=", leaf["world_bounds"])
            print(f"{label}_LEAF_{leaf['leaf_index']}_CONTACT=", leaf["contact"])

    print()
    print("ROOT_CAUSE_FLAGS=", flags)
    print("FILES_MODIFIED=False")
    print("NETWORK_USED=False")
    print("PRINTER_CONTACTED=False")
    print("PRINT_STARTED=False")
    print("REPORT=", report_path)
    print("R1G_FORENSIC_COMPLETE=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
