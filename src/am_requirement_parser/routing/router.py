from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator

from ..providers.base import StructuredOutputProvider
from .models import TaskRoute
from .schema import TASK_ROUTE_SCHEMA


ROUTER_INSTRUCTIONS = """
你是自然语言驱动增材制造系统的任务路由器。

你只负责判断用户的3D制造请求应进入哪条生成路线。
不要生成CAD代码，不要编造工程参数，也不要直接设计模型。

任务类型：

1. creative_asset

适用于视觉外形和创意表达为主的对象，例如：
动物、人物、卡通角色、手办、雕塑、摆件、玩具、
装饰品、花瓶以及其他自由曲面模型。

示例：
- 打印一只坐着的卡通小狗
- 生成一个龙形摆件
- 做一个可爱的宇航员手办

2. engineering_part

适用于功能、尺寸、装配、载荷、材料、强度、公差
或接口要求为主的工程零件。

示例：
- 设计一个承受800 N载荷的无人机支架
- 做一个孔距50 mm的连接板
- 生成一个需要和轴承配合的外壳

3. unknown

信息不足，无法可靠判断路线时使用。

判断原则：

- 小狗、人物、雕塑等不能因为用户提到尺寸就变成工程零件。
- 工程零件不能因为用户要求外观美观就变成创意模型。
- 用户要求打印时，target_output应为3d_print。
- 用户只要求生成模型或文件时，target_output应为3d_model。
- 信息不足时，needs_clarification必须为true。
- 信息不足时，只提出一个最关键的澄清问题。
- 不得把缺失参数当成用户已经提供的信息。

必须返回全部九个字段：

- schema_version
- task_type
- intent_summary
- object_name
- target_output
- needs_clarification
- clarification_question
- confidence
- routing_reason

即使某个字段没有内容，也必须按照Schema返回null，
不能省略任何必填字段。
""".strip()


def _format_error_path(
    error: Any,
) -> str:
    if not error.absolute_path:
        return "$"

    path = "$"

    for part in error.absolute_path:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"

    return path


def _collect_route_errors(
    data: Any,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: 任务路由结果必须是JSON对象"
        ]

    validator = Draft202012Validator(
        TASK_ROUTE_SCHEMA
    )

    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    return [
        (
            f"{_format_error_path(error)}: "
            f"{error.message}"
        )
        for error in errors
    ]


def _build_repair_input(
    *,
    original_input: str,
    previous_data: dict[str, Any],
    errors: list[str],
) -> str:
    previous_json = json.dumps(
        previous_data,
        ensure_ascii=False,
        indent=2,
    )

    error_text = "\n".join(
        f"- {error}"
        for error in errors
    )

    return (
        "请修正上一轮不符合Schema的路由结果。\n\n"
        f"原始用户请求：\n{original_input}\n\n"
        f"上一轮结果：\n{previous_json}\n\n"
        f"Schema错误：\n{error_text}\n\n"
        "请重新分析原始请求，并返回完整JSON对象。"
        "必须包含Schema要求的全部字段。"
        "不要解释，不要使用Markdown代码块。"
    )


def route_request(
    text: str,
    provider: StructuredOutputProvider,
) -> TaskRoute:
    cleaned = text.strip()

    if not cleaned:
        raise ValueError(
            "用户输入不能为空"
        )

    current_input = cleaned
    last_errors: list[str] = []
    last_data: dict[str, Any] = {}

    # 第一次正常生成；若Schema不合格，再自动纠错一次。
    for attempt in range(2):
        data = provider.generate_structured(
            instructions=ROUTER_INSTRUCTIONS,
            user_input=current_input,
            schema_name="am_task_route",
            schema=TASK_ROUTE_SCHEMA,
        )

        last_data = data
        last_errors = _collect_route_errors(
            data
        )

        if not last_errors:
            return TaskRoute.from_dict(data)

        if attempt == 0:
            current_input = _build_repair_input(
                original_input=cleaned,
                previous_data=data,
                errors=last_errors,
            )

    raise ValueError(
        "任务路由结果经过一次自动纠错后"
        "仍不符合Schema：\n"
        + "\n".join(last_errors)
        + "\n\n最后返回的数据：\n"
        + json.dumps(
            last_data,
            ensure_ascii=False,
            indent=2,
        )
    )