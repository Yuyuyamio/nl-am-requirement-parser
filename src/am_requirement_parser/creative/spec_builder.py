from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from ..providers.base import (
    StructuredOutputProvider,
)
from .models import CreativeAssetSpec
from .schema import CREATIVE_ASSET_SCHEMA


CREATIVE_SPEC_INSTRUCTIONS = """
你是自然语言驱动增材制造系统中的创意3D任务规划器。

你接收的输入已经被判断为creative_asset。
你的任务不是直接生成模型，而是输出一个可以交给文生3D或
图生3D服务使用的完整任务规格。

你必须：

1. 准确保留用户指定的对象、风格、姿态和尺寸。
2. 将厘米、米等尺寸统一换算成毫米。
3. generation_prompt_en必须使用英文，并适合3D资产生成。
4. negative_prompt_en必须使用英文，描述需要避免的问题。
5. 不得擅自添加用户没有要求的品牌、角色身份或复杂装饰。
6. 不得把系统为了可打印性增加的要求伪装成用户原始需求。
7. system_printability_guidance应包含适合3D打印的通用指导：
   - watertight closed mesh
   - single connected main body
   - flat and stable contact surface
   - no floating geometry
   - avoid extremely thin fragile parts
8. 用户明确说“打印”时，output_target为3d_print。
9. 用户只说生成模型或文件时，output_target为3d_model。
10. 如果目标是3d_print但用户完全没有给出尺寸，
    needs_clarification必须为true，并询问期望成品高度。
11. 如果用户已经给出高度，例如10厘米，
    target_height_mm必须换算为100。
12. 只能提出一个最关键的澄清问题。
13. 所有Schema必填字段都必须返回，不能省略。
14. schema_version必须是0.1.0。
15. task_type必须是creative_asset。
16. category只能描述对象类别，不能填写3d_print或3d_model。
17. needs_clarification为false时，
    clarification_question必须是null，不能是空字符串。
""".strip()


DEFAULT_PRINTABILITY_GUIDANCE = [
    "watertight closed mesh",
    "single connected main body",
    "flat and stable contact surface",
    "no floating geometry",
    "avoid extremely thin fragile parts",
]

CREATIVE_ASSET_FIELDS = {
    "schema_version",
    "task_type",
    "intent_summary",
    "object_name",
    "category",
    "visual_description",
    "style",
    "pose",
    "target_height_mm",
    "target_dimensions_text",
    "output_target",
    "generation_prompt_en",
    "negative_prompt_en",
    "system_printability_guidance",
    "needs_clarification",
    "clarification_question",
    "confidence",
}

VALID_CATEGORIES = {
    "animal",
    "character",
    "sculpture",
    "toy",
    "decoration",
    "container",
    "household_object",
    "other",
}


CATEGORY_ALIASES = {
    "animals": "animal",
    "pet": "animal",
    "creature": "animal",
    "figure": "character",
    "figurine": "toy",
    "statue": "sculpture",
    "ornament": "decoration",
    "decor": "decoration",
    "vase": "container",
    "household": "household_object",
}


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


def _clean_optional_string(
    value: Any,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    cleaned = value.strip()

    if not cleaned:
        return None

    if cleaned.lower() in {
        "null",
        "none",
        "n/a",
        "not applicable",
        "no clarification",
        "无需",
        "无",
        "不需要",
    }:
        return None

    return cleaned


def _as_boolean(
    value: Any,
    *,
    default: bool,
) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        cleaned = value.strip().lower()

        if cleaned in {
            "true",
            "yes",
            "1",
            "是",
            "需要",
        }:
            return True

        if cleaned in {
            "false",
            "no",
            "0",
            "否",
            "不需要",
        }:
            return False

    return default


def _as_confidence(
    value: Any,
) -> float:
    if isinstance(value, bool):
        return 0.5

    try:
        confidence = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return 0.5

    return max(
        0.0,
        min(1.0, confidence),
    )


def _parse_height_mm(
    text: str,
) -> float | None:
    number_pattern = (
        r"(\d+(?:\.\d+)?)"
    )

    unit_pattern = (
        r"(毫米|mm|厘米|cm|米|m)"
    )

    patterns = [
        (
            rf"(?:高度|高)\s*[:：]?\s*"
            rf"{number_pattern}\s*{unit_pattern}"
        ),
        (
            rf"{number_pattern}\s*{unit_pattern}"
            rf"\s*(?:高|高度)"
        ),
        (
            rf"(?:height)\s*[:：]?\s*"
            rf"{number_pattern}\s*{unit_pattern}"
        ),
    ]

    match = None

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            break

    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2).lower()

    if unit in {
        "毫米",
        "mm",
    }:
        factor = 1.0

    elif unit in {
        "厘米",
        "cm",
    }:
        factor = 10.0

    elif unit in {
        "米",
        "m",
    }:
        factor = 1000.0

    else:
        return None

    height_mm = value * factor

    if height_mm <= 0:
        return None

    return height_mm


