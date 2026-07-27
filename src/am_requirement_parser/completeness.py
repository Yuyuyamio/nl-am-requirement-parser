from __future__ import annotations

from typing import Any

from .clarification import build_clarification_questions
from .models import RequirementSpec


def _missing(
    code: str,
    field: str,
    message: str,
    priority: str = "critical",
) -> dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "priority": priority,
    }


def evaluate_missing_information(
    spec: RequirementSpec,
) -> list[dict[str, Any]]:
    """根据当前需求对象重新检查工程信息是否完整。"""

    missing: list[dict[str, Any]] = []

    if not spec.product.get("name"):
        missing.append(
            _missing(
                "PRODUCT_NAME_MISSING",
                "product.name",
                "产品名称未指定",
            )
        )

    if not spec.materials:
        missing.append(
            _missing(
                "MATERIAL_MISSING",
                "materials",
                "材料未指定",
            )
        )
    elif spec.materials[0].get("grade") is None:
        missing.append(
            _missing(
                "MATERIAL_GRADE_MISSING",
                "materials[0].grade",
                "材料具体牌号未指定",
                "important",
            )
        )

    loads = spec.boundary_conditions.get(
        "loads",
        [],
    )

    if not loads:
        missing.append(
            _missing(
                "LOAD_MISSING",
                "boundary_conditions.loads",
                "载荷未指定",
            )
        )
    else:
        first_load = loads[0]

        if first_load.get("direction_text") is None:
            missing.append(
                _missing(
                    "LOAD_DIRECTION_MISSING",
                    (
                        "boundary_conditions.loads[0]."
                        "direction"
                    ),
                    "载荷方向未指定",
                )
            )

        if first_load.get("application_region") is None:
            missing.append(
                _missing(
                    "LOAD_REGION_MISSING",
                    (
                        "boundary_conditions.loads[0]."
                        "application_region"
                    ),
                    "载荷作用区域未指定",
                )
            )

    process = spec.additive_manufacturing.get(
        "process"
    )

    if process is None:
        missing.append(
            _missing(
                "AM_PROCESS_MISSING",
                "additive_manufacturing.process",
                "增材制造工艺未指定",
            )
        )

    fixed_regions = spec.boundary_conditions.get(
        "fixed_regions",
        [],
    )

    if not fixed_regions:
        missing.append(
            _missing(
                "FIXED_REGION_MISSING",
                "boundary_conditions.fixed_regions",
                "固定区域未指定",
            )
        )

    if spec.geometry.get("design_domain") is None:
        missing.append(
            _missing(
                "DESIGN_DOMAIN_MISSING",
                "geometry.design_domain",
                "设计域尺寸未指定",
            )
        )

    return missing


def refresh_requirement_state(
    spec: RequirementSpec,
) -> RequirementSpec:
    """重新生成缺失信息、追问列表和完成状态。"""

    spec.missing_information = (
        evaluate_missing_information(spec)
    )

    spec.clarification_questions = (
        build_clarification_questions(
            spec.missing_information
        )
    )

    spec.status = (
        "complete"
        if not spec.missing_information
        else "incomplete"
    )

    spec.validation["engineering_complete"] = (
        not spec.missing_information
    )

    return spec