# NL-AM Requirement Parser / 造物台

一个面向增材制造的自然语言到实物打印工作流。用户用中文描述想制作的物品，系统即可完成需求结构化、三维模型生成、网格处理、Bambu Studio 自动定向与树状支撑切片，并在明确授权后将任务发送到打印机。

```text
自然语言 / 本地语音
→ M1 需求理解与结构化
→ M2 三维模型生成、修复、归一化与 STL 导出
→ Bambu Studio Auto Orient、原生树状支撑与切片
→ 文件下载
→（可选）FTPS 上传与 MQTT 启动打印
```

## 当前能力

- 中文自然语言需求解析、单位归一化、缺失信息检查和结构化 JSON 输出
- OpenRouter / OpenAI 需求理解，以及本地 TripoSG、Meshy 或 Mock 模型提供器
- GLB/STL 网格检查、必要修复、目标尺寸归一化和交付回读验证
- Bambu Studio 后台 Auto Orient、`tree(auto)` / `tree_hybrid` 原生支撑和 G-code 3MF 切片
- 本地“造物台”网页：文字或本地语音输入、任务进度、暂停/继续/停止、失败重试、任务置顶与交付下载
- 可选的 X1C 连接验证、FTPS 上传、AMS 映射和一次性 MQTT 开打
- 可恢复任务状态与事件日志；不自动重放结果不明确的实体打印动作

> 默认只生成文件，不会启动实体打印。只有开启“自动打印”或在命令行显式传入 `--start-print` 才会进入打印机上传与启动阶段。

## 快速开始：本地网站

### 1. 环境要求

- Windows 10/11
- Python 3.11+（当前本机环境使用 Python 3.11）
- Bambu Studio；完整本地 M2 流程还需要 WSL2、NVIDIA GPU、Stable Diffusion 与 TripoSG 环境
- OpenRouter API Key，或自行切换到 OpenAI 提供器

首次安装：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

设置需求理解所需的密钥：

```powershell
$env:OPENROUTER_API_KEY = "your-api-key"
```

只有需要从网站或命令行启动实体打印时，才设置打印机 LAN 访问码：

```powershell
$env:BAMBU_LAN_ACCESS_CODE = "your-access-code"
```

不要将密钥写入需求文本、仓库文件、任务状态或截图。

### 2. 启动

在项目根目录运行：

```powershell
.\scripts\run_print_frontend.ps1
```

网站默认位于 <http://127.0.0.1:8765/>。启动窗口需要保持运行；关闭浏览器不会停止后台任务，关闭启动窗口会停止网站服务。

常用启动参数：

```powershell
# 不自动打开浏览器
.\scripts\run_print_frontend.ps1 -NoBrowser

# 使用其他端口
.\scripts\run_print_frontend.ps1 -Port 8766

# 使用其他任务目录
.\scripts\run_print_frontend.ps1 -OutputRoot "outputs/my_jobs"
```

### 3. 使用

1. 保持“自动打印”关闭，输入物品、尺寸、姿态和材料要求。
2. 等待模型生成，以及 Bambu Studio 在后台完成自动定向、树状支撑和切片。
3. 下载并检查 G-code 3MF、可编辑工程或本体 STL。
4. 如需网站开打，先验证打印机连接，再明确确认启动打印。

更完整的中文操作说明见 [造物台快速开始](docs/ZAOWUTAI_QUICK_START.md)。

## 命令行

完整流程默认只生成文件：

```powershell
$env:PYTHONPATH = (Resolve-Path ".\src").Path
.\.venv\Scripts\python.exe -m am_print_automation.cli `
  "打印一只10厘米高、坐着的卡通小狗"
```

显式允许实体打印：

```powershell
.\.venv\Scripts\python.exe -m am_print_automation.cli `
  "打印一只10厘米高、坐着的卡通小狗" `
  --start-print
```

只运行 M1 需求解析：

```powershell
.\.venv\Scripts\python.exe -m am_requirement_parser.main_cli `
  "给我打印一只坐着的卡通小狗，高度10厘米，底部平稳，适合3D打印" `
  --output-dir "outputs\demo\m1"
```

根据 M1 清单创建 M2 任务：

```powershell
.\.venv\Scripts\python.exe -m am_model_generator.cli `
  "outputs\demo\m1\m1_manifest.json" `
  --output-dir "outputs\demo\m2"
```

## 任务产物

正式任务保存在：

```text
outputs/automatic_jobs/<job-id>/
```

其中包含可恢复的 `workflow_state.json`、追加式 `workflow_events.jsonl`，以及模型、STL、Bambu 工程和 G-code 3MF 等阶段产物。网页下载会检查文件路径与哈希，防止跨任务或已被替换的文件被误交付。

## 安全与验证边界

- 网站只监听 `127.0.0.1`，不是公网服务。
- Bambu Studio 成功生成目标 G-code 3MF 后即视为切片完成；项目不再叠加自制的平底、悬空、支撑接触或拆卸性放行规则。
- “切片成功”不等于实物打印一定成功。材料、喷嘴、平台、首层、支撑拆除和设备状态仍需人工确认。
- 暂停与停止在安全步骤边界生效；打印指令一旦发送，网页不是实体急停。
- 结果不明确的打印启动阶段不会被自动重放，避免重复打印。

## 网站打不开时

先在项目根目录重新运行：

```powershell
.\scripts\run_print_frontend.ps1 -NoBrowser
```

然后手动访问 <http://127.0.0.1:8765/>。如果仍失败：

1. 检查 `8765` 是否已被其他程序占用，可临时改用 `-Port 8766`。
2. 运行 `.\.venv\Scripts\python.exe --version`。如果提示无法创建进程，通常是创建虚拟环境时使用的 Python 已被卸载或移动；重新安装同系列 Python，或按“首次安装”步骤重建 `.venv`。
3. 查看终端中的缺失依赖或 Bambu Studio 冲突提示。运行后台切片前，请先关闭已打开的 Bambu Studio 窗口。

## 测试

```powershell
$env:PYTHONPATH = (Resolve-Path ".\src").Path
.\.venv\Scripts\python.exe -m unittest discover `
  -s tests `
  -p "test*.py" `
  -v
```

## 文档

- [造物台快速开始](docs/ZAOWUTAI_QUICK_START.md)
- [自动打印工作流](docs/AUTOMATIC_PRINT_WORKFLOW.md)
- [Bambu Studio 原生自动树状支撑](docs/DETACHABLE_SUPPORT.md)
- [M1 模块规范](docs/M1_MODULE_SPEC.md)
- [打印性恢复记录](docs/PRINTABILITY_RECOVERY_20260831.md)

## 项目状态

- M1：需求解析与结构化已实现
- M2：模型生成、网格处理、归一化与 STL 交付已实现
- 切片：Bambu Studio 自动定向、原生树状支撑和 G-code 3MF 输出已接入
- M4：X1C 连接验证、上传、AMS 映射、打印启动与运行期审计能力已接入
- 当前重点：真实设备与材料组合下的持续验证、故障恢复和交付体验优化

## License

见 [LICENSE](LICENSE)。
