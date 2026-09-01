"""Visualize accepted geometry and actual support toolpaths, not invented supports."""
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

from am_print_executor.gcode_support_continuity import parse_extrusion_segments
from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame


def load_accepted(directory):
    result = json.loads((directory/'result.json').read_text(encoding='utf-8'))
    receipt = result['acceptance']
    assert result['status']=='slice_complete' and receipt['status']=='pass' and receipt['removal']['status']=='pass'
    mesh = trimesh.load(receipt['geometry'],force='mesh',process=True)
    cumulative = np.eye(4)
    for path in sorted(directory.glob('orientation_*/auto_orient.json')):
        orientation = json.loads(path.read_text(encoding='utf-8'))
        cumulative = cumulative @ np.asarray(orientation['output']['upright_pose']['source_to_world_row_vector'])
    inverse = np.linalg.inv(cumulative)
    mesh.apply_transform(inverse.T)
    shift = np.r_[mesh.bounds.mean(axis=0)[:2],mesh.bounds[0,2]]
    mesh.apply_translation(-shift)
    offset = np.asarray(gate_report_in_geometry_frame({},Path(receipt['artifact']))['coordinate_mapping']['translation_xy_mm'])
    with zipfile.ZipFile(receipt['artifact']) as archive:
        text = archive.read('Metadata/plate_1.gcode').decode('utf-8')
    support, model, interface, widths = [], [], [], []
    for s in parse_extrusion_segments(text):
        is_support = 'support' in s.feature
        if not is_support and s.feature not in {'outer wall','overhang wall','bottom surface','bridge'}:
            continue
        points = np.array([[s.x1+offset[0],s.y1+offset[1],s.z,1],[s.x2+offset[0],s.y2+offset[1],s.z,1]])
        points = (points @ inverse)[:,:3]-shift
        if is_support:
            support.append(points);widths.append(s.line_width_mm)
            if 'interface' in s.feature:interface.append(points)
        else:model.append(points)
    return dict(mesh=mesh,model=np.asarray(model),support=np.asarray(support),interface=np.asarray(interface),
        widths=widths,receipt=receipt)


def draw(ax,data,elevation,azimuth,label,paths):
    mesh = data['mesh']
    if paths:
        ax.add_collection3d(Line3DCollection(data['model'],colors='#357ea3',linewidths=.3,alpha=.43))
        ax.add_collection3d(Line3DCollection(data['support'],colors='#13804b',linewidths=.7,alpha=.8))
        ax.add_collection3d(Line3DCollection(data['interface'],colors='#d48016',linewidths=.9,alpha=.9))
    else:
        light = np.clip(.7+.3*mesh.face_normals@np.array([.2,-.7,.65]),.25,1)
        colors = np.c_[light*.40,light*.67,light*.85,np.ones(len(light))]
        el,az=np.radians([elevation,azimuth])
        camera=np.array([np.cos(el)*np.cos(az),np.cos(el)*np.sin(az),np.sin(el)])
        front=mesh.face_normals@camera>1e-8
        ax.add_collection3d(Poly3DCollection(mesh.triangles[front],facecolors=colors[front],linewidths=0))
    radius = max(mesh.extents)*.54
    ax.set(xlim=(-radius,radius),ylim=(-radius,radius),zlim=(0,radius*2),title=label)
    ax.set_box_aspect((1,1,1));ax.set_axis_off();ax.view_init(elev=elevation,azim=azimuth)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path);p.add_argument('--coupon',type=Path,required=True)
    args = p.parse_args()
    model,coupon = load_accepted(args.directory),load_accepted(args.coupon)
    plt.rcParams['font.family']='Microsoft YaHei'
    fig = plt.figure(figsize=(12,11),facecolor='#f4f6f8')
    views = [(model,12,-65,'成品外观：坐姿和平底，耳下没有永久立柱',False),
        (model,0,-90,'实际切片 · 正面：支撑从下方托住耳朵',True),
        (model,0,0,'实际切片 · 侧面：支撑与成品分开',True),
        (coupon,12,-65,'双薄片小样：同一可拆支撑配置，已切片验收',True)]
    for i,view in enumerate(views,1):
        ax=fig.add_subplot(2,2,i,projection='3d',facecolor='#f4f6f8')
        draw(ax,*view)
    gap = model['receipt']['removal']['minimum_measured_contact_gap_mm']
    width = max(model['widths'])
    fig.suptitle('V4 可拆支撑 | 真实 Auto Orient 后复验通过',fontsize=17,color='#244758')
    fig.text(.5,.044,'蓝：成品   绿：临时支撑   橙：接触界面（均来自实际切片）',ha='center',fontsize=12)
    fig.text(.5,.019,f'最小接触间隙 {gap:.2f} mm · 支撑线宽最大 {width:.2f} mm · 未进行实体打印/拆卸试验',ha='center',fontsize=10,color='#615443')
    fig.tight_layout(rect=(0,.07,1,.96))
    path=args.directory/'detachable_supports.png';fig.savefig(path,dpi=160);plt.close(fig)
    metrics={'support_line_width_min_mm':min(model['widths']),'support_line_width_max_mm':width,
        'support_line_width_median_mm':float(np.median(model['widths'])),
        'support_segment_count':len(model['support']),
        'support_xy_length_mm':float(np.linalg.norm(np.diff(model['support'][:,:,:2],axis=1),axis=2).sum()),
        'minimum_contact_clearance_mm':gap,'physical_removal_verified':False}
    (args.directory/'support_path_metrics.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
    print(path.resolve(),flush=True)


if __name__=='__main__':main()
