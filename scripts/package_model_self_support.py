"""Package only the exact no-removable-support artifact that was checked."""
from pathlib import Path
import argparse,hashlib,json,zipfile
import trimesh
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.slice_geometry_frame import verify_source_matches_slice
from am_print_executor.semantic_pose_gate import inspect_upright_source_preserved

p=argparse.ArgumentParser();p.add_argument('directory',type=Path)
p.add_argument('--regressions',type=Path,required=True)
p.add_argument('--cases',type=Path,required=True)
a=p.parse_args();base=a.directory.resolve();root=Path(__file__).resolve().parents[1]
result=json.loads((base/'result.json').read_text(encoding='utf-8'))
receipt=result['acceptance'];assert result['status']=='slice_complete' and receipt['status']=='pass'
config=json.loads((base/'preparation.json').read_text(encoding='utf-8'))
tests=json.loads(a.regressions.read_text(encoding='utf-8'));assert tests['passed']
cases=json.loads(a.cases.read_text(encoding='utf-8'));assert cases['completed']==cases['planned']
geometry=Path(receipt['geometry']);artifact=Path(receipt['artifact']);project=Path(receipt['project'])
mesh=trimesh.load(geometry,force='mesh',process=True)
gate=inspect_final_gcode_printability(artifact,geometry_path=geometry,credit_removable_support=False)
flat=inspect_flat_printing_base(mesh);pose=inspect_upright_source_preserved(Path(receipt['source']),project)
verify_source_matches_slice(mesh,artifact)
assert gate['status']=='pass' and gate['support_xy_length_mm']==0 and flat['base_flatness_passed'] and pose['status']=='pass'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
original=Path(config['source']);assert sha(original)==config['source_sha256']
repeat=next(row for row in cases['cases'] if row['name']=='renamed_repeat')
assert repeat['status']=='pass' and repeat['geometry_sha256']==sha(geometry) and repeat['source_unchanged']
out=root/'outputs/printable_delivery/20260831_model_self_support_v3';out.mkdir(parents=True,exist_ok=False)
files={'mouse_30mm_self_supported.stl':geometry,'mouse_30mm_self_supported.3mf':project,
       'mouse_30mm_self_supported.gcode.3mf':artifact,'model_self_support.png':base/'model_self_support.png',
       'source.glb':original,'回归测试.json':a.regressions,'不同模型实测.json':a.cases,
       '通用修复与复现说明.md':root/'docs/MODEL_SELF_SUPPORT.md'}
other=next(row for row in cases['cases'] if row['name']=='organic_head_on_neck')
assert other['status']=='pass'
files['other_shape_self_supported.stl']=Path(other['geometry_path'])
files['other_shape_self_supported.3mf']=Path(other['acceptance']['project'])
hashes={}
for name,path in files.items():
    (out/name).write_bytes(path.read_bytes());hashes[name]=sha(out/name)
code_paths=['src/am_print_executor/model_buttresses.py','src/am_print_executor/verified_print_preparation.py',
            'src/am_print_executor/gcode_printability_gate.py','src/am_print_automation/workflow.py',
            'src/am_model_generator/coordinate_frame.py','scripts/prepare_verified_print.py']
report={'status':'pass','scope':'accepted_mouse_artifact_only; see case table for unresolved shapes',
        'source_sha256':config['source_sha256'],'source_unchanged':True,'hashes':hashes,
        'strict_model_only_gate':gate,'flat_base':flat,'upright_pose':pose,
        'reproducibility':{'renamed_input_same_stl_bytes':True,'stl_sha256':sha(geometry)},
        'unit_regressions':tests,'all_benchmark_cases_passed':cases['passed'],
        'code_sha256':{path:sha(root/path) for path in code_paths},
        'physical_print_performed':False,'printer_contacted':False}
(out/'acceptance.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
(out/'打印说明.md').write_text('''# 模型本体承托版 V3

本版替代之前仍依赖耳下可拆支撑的 V2。打开 mouse_30mm_self_supported.3mf。

- 耳下和下巴等部位已加入与主体或底座合并的永久承托；橙色预览标出改动。它们是模型的一部分，不能当废料拆掉。
- 保持屁股向下坐姿；实际 Auto Orient 后重新切片通过。
- 实测：X1 Carbon / 0.4 mm / PLA / 0.12 mm 层高 / 100% 填充 / 关闭可拆支撑。支撑挤出路径为0，严格检查未发现危险层。
- 同一原始文件改名后重新执行默认流程，最终 STL 字节完全一致。
- other_shape_self_supported 是同一通用流程通过的另一种形状，可用于对照查看。

没有实体试打。修改尺寸、机器、材料、层高或删除承托后，原验证不再适用。
其他样本并非全部通过；超出加材预算或自动摆放翻倒的样本已拒绝发布，详见通用修复与复现说明。
''',encoding='utf-8')
with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(out.iterdir()):archive.write(path,path.name)
print(out,flush=True)
