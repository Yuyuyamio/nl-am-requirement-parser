# Bambu Studio 原生自动树状支撑

生产入口只使用 Bambu Studio 自带的自动树状支撑：

- `enable_support = 1`
- `support_type = tree(auto)`
- `support_style = tree_hybrid`
- 支撑范围不限于热床，检测悬浮外壳、悬垂墙和桥接区域

程序不再生成局部填充、斜坡、筋、永久承托或融合支柱，也不会因切片未通过而重新生成模型。Bambu Studio 负责自动定向、摆放和支撑生成；程序只做尺寸换算、后台调用以及最终安全检查。

所有 Bambu Studio 调用都经过统一后台执行器：Windows 使用隐藏启动参数和 `CREATE_NO_WINDOW`，不通过 shell，不主动打开交互界面。若用户已经打开 Bambu Studio，后台任务会安全停止并提示关闭现有窗口，避免共享配置冲突。

最终验收会确认：

1. 切片配置确实是 `tree(auto) / tree_hybrid`；
2. Bambu 已生成可读取的模型刀路，并把正数支撑间隙写入成品；
3. 摆放后的模型具有稳定热床接触；
4. 工程、G-code 3MF 和最终摆放 STL 的哈希与验收凭据一致。

程序不再用自建的逐层网格、材料包络或局部支撑算法重新裁决 Bambu 的支撑位置。

软件检查不能替代实体试打和拆支撑测试，`physical_removal_verified` 在没有独立实测前始终为 `false`。

本地只生成文件：

```powershell
.venv\Scripts\python.exe scripts/prepare_verified_print.py 输入模型.glb 新目录/结果.gcode.3mf --height-mm 30 --machine 机器配置.json --process 工艺配置.json --filament 材料配置.json
```

输出路径必须不存在。该命令不会上传文件或启动打印机。
