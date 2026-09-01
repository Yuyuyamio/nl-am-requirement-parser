"""Publish only independently rechecked detachable artifacts and complete evidence."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile
import numpy as np
import trimesh
from scipy.spatial import cKDTree

from am_print_executor.detachable_support import inspect_detachable_support
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.semantic_pose_gate import inspect_upright_source_preserved
from am_print_executor.slice_geometry_frame import verify_source_matches_slice


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')


def recheck(directory):
    result = read(directory/'result.json'); receipt = result['acceptance']
    assert result['status']=='slice_complete' and receipt['status']=='pass'
    assert result['support_mode']==receipt['support_mode']=='detachable'
    geometry,artifact,project = map(Path,(receipt['geometry'],receipt['artifact'],receipt['project']))
    assert sha(geometry)==receipt['geometry_sha256'] and sha(artifact)==receipt['artifact_sha256']
    mesh = trimesh.load(geometry,force='mesh',process=True)
    verify_source_matches_slice(mesh,artifact)
    gate = inspect_final_gcode_printability(artifact,geometry_path=geometry,credit_removable_support=True)
    removal = inspect_detachable_support(artifact,geometry_path=geometry)
    flat = inspect_flat_printing_base(mesh)
    pose = inspect_upright_source_preserved(Path(receipt['source']),project)
    assert gate['status']==removal['status']==pose['status']=='pass' and flat['base_flatness_passed']
    preparation = read(directory/'preparation.json')
    assert sha(preparation['source'])==preparation['source_sha256']
    return dict(receipt=receipt,gate=gate,removal=removal,flat_base=flat,pose=pose,preparation=preparation)


def upper_geometry_check(directory,receipt):
    before = trimesh.load(directory/'prepared.stl',force='mesh',process=True)
    after = trimesh.load(receipt['geometry'],force='mesh',process=True)
    transform = np.eye(4)
    for path in sorted(directory.glob('orientation_*/auto_orient.json')):
        transform = transform @ np.asarray(read(path)['output']['upright_pose']['source_to_world_row_vector'])
    after.apply_transform(np.linalg.inv(transform).T)
    protected_z = before.bounds[0,2]+min(2.4,before.extents[2]*.08)+.001
    left = before.vertices[before.vertices[:,2]>protected_z]
    right = after.vertices[after.vertices[:,2]>protected_z]
    error = max(cKDTree(left).query(right)[0].max(),cKDTree(right).query(left)[0].max())
    assert error < .001
    return dict(status='pass',above_z_mm=float(protected_z),maximum_bidirectional_vertex_distance_mm=float(error),
        tolerance_mm=.001,description='Upper geometry including ears preserved after undoing actual placement transforms.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path);p.add_argument('--coupon',type=Path)
    p.add_argument('--include-calibration-sample',action='store_true',help='Opt-in only: export sample into a clearly marked separate folder')
    p.add_argument('--cases',type=Path,required=True);p.add_argument('--regressions',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('outputs/printable_delivery/20260831_mouse_main_only'))
    args = p.parse_args();root=Path(__file__).resolve().parents[1]
    if args.include_calibration_sample and args.coupon is None:
        p.error('--include-calibration-sample requires --coupon')
    tests,cases = read(args.regressions),read(args.cases)
    assert tests['passed'] and cases['completed']==cases['planned']
    print('RECHECK main',flush=True);main_data = recheck(args.directory)
    coupon_data = None
    if args.include_calibration_sample:
        print('RECHECK coupon',flush=True);coupon_data = recheck(args.coupon)
    receipt = main_data['receipt']
    upper = upper_geometry_check(args.directory,receipt)
    repeat = next(row for row in cases['cases'] if row['name']=='renamed_repeat')
    assert repeat['status']=='pass' and repeat['geometry_sha256']==sha(receipt['geometry']) and repeat['source_unchanged']
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    files = {'小鼠成品.stl':Path(receipt['geometry']),
        '01_小鼠模型_打开这个.3mf':Path(receipt['project']),
        '02_小鼠切片_查看支撑.gcode.3mf':Path(receipt['artifact']),
        '小鼠模型与实际支撑.png':args.directory/'primary_model_preview.png',
        '复现资料/原始模型.glb':Path(main_data['preparation']['source']),
        '验证记录/回归测试.json':args.regressions,'验证记录/不同形状与重复验证.json':args.cases,
        '复现资料/通用流程说明.md':root/'docs/DETACHABLE_SUPPORT.md',
        '验证记录/支撑线宽测量.json':args.directory/'support_path_metrics.json'}
    if coupon_data:
        for suffix,key in [('stl','geometry'),('3mf','project'),('gcode.3mf','artifact')]:
            files['测试小样_不是小鼠成品/removal_coupon.'+suffix]=Path(coupon_data['receipt'][key])
    hashes={}
    for name,path in files.items():
        (out/name).parent.mkdir(parents=True,exist_ok=True)
        (out/name).write_bytes(path.read_bytes());hashes[name]=sha(out/name)
    records=[('验证记录/小鼠验收.json',main_data),('验证记录/耳部几何保持检查.json',upper)]
    if coupon_data:records.append(('测试小样_不是小鼠成品/小样验收.json',coupon_data))
    for name,data in records:
        save(out/name,data);hashes[name]=sha(out/name)
    profiles_dir=out/'复现资料/复现配置';profiles_dir.mkdir(parents=True)
    (profiles_dir/'process.json').write_bytes((args.directory/'process.json').read_bytes())
    command=receipt['auto_orient']['command']
    machine,process=command[command.index('--load-settings')+1].split(';')
    filament=command[command.index('--load-filaments')+1].split(';')
    (profiles_dir/'machine.json').write_bytes(Path(machine).read_bytes())
    for i,path in enumerate(filament,1):(profiles_dir/f'filament_{i}.json').write_bytes(Path(path).read_bytes())
    for path in profiles_dir.iterdir():hashes[str(path.relative_to(out))]=sha(path)
    paths=['src/am_print_executor/detachable_support.py','src/am_print_executor/near_base_gap_fill.py',
        'src/am_print_executor/verified_print_preparation.py','src/am_print_executor/geometry_fidelity_gate.py',
        'src/am_print_executor/gcode_printability_gate.py','src/am_print_executor/gcode_support_continuity.py',
        'src/am_print_automation/workflow.py','src/am_model_generator/providers/request_builder.py',
        'scripts/package_detachable_support.py','scripts/render_detachable_acceptance.py']
    summary={'status':'pass','scope':'delivered_mouse_software_acceptance',
        'primary_project':'01_小鼠模型_打开这个.3mf','calibration_sample_included':bool(coupon_data),
        'all_benchmark_cases_passed':cases['passed'],'hashes':hashes,
        'physical_print_performed':False,'physical_removal_verified':False,'printer_contacted':False,
        'renamed_repeat_identical_stl':True,'final_geometry_sha256':sha(receipt['geometry']),
        'code_sha256':{path:sha(root/path) for path in paths},'unit_test_count':tests['tests']}
    save(out/'acceptance.json',summary)
    added=sum(c.get('fidelity',{}).get('volume',{}).get('added_mm3',0)
        for r in main_data['preparation']['rounds'] for c in r['candidates'])
    text=f'''# 完整小鼠模型：明确区分正式模型与测试件

**打开 `01_小鼠模型_打开这个.3mf`。** 这是完整的小鼠，有头、身体、四肢和尾巴，含打印配置，可以调用 Auto Orient 后重新切片。

**查看已验收的支撑路径，打开 `02_小鼠切片_查看支撑.gcode.3mf`。** 工程准备视图中的模型外形不包含切片器临时支撑，需切片或打开此预览文件才能看见它们。

{'另附测试件仅位于“测试小样_不是小鼠成品”子文件夹，不是正式模型。' if coupon_data else '此包不含测试小样。之前两片圆盘加立柱的测试件不再混入正式交付。'}

这次是纠正交付结构和文件标识，小鼠工程及切片与此前验收版本字节相同；没有把换文件名说成重新设计。预览图直接从实际交付的 3MF 读取。

STL 只有成品几何，不携带临时支撑。不要关闭支撑后直接打印 STL。

- 维持屁股向下的坐姿和平底；真实 Auto Orient 后重新切片，最终危险层为 0。
- 耳下和下巴改为切片器生成的独立临时支撑。最终实际支撑线宽 0.42 mm；上下最小接触间隙 0.24 mm；侧向配置间隙 0.35 mm。支撑基体线间距 3 mm，上下各两层界面，界面线间距 0.4 mm。
- 底座与底部浅缝补形属于成品，保留不拆。两轮局部修复合计增加约 {added:.3f} mm³，仅在离底面 1.88 mm 以内；耳朵几何保持不变，没有永久耳下立柱。
- {tests['tests']} 项回归通过；同一原始文件换名、换目录重跑，最终 STL 字节一致。规则已接入通用入口及上游生成提示，不依赖“小鼠”名称或特定坐标。

## 实体打印仍未验证

本次没有进行实体试打，不能承诺支撑徒手一定能拔掉、表面没有痕迹或现场打印必定成功。软件检查包括支撑连续性、名义挤出包络间隙、孔内支撑筛查；不模拟实际粘结强度、剥离力和完整工具路径。

## 适用范围和已知限制

验证配置：Bambu Lab X1 Carbon，0.4 mm 喷嘴，PLA，0.12 mm 层高。成品实心填充不等于临时支撑实心。更换打印机、材料、尺寸或切片参数后，必须重新切片验收。没有上传或启动打印机。

不同形状不是全部通过：方块、小样及小鼠通过；真正独立的悬空实体被拒绝；长悬臂虽通过支撑切片，但 Auto Orient 翻转 90°，姿态保护拒绝交付。完整失败记录保留在“验证记录/不同形状与重复验证.json”，没有放宽检查或宣称能自动修好任意模型。

## 复现

项目中的 `scripts/prepare_verified_print.py` 默认是可拆模式。使用本包“复现资料/原始模型.glb”，指定 `--height-mm 30 --support-mode detachable`，并用“复现资料/复现配置”里的 machine/process/filament 文件固定配置，输出到新路径。Bambu Studio 交互窗口须关闭。源文件与旧交付均未覆盖。
'''
    (out/'先读我_打印与拆卸.md').write_text(text,encoding='utf-8')
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(out))
    print(out,flush=True);print(out.with_suffix('.zip'),flush=True)


if __name__=='__main__':main()
