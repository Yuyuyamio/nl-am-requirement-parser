import unittest

from am_requirement_parser.parser import (
    parse_requirement,
)
from am_requirement_parser.schema_validator import (
    validate_against_schema,
)
from am_requirement_parser.validator import (
    validate_spec,
)


class ClarificationTests(unittest.TestCase):
    def test_questions_match_missing_items(
        self,
    ) -> None:
        spec = parse_requirement(
            "设计一个轻量化支架。"
        )

        self.assertEqual(
            len(spec.clarification_questions),
            len(spec.missing_information),
        )

        missing_codes = {
            item["code"]
            for item in spec.missing_information
        }

        question_codes = {
            item["based_on_code"]
            for item in spec.clarification_questions
        }

        self.assertEqual(
            missing_codes,
            question_codes,
        )

    def test_critical_questions_come_first(
        self,
    ) -> None:
        text = (
            "设计一个用于无人机的轻量化支架，"
            "使用铝合金，通过LPBF打印，"
            "承受竖直方向800 N载荷，"
            "最小壁厚不能低于1 mm。"
        )

        spec = parse_requirement(text)

        first_question = (
            spec.clarification_questions[0]
        )

        self.assertEqual(
            first_question["priority"],
            "critical",
        )

        self.assertEqual(
            first_question["based_on_code"],
            "LOAD_REGION_MISSING",
        )

        self.assertEqual(
            first_question["question"],
            "载荷具体作用在哪个面、孔或区域？",
        )

    def test_questions_match_json_schema(
        self,
    ) -> None:
        spec = parse_requirement(
            "设计一个支架，承受500 N向下载荷。"
        )

        validate_spec(spec)

        errors = validate_against_schema(
            spec.to_dict()
        )

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()