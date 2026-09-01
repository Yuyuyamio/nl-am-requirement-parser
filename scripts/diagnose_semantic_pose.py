"""Read-only views of possible coordinate conventions; no slicer or mesh repair."""
from pathlib import Path
import json
import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

root = Path(__file__).resolve().parents[1]
source = root / "outputs/flat_base_acceptance/AUTO-20260825-143340-FLATBASE-RESUME/current_mouse.repaired.bed_centered.flat_base.stl"
mesh = trimesh.load(source, force="mesh", process=True)
mesh.apply_translation(-mesh.bounds.mean(axis=0))
fig = plt.figure(figsize=(13, 15))
for row, angle in enumerate((0, 45, 90, -90)):
    candidate = mesh.copy()
    candidate.apply_transform(trimesh.transformations.rotation_matrix(np.deg2rad(angle), [1, 0, 0]))
    candidate.apply_translation(-np.r_[candidate.bounds.mean(axis=0)[:2], candidate.bounds[0, 2]])
    for column, (elevation, azimuth) in enumerate(((12, -90), (12, 0), (24, -55))):
        ax = fig.add_subplot(4, 3, row*3+column+1, projection="3d")
        shade = np.clip(.65 + .35 * candidate.face_normals @ np.array([.3,-.6,.7]), .2, 1)
        colors = np.c_[shade*.55, shade*.8, shade*.95, np.ones(len(shade))]
        ax.add_collection3d(Poly3DCollection(candidate.triangles, facecolors=colors, linewidth=0))
        radius = max(candidate.extents)*.55
        ax.set(xlim=(-radius,radius), ylim=(-radius,radius), zlim=(0,2*radius), title=f"Rotate X {angle:+d} deg | view {column+1}")
        ax.set_box_aspect((1,1,1))
        ax.view_init(elev=elevation,azim=azimuth)
        ax.set_axis_off()
fig.suptitle("Pose diagnosis only — no acceptance claim",fontsize=16)
fig.tight_layout()
out = root / "outputs/semantic_pose_repair"
out.mkdir(parents=True,exist_ok=True)
fig.savefig(out / "coordinate_pose_views.png",dpi=135)
print(out / "coordinate_pose_views.png",flush=True)
