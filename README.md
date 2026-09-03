# 造物台 · NL-AM Requirement Parser

一个自然语言驱动的智能增材制造系统原型。

用户只需要描述想要制作的物品，系统即可完成从需求理解、三维模型生成，到 Bambu Studio 自动切片和打印任务派发的完整流程。

## 功能

当前主要支持：

- 中文自然语言需求解析
- 三维模型自动生成
- 网格检查与自动修复
- 模型尺寸归一化
- STL 自动导出
- Bambu Studio Auto Orient
- Bambu Studio 原生树状支撑
- G-code 3MF 自动切片
- Textured PEI Plate 参数验证
- Bambu X1C 本地连接
- AMS 耗材自动映射
- Headless 打印任务派发
- 打印进度实时监控
- HMS / AI Warning 与真实打印错误显示
- 任务状态保存、恢复与失败处理

整体流程：

```text
自然语言
↓
需求解析
↓
3D 模型生成
↓
网格修复与尺寸归一化
↓
Bambu Studio Auto Orient
↓
Tree Support + Slice
↓
G-code 3MF
↓
X1C / AMS
↓
打印监控
```

## 部署

### 前置需求

基础运行环境：

- Windows 10 / 11
- Python 3.11+
- Bambu Studio
- Git

如需使用完整本地 3D 模型生成流程，还需要：

- WSL2
- NVIDIA GPU
- 对应的本地 3D Generation 环境

如需进行实体打印，还需要：

- Bambu Lab X1C
- 局域网连接
- 打印机 LAN Access Code

### 安装

克隆项目：

```powershell
git clone https://github.com/Yuyuyamio/nl-am-requirement-parser.git
cd nl-am-requirement-parser
```

创建 Python 环境：

```powershell
py -3.11 -m venv .venv

.\.venv\Scripts\python.exe -m pip install --upgrade pip

.\.venv\Scripts\python.exe -m pip install -e .
```

设置需求解析 API Key：

```powershell
$env:OPENROUTER_API_KEY = "your-api-key"
```

如果需要实体打印：

```powershell
$env:BAMBU_LAN_ACCESS_CODE = "your-access-code"
```

### 启动

运行本地造物台：

```powershell
.\scripts\run_print_frontend.ps1
```

默认访问：

```text
http://127.0.0.1:8765/
```

也可以直接使用命令行：

```powershell
am-auto-print "打印一只3厘米高的小仓鼠"
```

默认只生成模型和打印文件，不会直接启动实体打印。

如需允许打印：

```powershell
am-auto-print "打印一只3厘米高的小仓鼠" --start-print
```

## License

MIT License.
