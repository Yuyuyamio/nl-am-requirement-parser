from __future__ import annotations

from typing import Any


PRIORITY_ORDER = {
    "critical": 0,
    "important": 1,
    "optional": 2,
}


QUESTION_TEMPLATES = {
    "PRODUCT_NAME_MISSING": {
        "question": "需要设计的产品或零件叫什么？",
        "answer_type": "text",
    },
    "MATERIAL_MISSING": {
        "question": "计划使用什么材料？",
        "answer_type": "text",
    },
    "MATERIAL_GRADE_MISSING": {
        "question": (
            "请指定材料的具体牌号，"
            "例如 AlSi10Mg、Ti-6Al-4V 或 316L。"
        ),
        "answer_type": "text",
    },
    "LOAD_MISSING": {
        "question": "产品需要承受什么载荷，大小是多少？",
        "answer_type": "quantity",
    },
    "LOAD_DIRECTION_MISSING": {
        "question": "载荷的方向是什么？",
        "answer_type": "direction",
    },
    "LOAD_REGION_MISSING": {
        "question": "载荷具体作用在哪个面、孔或区域？",
        "answer_type": "region",
    },
    "AM_PROCESS_MISSING": {
        "question": (
            "计划采用哪种增材制造工艺，"
            "例如 FDM、SLA、SLS 或 LPBF？"
        ),
        "answer_type": "single_choice",
    },
    "FIXED_REGION_MISSING": {
        "question": "零件的哪些面、孔或区域需要固定？",
        "answer_type": "region",
    },
    "DESIGN_DOMAIN_MISSING": {
        "question": "请提供允许设计的空间尺寸或包络范围。",
        "answer_type": "dimensions",
    },
}


def build_clarification_questions(
    missing_information: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """根据缺失信息生成按优先级排列的问题。"""

    indexed_items = list(
        enumerate(missing_information)
    )

    sorted_items = sorted(
        indexed_items,
        key=lambda pair: (
            PRIORITY_ORDER.get(
                pair[1].get("priority", "optional"),
                99,
            ),
            pair[0],
        ),
    )

    questions: list[dict[str, Any]] = []

    for question_index, (_, item) in enumerate(
        sorted_items,
        start=1,
    ):
        code = item["code"]

        template = QUESTION_TEMPLATES.get(
            code,
            {
                "question": item["message"],
                "answer_type": "text",
            },
        )

        questions.append(
            {
                "question_id": (
                    f"QUESTION-{question_index:03d}"
                ),
                "based_on_code": code,
                "field": item["field"],
                "question": template["question"],
                "priority": item["priority"],
                "answer_type": template["answer_type"],
                "status": "pending",
            }
        )

    return questions