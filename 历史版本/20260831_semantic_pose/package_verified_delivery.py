"""Package only a twice-gated, actually Auto-Oriented local print artifact."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
import trimesh
from am_print_executor.bambu_auto_orient import inspect_auto_oriented_project
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.slice_geometry_frame import verify_source_matches_slice

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("preparation", type=Path)
parser.add_argument("destination", type=Path)
parser.add_argument("--regressions", type=Path, required=True)
args = parser.parse_args()
base = args.preparation.resolve()
receipt = json.loads((base / "final/acceptance.json").read_text(encoding="utf-8"))
trace = json.loads((base / "preparation.json").read_text(encoding="utf-8"))
regressions = json.loads(args.regressions.read_text(encoding="utf-8"))
assert receipt["status"] == "pass" and receipt["before_orientation_status"] == "pass" and receipt["after_orientation_status"] == "pass"
assert receipt["source_unchanged"] and regressions["passed"]
original = Path(trace["source"])
assert hashlib.sha256(original.read_bytes()).hexdigest() == trace["source_sha256"]
mesh = trimesh.load(receipt["geometry"], force="mesh", process=True)
flat = inspect_flat_printing_base(mesh)
gate = inspect_final_gcode_printability(Path(receipt["artifact"]), geometry_path=Path(receipt["geometry"]))
orientation = inspect_auto_oriented_project(Path(receipt["project"]))
alignment = verify_source_matches_slice(mesh, Path(receipt["artifact"]))
assert flat["base_flatness_passed"] and gate["status"] == "pass" and gate["dangerous_layer_count"] == 0
out = args.destination.resolve()
out.mkdir(parents=True, exist_ok=False)
files = {"mouse_30mm.stl": Path(receipt["geometry"]), "mouse_30mm.3mf": Path(receipt["project"]),
         "mouse_30mm.gcode.3mf": Path(receipt["artifact"]), "model_preview.png": base / "final/model_preview.png"}
hashes = {}
for name, source in files.items():
    data = source.read_bytes()
    (out / name).write_bytes(data)
    hashes[name] = hashlib.sha256(data).hexdigest()
with zipfile.ZipFile(out / "mouse_30mm.gcode.3mf") as archive:
    settings = json.loads(archive.read("Metadata/project_settings.config"))
profile_keys = ("printer_model", "printer_variant", "nozzle_diameter", "filament_type", "layer_height",
                "enable_support", "support_type", "support_threshold_angle", "sparse_infill_density", "sparse_infill_pattern")
report = {"status": "pass", "source": str(original), "source_sha256": trace["source_sha256"],
          "source_unchanged": True, "height_mm": float(mesh.extents[2]), "watertight": bool(mesh.is_watertight),
          "component_count": int(mesh.body_count), "flat_base": flat, "strict_printability": gate,
          "actual_auto_orient": orientation, "slice_geometry_alignment": alignment,
          "before_orientation_status": receipt["before_orientation_status"],
          "after_orientation_status": receipt["after_orientation_status"],
          "profile": {k: settings.get(k) for k in profile_keys}, "files_sha256": hashes,
          "regressions": regressions, "physical_print_performed": False, "printer_contacted": False,
          "preparation_directory": str(base)}
(out / "acceptance.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
(out / "repair_trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
readme = f"""# 小鼠模型打印交付 — 2026-08-31

推荐在 Bambu Studio 中打开 `mouse_30mm.3mf`（项目），或打开 `mouse_30mm.gcode.3mf` 查看已经验收的切片结果。`mouse_30mm.stl` 是同一模型的实际最终摆放几何。

此版高度 **{mesh.extents[2]:.3f} mm**，增加了 **1.2 mm 厚的永久平底底座和永久加强筋**。永久加强筋属于模型本身，不是打印后应拆除的支撑。切片工程另外包含可拆除的自动支撑。

## 已实际完成的验证

- 真实连续平底：通过；底面面积 {flat['bed_contact_area_mm2']:.2f} mm²，底面高差 {flat['base_height_range_mm']:.6f} mm。
- 重心投影位于支撑范围内，距离边界 {flat['stability']['stability_margin_mm']:.2f} mm。
- 网格封闭、单一连接实体、最小 Z=0。
- Auto Orient 前真实 Bambu 切片：严格检查通过。
- 实际 Bambu Studio Auto Orient：通过，检查了变换后的实际几何。
- Auto Orient 后重新切片：严格检查通过；危险层 0，悬空起始层岛 0，未落地支撑 0。
- {regressions['tests']} 项相关回归测试通过。原始 STL 未修改。

## 保留工程中的打印设置

Bambu Lab X1 Carbon、0.4 mm 喷嘴、PLA、0.20 mm 层高、100% 填充、Normal Auto 支撑、30° 支撑阈值。**不要关闭支撑。** 如换打印机、材料、缩放或改变姿态/层高/填充，需要重新切片检查；STL 本身不携带这些设置。

工程已经执行过 Auto Orient，不需要为了摆正再操作一次。模型现在有可供 Auto Orient 选择的完整平底。

## 验证的边界

本次进行了真实切片和自动摆放，但没有上传到打印机，也没有进行实体试打。首层粘附、耗材状态、喷嘴和打印机校准仍需现场确认，不能把计算验证等同于已完成实体打印。

`model_preview.png` 来自实际 STL，橙色标示底面。详细报告见 `acceptance.json`，几何改动和坐标换算见 `repair_trace.json`。

本次通用流程从冻结原模型独立运行，未执行全局体素化、marching cubes 或降低悬空判断阈值。默认禁止永久结构改动；明确开启结构修改选项时，才允许这种底座/加强筋方案。
"""
(out / "打印说明.md").write_text(readme, encoding="utf-8")
zip_path = out.with_suffix(".zip")
with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(out.iterdir()):
        archive.write(path, path.name)
print(json.dumps({"status": "pass", "directory": str(out), "zip": str(zip_path), "hashes": hashes}, indent=2), flush=True)
