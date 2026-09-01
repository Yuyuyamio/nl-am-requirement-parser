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
    p.add_argument('directory',type=Path);p.add_argument('--coupon',type=Path,required=True)
    p.add_argument('--cases',type=Path,required=True);p.add_argument('--regressions',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('outputs/printable_delivery/20260831_detachable_support_v4'))
    args = p.parse_args();root=Path(__file__).resolve().parents[1]
    tests,cases = read(args.regressions),read(args.cases)
    assert tests['passed'] and cases['completed']==cases['planned']
    print('RECHECK main',flush=True);main_data = recheck(args.directory)
    print('RECHECK coupon',flush=True);coupon_data = recheck(args.coupon)
    receipt = main_data['receipt']
    upper = upper_geometry_check(args.directory,receipt)
    repeat = next(row for row in cases['cases'] if row['name']=='renamed_repeat')
    assert repeat['status']=='pass' and repeat['geometry_sha256']==sha(receipt['geometry']) and repeat['source_unchanged']
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    files = {'mouse_30mm_detachable.stl':Path(receipt['geometry']),
        'mouse_30mm_detachable.3mf':Path(receipt['project']),
        'mouse_30mm_detachable.gcode.3mf':Path(receipt['artifact']),
        '实际模型与支撑.png':args.directory/'detachable_supports.png',
        '原始模型.glb':Path(main_data['preparation']['source']),
        '回归测试.json':args.regressions,'不同形状与重复验证.json':args.cases,
        '通用流程说明.md':root/'docs/DETACHABLE_SUPPORT.md',
        '支撑线宽测量.json':args.directory/'support_path_metrics.json'}
    for suffix,key in [('stl','geometry'),('3mf','project'),('gcode.3mf','artifact')]:
        files['removal_coupon.'+suffix]=Path(coupon_data['receipt'][key])
    hashes={}
    for name,path in files.items():
        (out/name).write_bytes(path.read_bytes());hashes[name]=sha(out/name)
    for name,data in [('小鼠验收.json',main_data),('小样验收.json',coupon_data),('耳部几何保持检查.json',upper)]:
        save(out/name,data);hashes[name]=sha(out/name)
    profiles_dir=out/'复现配置';profiles_dir.mkdir()
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
        'src/am_print_automation/workflow.py','src/am_model_generator/providers/request_builder.py']
    summary={'status':'pass','scope':'delivered_mouse_and_coupon_software_acceptance',
        'all_benchmark_cases_passed':cases['passed'],'hashes':hashes,
        'physical_print_performed':False,'physical_removal_verified':False,'printer_contacted':False,
        'renamed_repeat_identical_stl':True,'final_geometry_sha256':sha(receipt['geometry']),
        'code_sha256':{path:sha(root/path) for path in paths},'unit_test_count':tests['tests']}
    save(out/'acceptance.json',summary)
    added=sum(c.get('fidelity',{}).get('volume',{}).get('added_mm3',0)
        for r in main_data['preparation']['rounds'] for c in r['candidates'])
    text=f'''# V4：可拆支撑版（替代 V3 永久承托版）

先打开 **mouse_30mm_detachable.gcode.3mf** 查看已经验收的切片预览；编辑、调用 Auto Orient 和重新切片使用 **mouse_30mm_detachable.3mf**。STL 只有成品几何，不携带临时支撑。不要关闭支撑后直接打印 STL。

- 维持屁股向下的坐姿和平底；真实 Auto Orient 后重新切片，最终危险层为 0。
- 耳下和下巴改为切片器生成的独立临时支撑。最终实际支撑线宽 0.42 mm；上下最小接触间隙 0.24 mm；侧向配置间隙 0.35 mm。支撑基体线间距 3 mm，上下各两层界面，界面线间距 0.4 mm。
- 底座与底部浅缝补形属于成品，保留不拆。两轮局部修复合计增加约 {added:.3f} mm³，仅在离底面 1.88 mm 以内；耳朵几何保持不变，没有永久耳下立柱。
- {tests['tests']} 项回归通过；同一原始文件换名、换目录重跑，最终 STL 字节一致。规则已接入通用入口及上游生成提示，不依赖“小鼠”名称或特定坐标。

## 先用小样验证拆卸

**removal_coupon.gcode.3mf** 是通过同一配置切片验收的双薄片小样。它有类似耳下的薄片与支撑接触面，便于先验证耗材和机器条件下的拆卸效果。冷却后固定较厚的主体，从暴露的支撑外缘分段去除，避免拉扯薄片或把耳朵当把手。若必须用很大力气，不要强拔；先调整支撑/材料设置并重新验证。

本次没有进行实体试打，不能承诺支撑徒手一定能拔掉、表面没有痕迹或现场打印必定成功。软件检查包括支撑连续性、名义挤出包络间隙、孔内支撑筛查；不模拟实际粘结强度、剥离力和完整工具路径。

## 适用范围和已知限制

验证配置：Bambu Lab X1 Carbon，0.4 mm 喷嘴，PLA，0.12 mm 层高。成品实心填充不等于临时支撑实心。更换打印机、材料、尺寸或切片参数后，必须重新切片验收。没有上传或启动打印机。

不同形状不是全部通过：方块、小样及小鼠通过；真正独立的悬空实体被拒绝；长悬臂虽通过支撑切片，但 Auto Orient 翻转 90°，姿态保护拒绝交付。完整失败记录保留在“不同形状与重复验证.json”，没有放宽检查或宣称能自动修好任意模型。

## 复现

项目中的 `scripts/prepare_verified_print.py` 默认是可拆模式。使用本包“原始模型.glb”，指定 `--height-mm 30 --support-mode detachable`，并用“复现配置”里的 machine/process/filament 文件固定配置，输出到新路径。Bambu Studio 交互窗口须关闭。源文件与旧 V3 交付均未覆盖。
'''
    (out/'先读我_打印与拆卸.md').write_text(text,encoding='utf-8')
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(out))
    print(out,flush=True);print(out.with_suffix('.zip'),flush=True)


if __name__=='__main__':main()