def _infer_output_target(
    original_input: str,
    model_value: Any,
) -> str:
    lowered = original_input.lower()

    print_terms = (
        "打印",
        "印出来",
        "3d打印",
        "3d print",
        "print it",
        "printable",
    )

    if any(
        term in lowered
        for term in print_terms
    ):
        return "3d_print"

    if model_value in {
        "3d_print",
        "3d_model",
    }:
        return str(model_value)

    return "3d_model"


def _infer_category(
    *,
    original_input: str,
    data: dict[str, Any],
) -> str:
    raw_category = data.get("category")

    if isinstance(raw_category, str):
        cleaned_category = (
            raw_category.strip().lower()
        )

        if cleaned_category in VALID_CATEGORIES:
            return cleaned_category

        alias = CATEGORY_ALIASES.get(
            cleaned_category
        )

        if alias is not None:
            return alias

    combined = " ".join(
        str(value)
        for value in [
            original_input,
            data.get("object_name", ""),
            data.get(
                "visual_description",
                "",
            ),
            data.get("style", ""),
        ]
        if value is not None
    ).lower()

    animal_terms = (
        "狗",
        "小狗",
        "猫",
        "小猫",
        "兔",
        "熊",
        "鸟",
        "鱼",
        "马",
        "狐狸",
        "狼",
        "动物",
        "dog",
        "puppy",
        "cat",
        "kitten",
        "rabbit",
        "bear",
        "bird",
        "fish",
        "horse",
        "fox",
        "wolf",
        "animal",
    )

    sculpture_terms = (
        "雕塑",
        "雕像",
        "半身像",
        "塑像",
        "sculpture",
        "statue",
        "bust",
    )

    toy_terms = (
        "手办",
        "公仔",
        "玩具",
        "模型玩具",
        "figurine",
        "toy",
    )

    character_terms = (
        "人物",
        "角色",
        "宇航员",
        "机器人",
        "人偶",
        "character",
        "person",
        "astronaut",
        "robot",
    )

    decoration_terms = (
        "摆件",
        "装饰",
        "饰品",
        "挂件",
        "装饰品",
        "decoration",
        "ornament",
        "decor",
    )

    container_terms = (
        "花瓶",
        "瓶子",
        "杯子",
        "盒子",
        "容器",
        "vase",
        "bottle",
        "cup",
        "box",
        "container",
    )

    household_terms = (
        "灯罩",
        "收纳",
        "家居",
        "桌面用品",
        "lampshade",
        "household",
        "organizer",
    )

    category_groups = [
        ("animal", animal_terms),
        ("sculpture", sculpture_terms),
        ("toy", toy_terms),
        ("character", character_terms),
        (
            "decoration",
            decoration_terms,
        ),
        ("container", container_terms),
        (
            "household_object",
            household_terms,
        ),
    ]

    for category, terms in category_groups:
        if any(
            term in combined
            for term in terms
        ):
            return category

    return "other"


def _normalize_guidance(
    value: Any,
) -> list[str]:
    guidance: list[str] = []

    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str):
                continue

            cleaned = item.strip()

            if (
                cleaned
                and cleaned not in guidance
            ):
                guidance.append(cleaned)

    for default_item in (
        DEFAULT_PRINTABILITY_GUIDANCE
    ):
        if default_item not in guidance:
            guidance.append(default_item)

    return guidance


def _collect_raw_semantic_errors(
    data: Any,
) -> list[str]:
    """
    检查模型是否真正返回了核心语义。

    固定字段可以由本地规范化，
    但核心对象描述不能全部由兜底值伪造。
    """

    if not isinstance(data, dict):
        return [
            "$: 模型输出必须是JSON对象"
        ]

    required_semantic_fields = (
        "object_name",
        "visual_description",
        "generation_prompt_en",
        "negative_prompt_en",
    )

    errors: list[str] = []

    for field_name in (
        required_semantic_fields
    ):
        value = data.get(field_name)

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            errors.append(
                f"$.{field_name}: "
                "模型必须返回非空字符串"
            )

    return errors

