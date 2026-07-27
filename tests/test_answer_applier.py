import unittest

from am_requirement_parser.answer_applier import (
    apply_clarification_answer,
)
from am_requirement_parser.parser import (
    parse_requirement,
)
from am_requirement_parser.schema_validator import (
    validate_against_schema,
)
from am_requirement_parser.validator import (
    validate_spec,
)


def apply_by_code(
    spec,
    code: str,
    answer: str,
):
    question = next(
        item
        for item in spec.clarification_questions
        if item["based_on_code"] == code
    )

    return apply_clarification_answer(
        spec,
        question["question_id"],
        answer,
    )


class AnswerApplierTests(unittest.TestCase):
    def test_answer_removes_missing_item(
        self,
    ) -> None:
        text = (
            "设计一个支架，使用PLA，"
            "通过FDM打印，承受500 N向下载荷。"
        )

        spec = parse_requirement(text)

        self.assertTrue(
            any(
                item["code"]
                == "LOAD_REGION_MISSING"
                for item in spec.missing_information
            )
        )

        spec = apply_by_code(
            spec,
            "LOAD_REGION_MISSING",
            "顶部连接孔",
        )

        self.assertFalse(
            any(
                item["code"]
                == "LOAD_REGION_MISSING"
                for item in spec.missing_information
            )
        )

    def test_load_answer_creates_followups(
        self,
    ) -> None:
        spec = parse_requirement(
            "设计一个支架，使用PLA，通过FDM打印。"
        )

        spec = apply_by_code(
            spec,
            "LOAD_MISSING",
            "1.2 kN",
        )

        load = (
            spec.boundary_conditions["loads"][0]
        )

        self.assertEqual(
            load["magnitude"],
            1200.0,
        )

        missing_codes = {
            item["code"]
            for item in spec.missing_information
        }

        self.assertIn(
            "LOAD_DIRECTION_MISSING",
            missing_codes,
        )

        self.assertIn(
            "LOAD_REGION_MISSING",
            missing_codes,
        )

    def test_example_can_be_completed(
        self,
    ) -> None:
        text = (
            "设计一个用于无人机的轻量化支架，"
            "使用铝合金，通过LPBF打印，"
            "承受竖直方向800 N载荷，"
            "最小壁厚不能低于1 mm。"
        )

        spec = parse_requirement(text)

        spec = apply_by_code(
            spec,
            "LOAD_REGION_MISSING",
            "顶部承力安装面",
        )

        spec = apply_by_code(
            spec,
            "FIXED_REGION_MISSING",
            "底部四个安装孔",
        )

        spec = apply_by_code(
            spec,
            "DESIGN_DOMAIN_MISSING",
            "120 mm × 80 mm × 40 mm",
        )

        spec = apply_by_code(
            spec,
            "MATERIAL_GRADE_MISSING",
            "AlSi10Mg",
        )

        self.assertEqual(
            spec.status,
            "complete",
        )

        self.assertEqual(
            spec.missing_information,
            [],
        )

        self.assertEqual(
            spec.clarification_questions,
            [],
        )

    def test_completed_spec_matches_schema(
        self,
    ) -> None:
        text = (
            "设计一个用于无人机的轻量化支架，"
            "使用铝合金，通过LPBF打印，"
            "承受竖直方向800 N载荷，"
            "最小壁厚不能低于1 mm。"
        )

        spec = parse_requirement(text)

        answers = {
            "LOAD_REGION_MISSING": "顶部安装面",
            "FIXED_REGION_MISSING": "底部安装孔",
            "DESIGN_DOMAIN_MISSING": (
                "120 mm × 80 mm × 40 mm"
            ),
            "MATERIAL_GRADE_MISSING": (
                "AlSi10Mg"
            ),
        }

        for code, answer in answers.items():
            spec = apply_by_code(
                spec,
                code,
                answer,
            )

        validate_spec(spec)

        errors = validate_against_schema(
            spec.to_dict()
        )

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()