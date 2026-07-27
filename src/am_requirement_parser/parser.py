from __future__ import annotations

import re

from .models import RequirementSpec

from .completeness import refresh_requirement_state

MATERIALS = (
    "铝合金",
    "钛合金",
    "不锈钢",
    "碳纤维",
    "PLA",
    "ABS",
    "PETG",
    "树脂",
)

PROCESSES = (
    "FDM",
    "SLA",
    "SLS",
    "LPBF",
    "DED",
    "WAAM",
)

DIRECTION_VECTORS = {
    "向下": [0.0, 0.0, -1.0],
    "竖直方向": [0.0, 0.0, -1.0],
    "垂直方向": [0.0, 0.0, -1.0],
    "向上": [0.0, 0.0, 1.0],
    "水平方向": None,
    "沿X轴": [1.0, 0.0, 0.0],
    "沿x轴": [1.0, 0.0, 0.0],
    "沿Y轴": [0.0, 1.0, 0.0],
    "沿y轴": [0.0, 1.0, 0.0],
    "沿Z轴": [0.0, 0.0, 1.0],
    "沿z轴": [0.0, 0.0, 1.0],
}



def _extract_product_name(text: str) -> str | None:
    patterns = (
        r"设计一个(?:用于[^，。；]+的)?([^，。；]+)",
        r"设计(?:一款|一种)?([^，。；]+)",
    )

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            name = re.sub(r"^(一个|一款|一种)", "", name)
            return name or None

    return None


def _extract_force(text: str) -> list[dict]:
    loads: list[dict] = []

    pattern = re.compile(
        r"(?P<value>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>kN|KN|kn|N|牛顿|牛)"
    )

    for index, match in enumerate(pattern.finditer(text), start=1):
        value = float(match.group("value"))
        original_unit = match.group("unit")

        if original_unit.lower() == "kn":
            value *= 1000.0

        direction_text = None
        direction_vector = None

        direction_match = re.search(
            r"(竖直方向|垂直方向|水平方向|向上|向下|沿[XYZxyz]轴)",
            text,
        )

        if direction_match:
            direction_text = direction_match.group(1)
            direction_vector = DIRECTION_VECTORS.get(direction_text)

        loads.append(
            {
                "load_id": f"LOAD-{index:03d}",
                "type": "force",
                "magnitude": value,
                "unit": "N",
                "direction_text": direction_text,
                "direction_vector": direction_vector,
                "reference_frame": "global_cartesian",
                "application_region": None,
                "source_type": "user_explicit",
                "source_text": match.group(0),
                "confirmation_status": "confirmed",
            }
        )

    return loads


def _extract_wall_thickness(text: str) -> dict | None:
    match = re.search(
        r"(?:最小壁厚|壁厚(?:不能|不得)?低于)\s*"
        r"(?:为|是|不能低于|不得低于)?\s*"
        r"(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米)",
        text,
    )

    if not match:
        return None

    value = float(match.group(1))
    original_unit = match.group(2)

    if original_unit in {"cm", "厘米"}:
        value *= 10.0

    return {
        "value": value,
        "unit": "mm",
        "source_type": "user_explicit",
        "source_text": match.group(0),
        "confirmation_status": "confirmed",
    }


def parse_requirement(text: str) -> RequirementSpec:
    cleaned = text.strip()

    spec = RequirementSpec(
        original_input={
            "input_type": "text",
            "raw_text": cleaned,
        }
    )

    # 产品名称
    product_name = _extract_product_name(cleaned)
    spec.product["name"] = product_name

    if product_name:
        spec.source_evidence.append(
            {
                "field": "product.name",
                "source_type": "user_explicit",
                "source_text": product_name,
                "confidence": 0.90,
            }
        )

    # 材料
    for material in MATERIALS:
        if material.lower() in cleaned.lower():
            spec.materials.append(
                {
                    "material_family": material,
                    "grade": None,
                    "properties": {},
                    "source_type": "user_explicit",
                    "source_text": material,
                    "confirmation_status": "confirmed",
                }
            )

    # 增材制造工艺
    for process in PROCESSES:
        if process.lower() in cleaned.lower():
            spec.additive_manufacturing["process"] = {
                "name": process,
                "source_type": "user_explicit",
                "source_text": process,
                "confirmation_status": "confirmed",
            }
            break

    # 载荷必须写入正式 boundary_conditions 字段
    spec.boundary_conditions["loads"] = _extract_force(cleaned)

    # 最小壁厚
    wall_thickness = _extract_wall_thickness(cleaned)

    if wall_thickness:
        spec.additive_manufacturing[
            "minimum_wall_thickness"
        ] = wall_thickness

    # 优化目标
    if any(
        word in cleaned
        for word in (
            "轻量化",
            "减重",
            "重量尽量小",
            "质量尽量小",
        )
    ):
        spec.optimization["objectives"].append(
            {
                "name": "minimize_mass",
                "source_type": "user_explicit",
                "source_text": "轻量化",
                "confirmation_status": "confirmed",
            }
        )

    return refresh_requirement_state(spec)