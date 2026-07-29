# Natural-Language AM Requirement Parser

自然语言驱动智能化增材制造项目的第一阶段仓库。

当前最小目标：

```text
中文自然语言需求
        ↓
结构化工程需求
        ↓
缺失信息检查
        ↓
requirement_spec.json
```

## 运行

Windows 下直接双击：

```text
run_demo.bat
```

或者在 PowerShell 中运行：

```powershell
$env:PYTHONPATH="src"
python -m am_requirement_parser.cli examples/input.txt
```

运行后会在项目根目录生成：

```text
requirement_spec.json
```

## 测试

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```