def _canonicalize_data(
    data: Any,
    *,
    original_input: str,
) -> dict[str, Any]:
    """
    将模型输出转换为本项目的规范格式。

    固定字段、单位转换和空值规则由本地代码控制，
    不再依赖模型每次都严格遵守。
    """

    if isinstance(data, dict):
        normalized = dict(data)
    else:
        normalized = {}
        # 兼容模型常见的字段别名。
        # 只有正式字段为空时，才使用别名内容。
        if _clean_optional_string(
            normalized.get("style")
        ) is None:
            normalized["style"] = (
                normalized.get(
                    "style_description"
             )
            )

        if _clean_optional_string(
            normalized.get("pose")
        ) is None:
            normalized["pose"] = (
                normalized.get(
                    "pose_description"
                )
            )

    normalized["schema_version"] = (
        "0.1.0"
    )

    normalized["task_type"] = (
        "creative_asset"
    )

    normalized["output_target"] = (
        _infer_output_target(
            original_input,
            normalized.get(
                "output_target"
            ),
        )
    )

    normalized["category"] = (
        _infer_category(
            original_input=original_input,
            data=normalized,
        )
    )

    parsed_height = _parse_height_mm(
        original_input
    )

    if parsed_height is not None:
        normalized[
            "target_height_mm"
        ] = parsed_height

    else:
        model_height = normalized.get(
            "target_height_mm"
        )

        if isinstance(model_height, bool):
            normalized[
                "target_height_mm"
            ] = None

        elif model_height is None:
            normalized[
                "target_height_mm"
            ] = None

        else:
            try:
                numeric_height = float(
                    model_height
                )
            except (
                TypeError,
                ValueError,
            ):
                numeric_height = 0.0

            normalized[
                "target_height_mm"
            ] = (
                numeric_height
                if numeric_height > 0
                else None
            )

    normalized["style"] = (
        _clean_optional_string(
            normalized.get("style")
        )
    )

    normalized["pose"] = (
        _clean_optional_string(
            normalized.get("pose")
        )
    )

    normalized[
        "target_dimensions_text"
    ] = _clean_optional_string(
        normalized.get(
            "target_dimensions_text"
        )
    )

    if (
        parsed_height is not None
        and normalized[
            "target_dimensions_text"
        ] is None
    ):
        normalized[
            "target_dimensions_text"
        ] = (
            f"高度{parsed_height:g}毫米"
        )

    output_target = normalized[
        "output_target"
    ]

    target_height = normalized.get(
        "target_height_mm"
    )

    model_needs_clarification = (
        _as_boolean(
            normalized.get(
                "needs_clarification"
            ),
            default=False,
        )
    )

    missing_print_size = (
        output_target == "3d_print"
        and target_height is None
    )

    needs_clarification = (
        model_needs_clarification
        or missing_print_size
    )

    normalized[
        "needs_clarification"
    ] = needs_clarification

    question = _clean_optional_string(
        normalized.get(
            "clarification_question"
        )
    )

    if not needs_clarification:
        question = None

    elif question is None:
        if missing_print_size:
            question = (
                "希望成品高度是多少毫米？"
            )
        else:
            question = (
                "请补充最关键的外形要求。"
            )

    normalized[
        "clarification_question"
    ] = question

    normalized["confidence"] = (
        _as_confidence(
            normalized.get("confidence")
        )
    )

    intent_summary = (
        _clean_optional_string(
            normalized.get(
                "intent_summary"
            )
        )
    )

    normalized["intent_summary"] = (
        intent_summary
        or original_input
    )

    object_name = (
        _clean_optional_string(
            normalized.get(
                "object_name"
            )
        )
    )

    normalized["object_name"] = (
        object_name
        or "creative 3D object"
    )

    visual_description = (
        _clean_optional_string(
            normalized.get(
                "visual_description"
            )
        )
    )

    normalized[
        "visual_description"
    ] = (
        visual_description
        or original_input
    )

    generation_prompt = (
        _clean_optional_string(
            normalized.get(
                "generation_prompt_en"
            )
        )
    )

    if generation_prompt is None:
        generation_prompt = (
            "Create a printable 3D model "
            f"of {normalized['object_name']}. "
            "Use a single connected main body "
            "with clean, smooth geometry."
        )

    normalized[
        "generation_prompt_en"
    ] = generation_prompt

    negative_prompt = (
        _clean_optional_string(
            normalized.get(
                "negative_prompt_en"
            )
        )
    )

    if negative_prompt is None:
        negative_prompt = (
            "floating geometry, disconnected parts, "
            "open mesh, non-manifold geometry, "
            "extremely thin fragile parts, "
            "unstable contact surface"
        )

    normalized[
        "negative_prompt_en"
    ] = negative_prompt

    normalized[
        "system_printability_guidance"
    ] = _normalize_guidance(
        normalized.get(
            "system_printability_guidance"
        )
    )

    return {
        field_name: normalized.get(
            field_name
        )
        for field_name in (
            CREATIVE_ASSET_FIELDS
        )
    }


