# 可拆支撑：当前默认流程

默认入口已从永久承托切换为 `support_mode="detachable"`，覆盖本地准备脚本和 `ProductionServices`。旧永久版仍可用 `--support-mode permanent` 显式选择，历史文件不覆盖。

上游生成提示要求只生成成品、不把临时支撑柱并入模型，并保留可接近的过悬底面。GLB +Y 向上、STL +Z 向上、坐姿/站姿保护、平底检查保持原规则。无法识别任意输入的解剖方向，仍以来源坐标契约为准。

成品 STL 不包含切片器的临时支撑。临时支撑由 Bambu 在切片时生成，参数保存在 3MF 项目中。仅导入 STL 而关闭支撑，不是本流程验收的打印方案。底座属于保留的成品几何，不能当临时支撑拔除。

对于当前 0.4 mm 喷嘴配置，起始参数是 0.12 mm 层高，上下接触距离 0.24 mm，侧向距离 0.35 mm，上下各两层支撑界面，界面线间距 0.4 mm，支撑主体线间距 3 mm。模型仍用保守的实心填充，临时支撑不是实心立柱。参数按喷嘴和层高计算，没有模型名称或耳朵坐标分支。这些是待实切片和材料校准的起点，不是已证实的最佳参数。

选择可拆模式后，失败不能回退成大块永久承托。仅允许既有保真预算内的小范围局部加材；无法满足就拒绝发布并交给上游重设计，最多两轮的预算不重置。源文件不改写。精密模型仍可禁用改形。

验收分开进行：

1. 原严格打印检查保留所有危险区域和桥接条件，计入真实可达的临时支撑；没有放宽面积阈值。
2. 新的分离设计检查读取最终 G-code 的实际线宽和路径层厚，以保守的挤出材料包络估算间隙，拒绝粘连/距离过小、缺少测量信息、过密配置和模型截面孔内支撑。
3. 通过前两项和平底检查后才调用真实 Auto Orient，之后重新切片并再次验收。
4. 最终凭据绑定支撑模式、模型和切片文件 SHA256。旧永久版凭据不能通过当前可拆模式验收。失败的中间结果不发布。

分离检查是保守筛查：截面孔即使可能从另一高度取出，也会要求重新设计或人工评估；小接触可能因材料包络估计而被拒绝。它不模拟实际挤出变形、剥离力、钳子进入空间或完整拆除运动，不能证明“徒手一定能拆”。`physical_removal_verified` 始终保持 false，直到有独立实体试验；代码不会上传或启动打印机。

[Prusa 官方支撑说明](https://help.prusa3d.com/article/support-material_1698)说明了接触距离、支撑图案和密度与拆除难度的关系。增加间隙也可能损失底面质量，因此不能只减细支撑而跳过打印检查。

## 复现与验证

先保存并关闭 Bambu Studio 的交互窗口。现有 `run_bambu_cli` 只允许串行、隐藏的本地调用；不能为了验证绕过此保护或强行关闭用户文件。

```powershell
.venv\Scripts\python.exe scripts/prepare_verified_print.py 输入模型.glb 新目录/结果.gcode.3mf --height-mm 30 --support-mode detachable --machine 机器配置.json --process 工艺配置.json --filament 材料配置.json
.venv\Scripts\python.exe scripts/run_printable_repair_regressions.py
.venv\Scripts\python.exe scripts/validate_detachable_support.py tests/fixtures/flat_base_regression_current_mouse/current_mouse_curved_base.glb --profiles outputs/model_self_support/trial_1/verified_preparation_nwd6wz1m/profiles
```

后一个命令依次检查原始小鼠、小型双薄片拆卸样本、方块、长悬臂、真正独立的悬空实体，以及改名换目录后的同源输入。报告同时保留预期失败、未完成项和重复结果对比；中途中断不能写成全部通过。

## 本轮实际状态

软件回归共 145 项通过，报告为 `outputs/printable_foundation_validation/regressions_20260831_141721.json`。小鼠候选和小样的实体连通/封闭及平底预检通过，源 GLB 哈希保持不变。新规则也已读取旧 V2 的真实切片，保守检测到六处极小接触包络交叠，拒绝把旧版作为新版验收证据；这不是实体粘连试验结果。

真实新版切片尝试记录：`outputs/detachable_support/validation_20260831_141332/report.json`。Bambu Studio 进程 15616 正由用户打开 V3 项目，工具拒绝启动，`passed: false`、`output_published: false`。准备完成的正确向上平底模型和 `removal_coupon_UNVERIFIED.stl` 仅供审查，均未获得本轮最终打印验收。下一步需要关闭该应用后运行上述验证，解决实际检查暴露的问题，再交付正式 3MF 和切片文件；实体拆卸仍需小样试打。
