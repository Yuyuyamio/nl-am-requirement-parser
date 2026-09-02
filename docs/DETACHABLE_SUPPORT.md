# Bambu Studio 原生自动树状支撑

生产入口只使用 Bambu Studio 自带的自动树状支撑：

- `enable_support = 1`
- `support_type = tree(auto)`
- `support_style = tree_hybrid`

除此之外沿用当前 Bambu 工艺配置，不覆写悬垂阈值、支撑范围、接触间隙、界面层、底座图案、层高或填充设置。

程序不再生成局部填充、斜坡、筋、永久承托或融合支柱，也不会在 Bambu 切片成功后重新检查或重新生成模型。Bambu Studio 独自负责自动定向、摆放、支撑生成和切片决策。

所有 Bambu Studio 调用都经过统一后台执行器：Windows 使用隐藏启动参数和 `CREATE_NO_WINDOW`，不通过 shell，不主动打开交互界面。若用户已经打开 Bambu Studio，后台任务会安全停止并提示关闭现有窗口，避免共享配置冲突。

Bambu Studio 只要成功返回并生成目标 `.gcode.3mf`，生产流程就立即放行。程序不再解析刀路来裁决树状支撑，不检查支撑接触、逐层可达性、平底、悬空面积或拆卸间隙。下载时的路径与哈希检查只用于防止文件丢失或被替换，不参与可打印性判断。

本地只生成文件：

```powershell
.venv\Scripts\python.exe scripts/prepare_verified_print.py 输入模型.glb 新目录/结果.gcode.3mf --height-mm 30 --machine 机器配置.json --process 工艺配置.json --filament 材料配置.json
```

输出路径必须不存在。该命令本身不会上传文件或启动打印机；网站开启“自动打印”时会在切片成功后直接上传并开打。
