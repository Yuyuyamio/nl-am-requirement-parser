from __future__ import annotations

from typing import Any

from .completeness import (
    refresh_requirement_state,
)
from .models import RequirementSpec
from .unit_normalizer import (
    parse_box_dimensions,
    parse_force_quantity,
)


SUPPORTED_PROCESSES = {
    "FDM",
    "SLA",
    "SLS",
    "LPBF",
    "DED",
    "WAAM",
}


DIRECTION_VECTORS = {
    "向下": [0.0, 0.0, -1.0],
    "竖直向下": [0.0, 0.0, -1.0],
    "向上": [0.0, 0.0, 1.0],
    "竖直向上": [0.0, 0.0, 1.0],
    "X正方向": [1.0, 0.0, 0.0],
    "X负方向": [-1.0, 0.0, 0.0],
    "Y正方向": [0.0, 1.0, 0.0],
    "Y负方向": [0.0, -1.0, 0.0],
    "Z正方向": [0.0, 0.0, 1.0],
    "Z负方向": [0.0, 0.0, -1.0],
}


def _require_text(answer: str) -> str:
    cleaned = answer.strip()

    if not cleaned:
        raise ValueError("回答不能为空")

    return cleaned


def _parse_direction(
    answer: str,
) -> tuple[str, list[float]]:
    cleaned = _require_text(answer)

    for direction_text, vector in (
        DIRECTION_VECTORS.items()
    ):
        if (
            direction_text.lower()
            in cleaned.lower()
        ):
            return direction_text, vector

    raise ValueError(
        "暂时无法识别该方向。"
        "请使用向上、向下、X正方向、X负方向、"
        "Y正方向、Y负方向、Z正方向或Z负方向。"
    )


def _find_question(
    spec: RequirementSpec,
    question_id: str,
) -> dict[str, Any]:
    for question in (
        spec.clarification_questions
    ):
        if (
            question["question_id"]
            == question_id
        ):
            return question

    raise ValueError(
        f"找不到待回答问题：{question_id}"
    )


def _record_evidence(
    spec: RequirementSpec,
    field: str,
    answer: str,
) -> None:
    spec.source_evidence.append(
        {
            "field": field,
            "source_type": (
                "user_clarification"
            ),
            "source_text": answer,
            "confidence": 1.0,
            "confirmation_status": (
                "confirmed"
            ),
        }
    )


def apply_clarification_answer(
    spec: RequirementSpec,
    question_id: str,
    answer: str,
) -> RequirementSpec:
    """把一条追问回答写回RequirementSpec。"""

    question = _find_question(
        spec,
        question_id,
    )

    code = question["based_on_code"]
    field = question["field"]
    cleaned = _require_text(answer)

    if code == "PRODUCT_NAME_MISSING":
        spec.product["name"] = cleaned

    elif code == "MATERIAL_MISSING":
        spec.materials.append(
            {
                "material_family": cleaned,
                "grade": None,
                "properties": {},
                "source_type": (
                    "user_clarification"
                ),
                "source_text": cleaned,
                "confirmation_status": (
                    "confirmed"
                ),
            }
        )

    elif code == "MATERIAL_GRADE_MISSING":
        if not spec.materials:
            raise ValueError(
                "必须先指定材料，"
                "再指定材料牌号"
            )

        spec.materials[0]["grade"] = (
            cleaned
        )

    elif code == "LOAD_MISSING":
        force = parse_force_quantity(
            cleaned
        )

        load_number = (
            len(
                spec.boundary_conditions[
                    "loads"
                ]
            )
            + 1
        )

        spec.boundary_conditions[
            "loads"
        ].append(
            {
                "load_id": (
                    f"LOAD-{load_number:03d}"
                ),
                "type": "force",
                "magnitude": force["value"],
                "unit": force["unit"],
                "direction_text": None,
                "direction_vector": None,
                "reference_frame": (
                    "global_cartesian"
                ),
                "application_region": None,
                "source_type": (
                    "user_clarification"
                ),
                "source_text": (
                    force["source_text"]
                ),
                "original_value": (
                    force["original_value"]
                ),
                "original_unit": (
                    force["original_unit"]
                ),
                "confirmation_status": (
                    "confirmed"
                ),
            }
        )

    elif code == "LOAD_DIRECTION_MISSING":
        loads = spec.boundary_conditions[
            "loads"
        ]

        if not loads:
            raise ValueError(
                "必须先指定载荷大小，"
                "再指定方向"
            )

        direction_text, vector = (
            _parse_direction(cleaned)
        )

        loads[0]["direction_text"] = (
            direction_text
        )
        loads[0]["direction_vector"] = (
            vector
        )

    elif code == "LOAD_REGION_MISSING":
        loads = spec.boundary_conditions[
            "loads"
        ]

        if not loads:
            raise ValueError(
                "必须先指定载荷，"
                "再指定作用区域"
            )

        loads[0]["application_region"] = {
            "description": cleaned,
            "source_type": (
                "user_clarification"
            ),
        }

    elif code == "AM_PROCESS_MISSING":
        process = cleaned.upper()

        if process not in (
            SUPPORTED_PROCESSES
        ):
            raise ValueError(
                "暂时支持的工艺为："
                + ", ".join(
                    sorted(
                        SUPPORTED_PROCESSES
                    )
                )
            )

        spec.additive_manufacturing[
            "process"
        ] = {
            "name": process,
            "source_type": (
                "user_clarification"
            ),
            "source_text": cleaned,
            "confirmation_status": (
                "confirmed"
            ),
        }

    elif code == "FIXED_REGION_MISSING":
        spec.boundary_conditions[
            "fixed_regions"
        ].append(
            {
                "description": cleaned,
                "source_type": (
                    "user_clarification"
                ),
                "confirmation_status": (
                    "confirmed"
                ),
            }
        )

    elif code == "DESIGN_DOMAIN_MISSING":
        spec.geometry[
            "design_domain"
        ] = parse_box_dimensions(
            cleaned
        )

    else:
        raise ValueError(
            "暂不支持处理该问题类型："
            f"{code}"
        )

    _record_evidence(
        spec,
        field,
        cleaned,
    )

    return refresh_requirement_state(spec)