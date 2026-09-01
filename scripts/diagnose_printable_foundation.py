"""Read-only planar contact audit and mesh views after manual rejection."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
import trimesh
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from am_print_executor.flat_base_gate import inspect_flat_printing_base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/printable_foundation_diagnosis"


def audit(mesh):
    triangles = mesh.triangles
    z0 = float(mesh.bounds[0, 2])
    center = mesh.center_mass
    results = {"legacy_gate": inspect_flat_printing_base(mesh), "center_of_mass": center.tolist(), "contact_bands": {}}
    for band in (1e-5, 0.001, 0.01, 0.05, 0.1, 0.2):
        mask = triangles[:, :, 2].max(axis=1) <= z0 + band
        regions = [Polygon(t[:, :2]) for t in triangles[mask] if Polygon(t[:, :2]).area > 1e-10]
        footprint = unary_union(regions)
        results["contact_bands"][str(band)] = {
            "projected_union_area_mm2": footprint.area, "triangle_count": int(mask.sum()),
            "com_xy_inside_contact": bool(footprint.covers(Point(center[:2]))),
            "com_distance_to_contact_mm": float(footprint.distance(Point(center[:2]))),
            "max_triangle_tilt_degrees": float(np.degrees(np.arccos(np.clip(np.abs(mesh.face_normals[mask, 2]), 0, 1))).max()) if mask.any() else None,
        }
    return results


def render(mesh, destination):
    local = mesh.copy()
    local.apply_translation(-local.bounds.mean(axis=0))
    triangles = local.triangles
    fig = plt.figure(figsize=(14, 11))
    for index, (elev, azim, label) in enumerate(((20, -65, "Perspective"), (0, -90, "Front"), (0, 0, "Side"), (-90, -90, "Underside")), 1):
        ax = fig.add_subplot(2, 2, index, projection="3d")
        normals = local.face_normals
        light = np.clip(.6 + .35 * normals @ np.array([.2, -.6, .7]), .2, 1)
        colors = np.column_stack([light * .66, light * .8, light * .91, np.ones(len(light))])
        near_base = triangles[:, :, 2].max(axis=1) < local.bounds[0, 2] + .05
        colors[near_base] = [1, .22, .1, 1]
        ax.add_collection3d(Poly3DCollection(triangles, facecolors=colors, linewidth=0, rasterized=True))
        radius = max(local.extents) * .55
        ax.set(xlim=(-radius, radius), ylim=(-radius, radius), zlim=(-radius, radius), title=label)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
    fig.suptitle("Current mesh — red marks the old 0.05 mm contact band")
    fig.tight_layout()
    fig.savefig(destination, dpi=145)
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = ROOT / "outputs/flat_base_acceptance/AUTO-20260825-143340-FLATBASE-RESUME/current_mouse.repaired.bed_centered.flat_base.stl"
    mesh = trimesh.load(source, force="mesh", process=True)
    result = audit(mesh)
    result["source"] = str(source)
    (OUT / "contact_audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    render(mesh, OUT / "current_mouse_views.png")
    print(json.dumps(result["contact_bands"], indent=2), flush=True)


if __name__ == "__main__":
    main()
