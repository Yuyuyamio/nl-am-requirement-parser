# M1 需求输入与语义解析模块

## 1. 模块目标

M1 负责将用户的自然语言增材制造需求转换为经过验证的结构化任务规格。

M1 不负责：

- 生成三维几何模型；
- 下载 GLB、OBJ 或 STL；
- 网格修复；
- 自动切片；
- 生成 G-code；
- 控制 3D 打印机。

这些能力分别属于后续的 M2、M3 和 M4 模块。

## 2. 统一入口

在项目根目录执行：

```powershell
python -m am_requirement_parser.main_cli "用户需求"
```

也可以使用安装后的命令：

```powershell
am-m1 "用户需求"
```

## 3. 任务路线

M1 支持三种任务类型。

### 3.1 creative_asset

适用于：

- 动物；
- 人物；
- 卡通角色；
- 手办；
- 雕塑；
- 摆件；
- 装饰性自由曲面模型。

正式输出：

```text
creative_asset_spec.json
```

### 3.2 engineering_part

适用于：

- 支架；
- 外壳；
- 连接件；
- 承力零件；
- 具有尺寸、载荷、材料、接口或工艺约束的零件。

正式输出：

```text
requirement_spec.json
```

### 3.3 unknown

适用于信息不足、无法可靠判断任务类型的输入。

正式输出：

```text
unresolved_task.json
```

该类型不能进入 M2。

## 4. M1 输出文件

每次统一流程输出：

```text
task_route.json
正式任务规格 JSON
m1_manifest.json
```

### 4.1 task_route.json

保存任务分类结果，包括：

- `task_type`；
- `intent_summary`；
- `object_name`；
- `confidence`；
- `routing_reason`；
- `clarification_question`。

### 4.2 正式任务规格

创意任务：

```text
creative_asset_spec.json
```

工程任务：

```text
requirement_spec.json
```

未知任务：

```text
unresolved_task.json
```

### 4.3 m1_manifest.json

M1 向后续模块交付的流程清单，包括：

- 原始输入；
- 任务类型；
- 当前状态；
- 路由文件位置；
- 正式规格位置；
- 是否允许进入 M2。

## 5. 状态规则

| 状态 | 含义 | next_module |
|---|---|---|
| `ready` | 创意规格完整 | `M2` |
| `complete` | 工程规格完整 | `M2` |
| `incomplete` | 工程信息缺失 | `null` |
| `needs_clarification` | 需要用户补充信息 | `null` |
| `conflict` | 需求存在冲突 | `null` |

只有下面两种组合允许进入 M2：

```text
status = ready
next_module = M2
```

或：

```text
status = complete
next_module = M2
```

## 6. 可靠性机制

M1 采用多层验证：

```text
云端大模型语义理解
        ↓
结构化 JSON 输出
        ↓
本地字段规范化
        ↓
JSON Schema 校验
        ↓
单位归一化
        ↓
缺失信息检查
        ↓
Manifest 契约验证
```

固定字段、单位换算、枚举值和状态转换由本地程序控制。

大模型主要负责：

- 理解对象；
- 理解功能；
- 提取姿态和风格；
- 识别自然语言意图；
- 生成适合后续模型使用的描述。

## 7. 工程安全原则

工程任务必须遵循：

- 不得编造载荷；
- 不得编造材料牌号；
- 不得编造固定区域；
- 不得编造设计域；
- 不得把缺失参数当作已确认参数；
- 工程信息不足时禁止进入 M2。

## 8. M1 与 M2 的边界

M2 只能读取：

```text
m1_manifest.json
```

然后根据 Manifest 中的：

```text
output_file
```

读取正式规格。

M2 不得重新解释原始自然语言，也不得绕过 M1 状态检查。

当：

```text
next_module = null
```

时，M2 必须停止。

## 9. 当前已实现能力

- 中文自然语言输入；
- OpenRouter 云端模型接入；
- 创意与工程任务自动路由；
- 创意 3D 任务规格生成；
- 工程约束结构化解析；
- 载荷单位归一化；
- 几何尺寸单位归一化；
- 缺失信息识别；
- 澄清问题生成；
- 用户回答写回；
- JSON Schema 验证；
- 自动纠错；
- M1 统一入口；
- M1 Manifest 交付契约；
- 离线单元测试；
- 最终验收程序。

## 10. 当前限制

- 免费云端模型的输出质量和响应速度可能波动；
- 创意对象名称偶尔会使用本地兜底值；
- 当前工程解析器主要覆盖示例化的支架需求；
- 尚未接入语音转文字；
- 尚未建立制造知识图谱和 RAG；
- 尚未生成真实三维模型；
- 尚未接入切片器和打印机。

以上限制不影响 M1 MVP 的模块闭环，但属于后续迭代内容。
