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
from am_requirement_parser.unit_normalizer import (
    parse_box_dimensions,
    parse_force_quantity,
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


class UnitNormalizerTests(unittest.TestCase):
    def test_force_kn_is_converted_to_n(
        self,
    ) -> None:
        force = parse_force_quantity(
            "1.2 kN"
        )

        self.assertEqual(
            force["value"],
            1200.0,
        )

        self.assertEqual(
            force["unit"],
            "N",
        )

        self.assertEqual(
            force["original_value"],
            1.2,
        )

        self.assertEqual(
            force["original_unit"],
            "kN",
        )

    def test_cm_dimensions_are_converted_to_mm(
        self,
    ) -> None:
        domain = parse_box_dimensions(
            "12 cm × 8 cm × 4 cm"
        )

        dimensions = domain["dimensions"]

        self.assertEqual(
            dimensions["length"]["value"],
            120.0,
        )

        self.assertEqual(
            dimensions["width"]["value"],
            80.0,
        )

        self.assertEqual(
            dimensions["height"]["value"],
            40.0,
        )

    def test_trailing_unit_applies_to_all(
        self,
    ) -> None:
        domain = parse_box_dimensions(
            "120 × 80 × 40 mm"
        )

        dimensions = domain["dimensions"]

        self.assertEqual(
            dimensions["length"]["value"],
            120.0,
        )

        self.assertEqual(
            dimensions["width"]["value"],
            80.0,
        )

        self.assertEqual(
            dimensions["height"]["value"],
            40.0,
        )

    def test_invalid_dimensions_are_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            ValueError
        ):
            parse_box_dimensions(
                "大概一个鞋盒那么大"
            )

    def test_answered_domain_matches_schema(
        self,
    ) -> None:
        spec = parse_requirement(
            "设计一个支架，使用PLA，"
            "通过FDM打印，承受500 N向下载荷。"
        )

        spec = apply_by_code(
            spec,
            "DESIGN_DOMAIN_MISSING",
            "12 cm × 8 cm × 4 cm",
        )

        validate_spec(spec)

        errors = validate_against_schema(
            spec.to_dict()
        )

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()