def _collect_errors(
    data: Any,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: 创意任务规格必须是JSON对象"
        ]

    validator = Draft202012Validator(
        CREATIVE_ASSET_SCHEMA
    )

    schema_errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    errors = [
        (
            f"{_format_error_path(error)}: "
            f"{error.message}"
        )
        for error in schema_errors
    ]

    needs_clarification = data.get(
        "needs_clarification"
    )

    question = data.get(
        "clarification_question"
    )

    output_target = data.get(
        "output_target"
    )

    target_height = data.get(
        "target_height_mm"
    )

    if (
        needs_clarification is True
        and not question
    ):
        errors.append(
            "$.clarification_question: "
            "需要澄清时必须提供问题"
        )

    if (
        needs_clarification is False
        and question is not None
    ):
        errors.append(
            "$.clarification_question: "
            "无需澄清时必须为null"
        )

    if (
        output_target == "3d_print"
        and target_height is None
        and needs_clarification is not True
    ):
        errors.append(
            "$.target_height_mm: "
            "打印任务缺少尺寸时必须请求澄清"
        )

    return errors


def _build_repair_input(
    *,
    original_input: str,
    previous_data: dict[str, Any],
    errors: list[str],
) -> str:
    return (
        "请修正上一轮创意3D任务规格。\n\n"
        f"原始用户请求：\n{original_input}\n\n"
        "上一轮结果：\n"
        + json.dumps(
            previous_data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n验证错误：\n"
        + "\n".join(
            f"- {error}"
            for error in errors
        )
        + "\n\n请重新输出完整JSON对象。"
        "不要解释，不要使用Markdown代码块。"
    )


def build_creative_spec(
    text: str,
    provider: StructuredOutputProvider,
) -> CreativeAssetSpec:
    cleaned = text.strip()

    if not cleaned:
        raise ValueError(
            "用户输入不能为空"
        )

    current_input = cleaned
    last_data: dict[str, Any] = {}
    last_errors: list[str] = []

    for attempt in range(2):
        raw_data = (
            provider.generate_structured(
                instructions=(
                    CREATIVE_SPEC_INSTRUCTIONS
                ),
                user_input=current_input,
                schema_name=(
                    "am_creative_asset_spec"
                ),
                schema=CREATIVE_ASSET_SCHEMA,
            )
        )

        raw_semantic_errors = (
            _collect_raw_semantic_errors(
                raw_data
            )
        )

        if raw_semantic_errors:
            data = (
                dict(raw_data)
                if isinstance(raw_data, dict)
                else {}
            )

            last_data = data
            last_errors = (
                raw_semantic_errors
            )

        else:
            data = _canonicalize_data(
                raw_data,
                original_input=cleaned,
            )

            last_data = data
            last_errors = (
                _collect_errors(data)
            )

        if not last_errors:
            return (
                CreativeAssetSpec.from_dict(
                    data
                )
            )

        if attempt == 0:
            current_input = (
                _build_repair_input(
                    original_input=cleaned,
                    previous_data=data,
                    errors=last_errors,
                )
            )

    raise ValueError(
        "创意任务规格经过一次自动纠错后"
        "仍不合法：\n"
        + "\n".join(last_errors)
        + "\n\n最后返回的数据：\n"
        + json.dumps(
            last_data,
            ensure_ascii=False,
            indent=2,
        )
    )