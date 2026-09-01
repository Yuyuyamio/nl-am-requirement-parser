"""Render real pending geometry; do not fabricate ungenerated slicer supports."""
from pathlib import Path
import argparse
import trimesh
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('directory',type=Path)
a = p.parse_args()
source = next((a.directory/'main').glob('verified_preparation_*/prepared.stl'))
coupon = a.directory/'removal_coupon_UNVERIFIED.stl'
plt.rcParams['font.family'] = 'Microsoft YaHei'
fig = plt.figure(figsize=(12,5.4),facecolor='#f4f6f8')
for i,(path,elev,azim,label) in enumerate([(source,6,-90,'小鼠成品候选 · 正面'),
        (source,6,0,'小鼠成品候选 · 侧面'),(coupon,12,-65,'拆卸小样 · 双薄片')],1):
    mesh = trimesh.load(path,force='mesh',process=True)
    mesh.apply_translation(-np.r_[mesh.bounds.mean(axis=0)[:2],mesh.bounds[0,2]])
    ax = fig.add_subplot(1,3,i,projection='3d',facecolor='#f4f6f8')
    lighting = np.clip(.7+.3*mesh.face_normals@np.array([.2,-.7,.65]),.25,1)
    colors = np.c_[lighting*.40,lighting*.67,lighting*.85,np.ones(len(lighting))]
    ax.add_collection3d(Poly3DCollection(mesh.triangles,facecolors=colors,linewidths=0))
    radius = max(mesh.extents)*.56
    ax.set(xlim=(-radius,radius),ylim=(-radius,radius),zlim=(0,2*radius),title=label)
    ax.set_box_aspect((1,1,1));ax.set_axis_off();ax.view_init(elev=elev,azim=azim)
fig.suptitle('几何候选，尚未完成切片验收',fontsize=17,color='#9a5315')
fig.text(.5,.035,'图中仅为成品几何；临时支撑须由切片器生成。旧版粗承托未带入。',ha='center',fontsize=11)
fig.tight_layout(rect=(0,.08,1,.95))
output = a.directory/'candidate_UNVERIFIED.png'
fig.savefig(output,dpi=160);plt.close(fig)
print(output.resolve())
