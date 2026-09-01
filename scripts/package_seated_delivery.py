"""Deliver only a reviewed upright model and its actual accepted support paths."""
from pathlib import Path
import argparse
import json
import hashlib
import zipfile
import trimesh
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.semantic_pose_gate import inspect_upright_source_preserved
from am_print_executor.slice_geometry_frame import verify_source_matches_slice

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('preparation',type=Path)
parser.add_argument('--regressions',type=Path,required=True)
args=parser.parse_args()
base=args.preparation.resolve()
result=json.loads((base/'result.json').read_text(encoding='utf-8'))
receipt=result['acceptance']
pose_source=json.loads((base.parent/'pose_source.json').read_text(encoding='utf-8'))
regressions=json.loads(args.regressions.read_text(encoding='utf-8'))
assert result['status']=='slice_complete' and receipt['status']=='pass' and regressions['passed']
assert pose_source['status']=='anatomical_pose_reviewed'
assert hashlib.sha256(Path(pose_source['source']).read_bytes()).hexdigest()==pose_source['sha256']
geometry=Path(receipt['geometry']); artifact=Path(receipt['artifact']); project=Path(receipt['project'])
mesh=trimesh.load(geometry,force='mesh',process=True)
pose=inspect_upright_source_preserved(Path(receipt['source']),project)
gate=inspect_final_gcode_printability(artifact,geometry_path=geometry)
flat=inspect_flat_printing_base(mesh)
verify_source_matches_slice(mesh,artifact)
assert pose['status']=='pass' and gate['status']=='pass' and flat['base_flatness_passed']
assert abs(mesh.extents[2]-30)<1e-4
out=Path(__file__).resolve().parents[1]/'outputs/printable_delivery/20260831_mouse_seated_v2'
out.mkdir(parents=True,exist_ok=False)
files={'mouse_seated_30mm.stl':geometry,'mouse_seated_30mm.3mf':project,'mouse_seated_30mm.gcode.3mf':artifact,
       'seated_model_and_supports.png':base/'seated_model_and_supports.png','ear_support_detail.png':base/'ear_support_detail.png'}
hashes={}
for name,path in files.items():
    data=path.read_bytes(); (out/name).write_bytes(data); hashes[name]=hashlib.sha256(data).hexdigest()
report={'status':'pass','supersedes':'20260831_mouse_verified (rejected: back was treated as the bottom)',
        'semantic_pose':pose_source,'orientation_preservation':pose,'flat_base':flat,'strict_gate':gate,
        'before_orientation_status':receipt['before_orientation_status'],'after_orientation_status':receipt['after_orientation_status'],
        'source_glb_unchanged':True,'hashes':hashes,'regressions':regressions,
        'physical_print_performed':False,'printer_contacted':False,'preparation_directory':str(base)}
(out/'acceptance.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
(out/'打印说明.md').write_text(f'''# 小鼠自然坐姿版 V2

本版替代之前背部朝下的错误版本。之前的版本不应再作为本任务的完成结果。

请打开 **mouse_seated_30mm.3mf**。这是已经实际执行 Auto Orient 的 Bambu 工程。**mouse_seated_30mm.gcode.3mf** 包含最终通过检查的实际切片，可直接查看支撑预览；STL 不携带打印支撑设置。

## 本次真正修正的内容

- 正确坐姿：屁股和后足朝下，脸朝前，两只耳朵朝上。
- 修复了 GLB 的 +Y 向上与打印 +Z 向上之间缺失的转换。高度按正确方向恢复为 30 mm。
- 平底在屁股下方，保留小型底座；少量永久连接结构只处理底部和低处尾巴，没有上一版的背部托架与通向耳朵的永久立柱。
- 耳根和下巴由切片器生成可拆支撑。图中蓝色是模型，绿色是实际 G-code 支撑，橙色是支撑接触界面。
- Auto Orient 必须保持模型原有向上方向，本次测得倾斜 {pose['tilt_degrees']:.6f}°。在台面上转方向允许，翻到背部不允许。
- Auto Orient 前、后真实切片严格检查均通过。最终危险层 {gate['dangerous_layer_count']}，悬空起始岛 {gate['unsupported_layer_island_count']}，未落地支撑 {gate['unanchored_support_component_count']}。
- 平底面积 {flat['bed_contact_area_mm2']:.2f} mm²，高差 {flat['base_height_range_mm']:.6f} mm。{regressions['tests']} 项相关回归测试通过。

## 使用条件

Bambu Lab X1 Carbon，0.4 mm 喷嘴，PLA，0.20 mm 层高，100% 填充，Normal Auto 支撑，30° 阈值。**必须保留支撑。此版没有声称耳朵可以无支撑打印。** 改尺寸、打印机、材料或切片设置后应重新验证。

本次未上传、启动或进行实体试打。软件检查不能证明现场的首层粘附、耗材状态和打印机校准已经合格。

技术依据：[glTF 坐标约定](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#coordinate-system-and-units)。实际文件及检查哈希见 acceptance.json。
''',encoding='utf-8')
with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(out.iterdir()):archive.write(path,path.name)
print(json.dumps({'status':'pass','directory':str(out),'geometry':str(out/'mouse_seated_30mm.stl'),'zip':str(out.with_suffix('.zip'))},indent=2),flush=True)
