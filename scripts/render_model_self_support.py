"""Render accepted geometry and actual support-free toolpaths, without invented supports."""
from pathlib import Path
import argparse,json,zipfile
import numpy as np
import trimesh
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection,Line3DCollection
from am_model_generator.coordinate_frame import load_print_scene
from am_print_executor.gcode_support_continuity import parse_extrusion_segments
from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame

p=argparse.ArgumentParser();p.add_argument('directory',type=Path);a=p.parse_args()
base=a.directory.resolve()
result=json.loads((base/'result.json').read_text(encoding='utf-8'))
receipt=result['acceptance'];assert receipt['status']=='pass'
config=json.loads((base/'preparation.json').read_text(encoding='utf-8'))
gate=json.loads((Path(receipt['artifact']).parent/'gate.json').read_text(encoding='utf-8'))
assert gate['status']=='pass' and gate['support_xy_length_mm']==0 and gate['policy']['credit_removable_support'] is False
mesh=trimesh.load(receipt['geometry'],force='mesh',process=True)
inverse=np.eye(4)
for path in sorted(base.glob('orientation_*/auto_orient.json')):
    record=json.loads(path.read_text(encoding='utf-8'))
    inverse=inverse@np.array(record['output']['upright_pose']['source_to_world_row_vector'])
inverse=np.linalg.inv(inverse);mesh.apply_transform(inverse.T)
prepared=trimesh.load(base/'prepared.stl',force='mesh',process=True)
original=load_print_scene(Path(config['source'])).to_mesh();original.apply_scale(config['uniform_input_scale'])
original.apply_translation(np.r_[prepared.bounds.mean(axis=0)[:2]-original.bounds.mean(axis=0)[:2],-original.bounds[0,2]])
shift=np.r_[mesh.bounds.mean(axis=0)[:2],0.]
mesh.apply_translation(-shift);original.apply_translation(-shift)
dist=np.concatenate([trimesh.proximity.closest_point_naive(original,chunk)[1]
                     for chunk in np.array_split(mesh.triangles_center,max(1,len(mesh.faces)//24))])
shade=np.clip(.7+.3*mesh.face_normals@np.array([.2,-.7,.68]),.35,1)
colors=np.c_[shade*.4,shade*.69,shade*.9,np.ones(len(shade))]
colors[dist>.04]=np.c_[shade[dist>.04]*.96,shade[dist>.04]*.57,shade[dist>.04]*.16,np.ones(sum(dist>.04))]
artifact=Path(receipt['artifact'])
offset=np.array(gate_report_in_geometry_frame({},artifact)['coordinate_mapping']['translation_xy_mm'])
with zipfile.ZipFile(artifact) as archive:
    text=archive.read(next(n for n in archive.namelist() if n.endswith('.gcode'))).decode('utf-8',errors='replace')
lines=[]
for s in parse_extrusion_segments(text):
    if s.feature not in {'outer wall','overhang wall','bottom surface'}:continue
    pts=np.array([[s.x1+offset[0],s.y1+offset[1],s.z,1],[s.x2+offset[0],s.y2+offset[1],s.z,1]])
    pts=(pts@inverse)[:,:3]-shift
    if np.all(pts[:,:2]>=mesh.bounds[0,:2]-1) and np.all(pts[:,:2]<=mesh.bounds[1,:2]+1):lines.append(pts)
plt.rcParams['font.sans-serif']=['Microsoft YaHei'];plt.rcParams['axes.unicode_minus']=False
fig=plt.figure(figsize=(14,8),facecolor='#f5f7fa')
for i,(elev,azim,title) in enumerate([(0,-90,'正面：耳下承托已并入模型'),(0,0,'侧面：屁股落地，承托连接主体'),
                                     (20,-55,'橙色：新增的永久承托和底座'),(12,-70,'实际切片：可拆支撑为 0')],1):
    ax=fig.add_subplot(2,2,i,projection='3d',facecolor='#f5f7fa')
    if i<4:ax.add_collection3d(Poly3DCollection(mesh.triangles,facecolors=colors,linewidths=0))
    else:ax.add_collection3d(Line3DCollection(lines,colors='#3589b5',linewidths=.3,alpha=.7))
    radius=max(mesh.extents)*.53
    ax.set(xlim=(-radius,radius),ylim=(-radius,radius),zlim=(0,2*radius),title=title)
    ax.set_box_aspect((1,1,1));ax.set_axis_off();ax.view_init(elev=elev,azim=azim)
fig.suptitle('模型本体承托版｜0.12 mm 无支撑切片通过｜自动摆放后复验通过',fontsize=14)
fig.tight_layout();fig.savefig(base/'model_self_support.png',dpi=160)
print(base/'model_self_support.png',flush=True)
