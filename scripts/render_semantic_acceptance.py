"""Render the actual seated mesh AND real slicer supports in its front frame."""
from pathlib import Path
import argparse
import json
import zipfile
import numpy as np
import trimesh
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection, Line3DCollection
from matplotlib.collections import LineCollection
from am_print_executor.gcode_support_continuity import parse_extrusion_segments
from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory',type=Path)
args=parser.parse_args()
base=args.directory.resolve()
result=json.loads((base/'result.json').read_text(encoding='utf-8'))
receipt=result['acceptance']
assert receipt['status']=='pass'
mesh=trimesh.load(receipt['geometry'],force='mesh',process=True)
cumulative=np.eye(4)
for path in sorted(base.glob('orientation_*/auto_orient.json')):
    record=json.loads(path.read_text(encoding='utf-8'))
    cumulative=cumulative @ np.array(record['output']['upright_pose']['source_to_world_row_vector'])
inverse=np.linalg.inv(cumulative)
mesh.apply_transform(inverse.T)
shift=np.r_[mesh.bounds.mean(axis=0)[:2],0.]
mesh.apply_translation(-shift)
artifact=Path(receipt['artifact'])
offset=np.array(gate_report_in_geometry_frame({},artifact)['coordinate_mapping']['translation_xy_mm'])
with zipfile.ZipFile(artifact) as archive:
    entry=next(n for n in archive.namelist() if n.endswith('.gcode'))
    segments=parse_extrusion_segments(archive.read(entry).decode('utf-8',errors='replace'))
model_lines=[]
support_lines=[]
interface_lines=[]
for s in segments:
    if not ('support' in s.feature or s.feature in {'outer wall','overhang wall','bottom surface'}):
        continue
    points=np.array([[s.x1+offset[0],s.y1+offset[1],s.z,1.],[s.x2+offset[0],s.y2+offset[1],s.z,1.]])
    points=(points @ inverse)[:,:3]-shift
    if np.any(points[:,:2]<mesh.bounds[0,:2]-3) or np.any(points[:,:2]>mesh.bounds[1,:2]+3):
        continue
    if 'support' in s.feature:
        support_lines.append(points)
        if 'interface' in s.feature:
            interface_lines.append(points)
    else:
        model_lines.append(points)
model_lines=np.array(model_lines)
support_lines=np.array(support_lines)
interface_lines=np.array(interface_lines)
fig=plt.figure(figsize=(12,10),facecolor='#f5f7fa')
views=[(0,-90,'Front: rump down, ears up'),(0,0,'Side: seated on its bottom'),(20,-55,'Actual delivered geometry'),(15,-70,'Actual sliced toolpaths: green = removable supports')]
for i,(elev,azim,title) in enumerate(views,1):
    ax=fig.add_subplot(2,2,i,projection='3d',facecolor='#f5f7fa')
    if i<4:
        shade=np.clip(.65+.35*mesh.face_normals@np.array([.2,-.7,.68]),.25,1)
        colors=np.c_[shade*.45,shade*.72,shade*.94,np.ones(len(shade))]
        colors[mesh.triangles[:,:,2].max(axis=1)<.001]=[1,.58,.15,1]
        ax.add_collection3d(Poly3DCollection(mesh.triangles,facecolors=colors,linewidth=0))
    else:
        ax.add_collection3d(Line3DCollection(model_lines,colors='#368fc2',linewidths=.4,alpha=.65))
        ax.add_collection3d(Line3DCollection(support_lines,colors='#258953',linewidths=.9,alpha=.9))
    radius=max(mesh.extents)*.55
    ax.set(xlim=(-radius,radius),ylim=(-radius,radius),zlim=(0,radius*2),title=title)
    ax.set_box_aspect((1,1,1)); ax.set_axis_off(); ax.view_init(elev=elev,azim=azim)
fig.suptitle('Corrected sitting pose | Auto Orient preserves up | Final sliced gate: PASS',fontsize=13)
fig.tight_layout(); fig.savefig(base/'seated_model_and_supports.png',dpi=150)
plt.close(fig)

fig,axes=plt.subplots(1,2,figsize=(13,6))
for ax,dimension,title in zip(axes,(0,1),('Front projection: real support under ear roots / chin','Side projection: real support reaches the underside')):
    upper_model=model_lines[np.max(model_lines[:,:,2],axis=1)>12]
    upper_support=support_lines[np.max(support_lines[:,:,2],axis=1)>12]
    upper_interface=interface_lines[np.max(interface_lines[:,:,2],axis=1)>12]
    ax.add_collection(LineCollection(upper_model[:,:,[dimension,2]],colors='#4893bd',linewidths=.35,alpha=.5))
    ax.add_collection(LineCollection(upper_support[:,:,[dimension,2]],colors='#16823d',linewidths=1,alpha=.8))
    ax.add_collection(LineCollection(upper_interface[:,:,[dimension,2]],colors='#df8216',linewidths=1.2))
    ax.set(xlim=(mesh.bounds[0,dimension]-2,mesh.bounds[1,dimension]+2),ylim=(12,31),xlabel='mm',ylabel='Height mm',title=title)
    ax.set_aspect('equal'); ax.grid(alpha=.2)
fig.suptitle('Actual final G-code | Blue: model | Green: removable support | Orange: contact interface',fontsize=12)
fig.tight_layout(); fig.savefig(base/'ear_support_detail.png',dpi=160)
print(base/'seated_model_and_supports.png',flush=True)
print(base/'ear_support_detail.png',flush=True)
