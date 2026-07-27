from __future__ import annotations

import re
from typing import Any


FORCE_FACTORS_TO_N = {
    "N": 1.0,
    "牛": 1.0,
    "牛顿": 1.0,
    "kN": 1000.0,
    "KN": 1000.0,
    "kn": 1000.0,
}

LENGTH_FACTORS_TO_MM = {
    "mm": 1.0,
    "毫米": 1.0,
    "cm": 10.0,
    "厘米": 10.0,
    "m": 1000.0,
    "米": 1000.0,
}


def parse_force_quantity(
    text: str,
) -> dict[str, Any]:
    """从文本中提取力，并统一转换为N。"""

    cleaned = text.strip()

    if not cleaned:
        raise ValueError("载荷回答不能为空")

    match = re.search(
        r"(?P<value>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>kN|KN|kn|N|牛顿|牛)",
        cleaned,
    )

    if not match:
        raise ValueError(
            "载荷必须包含数值和单位，"
            "例如800 N或1.2 kN"
        )

    original_value = float(
        match.group("value")
    )
    original_unit = match.group("unit")

    value_in_n = (
        original_value
        * FORCE_FACTORS_TO_N[original_unit]
    )

    if value_in_n <= 0:
        raise ValueError("载荷必须大于0")

    return {
        "value": value_in_n,
        "unit": "N",
        "original_value": original_value,
        "original_unit": original_unit,
        "source_text": match.group(0),
    }


def normalize_length(
    value: float,
    unit: str,
) -> float:
    """把长度统一转换为mm。"""

    if unit not in LENGTH_FACTORS_TO_MM:
        raise ValueError(
            f"暂不支持长度单位：{unit}"
        )

    value_in_mm = (
        value
        * LENGTH_FACTORS_TO_MM[unit]
    )

    if value_in_mm <= 0:
        raise ValueError("长度必须大于0")

    return value_in_mm


def parse_box_dimensions(
    text: str,
) -> dict[str, Any]:
    """
    解析长方体设计包络。

    支持：
    - 120 mm × 80 mm × 40 mm
    - 120×80×40 mm
    - 12 cm x 8 cm x 4 cm
    - 0.12 m * 0.08 m * 0.04 m
    """

    cleaned = text.strip()

    if not cleaned:
        raise ValueError("设计域尺寸不能为空")

    number = r"\d+(?:\.\d+)?"
    unit = r"(?:mm|毫米|cm|厘米|m|米)"
    token = rf"({number})\s*({unit})?"

    pattern = re.compile(
        rf"^\s*"
        rf"{token}\s*[xX×*]\s*"
        rf"{token}\s*[xX×*]\s*"
        rf"{token}"
        rf"\s*$"
    )

    match = pattern.fullmatch(cleaned)

    if not match:
        raise ValueError(
            "设计域尺寸格式无法识别。"
            "请使用类似"
            "120 mm × 80 mm × 40 mm的格式。"
        )

    values = [
        float(match.group(1)),
        float(match.group(3)),
        float(match.group(5)),
    ]

    units = [
        match.group(2),
        match.group(4),
        match.group(6),
    ]

    explicit_units = {
        item
        for item in units
        if item is not None
    }

    if not explicit_units:
        raise ValueError(
            "设计域尺寸必须包含长度单位，"
            "例如mm、cm或m"
        )

    if len(explicit_units) > 1 and any(
        item is None for item in units
    ):
        raise ValueError(
            "部分尺寸缺少单位且存在多种单位，"
            "无法判断缺失尺寸使用哪个单位"
        )

    if len(explicit_units) == 1:
        fallback_unit = next(
            iter(explicit_units)
        )
        units = [
            item or fallback_unit
            for item in units
        ]

    if any(item is None for item in units):
        raise ValueError(
            "每个尺寸都必须指定单位"
        )

    length_mm = normalize_length(
        values[0],
        units[0],
    )
    width_mm = normalize_length(
        values[1],
        units[1],
    )
    height_mm = normalize_length(
        values[2],
        units[2],
    )

    return {
        "shape": "rectangular_prism",
        "dimensions": {
            "length": {
                "value": length_mm,
                "unit": "mm",
            },
            "width": {
                "value": width_mm,
                "unit": "mm",
            },
            "height": {
                "value": height_mm,
                "unit": "mm",
            },
        },
        "source_type": "user_clarification",
        "source_text": cleaned,
        "confirmation_status": "confirmed",
    }