from __future__ import annotations

from .models import RequirementSpec


def validate_spec(spec: RequirementSpec) -> list[str]:
    errors: list[str] = []

    if not spec.original_input.get("raw_text"):
        errors.append("原始输入不能为空")

    if not isinstance(spec.product, dict):
        errors.append("product必须是对象")

    if not isinstance(spec.materials, list):
        errors.append("materials必须是数组")

    if not isinstance(spec.boundary_conditions, dict):
        errors.append("boundary_conditions必须是对象")

    loads = spec.boundary_conditions.get("loads", [])

    if not isinstance(loads, list):
        errors.append("boundary_conditions.loads必须是数组")
        loads = []

    for index, load in enumerate(loads):
        magnitude = load.get("magnitude")

        if not isinstance(magnitude, (int, float)):
            errors.append(f"载荷{index + 1}的数值必须是数字")
        elif magnitude <= 0:
            errors.append(f"载荷{index + 1}的数值必须大于0")

        if load.get("unit") != "N":
            errors.append(f"载荷{index + 1}必须归一化为N")

        direction_vector = load.get("direction_vector")

        if direction_vector is not None:
            if (
                not isinstance(direction_vector, list)
                or len(direction_vector) != 3
            ):
                errors.append(
                    f"载荷{index + 1}的方向向量必须包含3个分量"
                )

    wall = spec.additive_manufacturing.get(
        "minimum_wall_thickness"
    )

    if wall is not None:
        value = wall.get("value")

        if not isinstance(value, (int, float)):
            errors.append("最小壁厚数值必须是数字")
        elif value <= 0:
            errors.append("最小壁厚必须大于0")

        if wall.get("unit") != "mm":
            errors.append("最小壁厚必须归一化为mm")

    if not isinstance(spec.missing_information, list):
        errors.append("missing_information必须是数组")
    else:
        for item in spec.missing_information:
            if not isinstance(item, dict):
                errors.append(
                    "missing_information中的每一项必须是对象"
                )
                break

    if spec.status == "complete" and spec.missing_information:
        errors.append(
            "状态为complete时不能存在missing_information"
        )

    spec.validation["unit_valid"] = not any(
        "归一化" in error for error in errors
    )
    spec.validation["engineering_complete"] = (
        not spec.missing_information
    )

    return errors