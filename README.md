# NL-AM Requirement Parser

一个面向增材制造的自然语言驱动工作流原型。

用户输入一段中文需求后，系统会完成：

```text
自然语言需求
→ M1 语义解析与结构化
→ M2 本地三维模型生成
→ 网格检查与修复
→ 尺寸归一化
→ STL 导出与验证
```

## 当前进度

### M1：需求解析

* 识别任务类型
* 提取尺寸、外观和打印要求
* 生成结构化 JSON
* 判断是否需要用户补充信息

### M2：三维模型生成

* 使用 Stable Diffusion 生成参考图
* 使用本地 TripoSG 生成 GLB 模型
* 检查网格完整性
* 自动修复不封闭模型
* 按目标高度归一化
* 导出并回读验证 STL

M2 最终交付文件：

```text
m2_output.stl
```

## 环境要求

* Python 3.11+
* Windows 10/11
* WSL2
* NVIDIA GPU
* OpenRouter API Key
* 本地 Stable Diffusion 与 TripoSG 环境

安装 Python 依赖：

```powershell
python -m pip install -e .
```

设置源码路径：

```powershell
$env:PYTHONPATH = (Resolve-Path ".\src").Path
```

设置 OpenRouter Key：

```powershell
$env:OPENROUTER_API_KEY = "your-api-key"
```

## 使用示例

输入自然语言需求：

```powershell
python -m am_requirement_parser.main_cli `
    "给我打印一只坐着的卡通小狗，高度10厘米，底部平稳，适合3D打印" `
    --output-dir "outputs\demo\m1"
```

创建 M2 任务：

```powershell
python -m am_model_generator.cli `
    "outputs\demo\m1\m1_manifest.json" `
    --output-dir "outputs\demo\m2"
```

后续依次执行本地 TripoSG 生成、网格修复、归一化和 STL 导出工具。

最终模型位于任务目录：

```text
m2_output.stl
```

## 测试

```powershell
python -m unittest discover `
    -s tests `
    -p "test*.py" `
    -v
```

## 项目状态

* M1：已完成
* M2：已完成
* M3：待开发，负责切片与路径规划
* M4：待开发，负责打印执行与反馈优化
