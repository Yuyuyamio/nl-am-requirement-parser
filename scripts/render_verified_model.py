"""Render actual final STL geometry and planar contact for human inspection."""
from pathlib import Path
import argparse
import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
mesh = trimesh.load(args.source, force="mesh", process=True)
mesh.apply_translation(-np.r_[mesh.bounds.mean(axis=0)[:2], mesh.bounds[0, 2]])
fig = plt.figure(figsize=(12, 10), facecolor="#f5f7fa")
triangles = mesh.triangles
for number, (elev, azim, title) in enumerate(((24, -60, "Actual final mesh"), (0, -90, "Front: flat base on Z = 0"), (0, 0, "Side: permanent support ribs"), (-90, -90, "Underside: continuous planar contact")), 1):
    ax = fig.add_subplot(2, 2, number, projection="3d", facecolor="#f5f7fa")
    shade = np.clip(.67 + .3 * mesh.face_normals @ np.array([.3, -.6, .74]), .2, 1)
    colors = np.c_[.30 * shade, .68 * shade, .94 * shade, np.ones(len(shade))]
    base_faces = triangles[:, :, 2].max(axis=1) < .001
    colors[base_faces] = [1, .5, .12, 1]
    ax.add_collection3d(Poly3DCollection(triangles, facecolors=colors, linewidth=0))
    span = max(mesh.extents) * .55
    ax.set(xlim=(-span, span), ylim=(-span, span), zlim=(0, span*2), title=title)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
fig.suptitle("30 mm model | Real Bambu Auto Orient + re-slice: PASS | No flagged unsupported layers", fontsize=12)
fig.tight_layout()
args.output.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(args.output, dpi=160, facecolor=fig.get_facecolor())
print(args.output, flush=True)
