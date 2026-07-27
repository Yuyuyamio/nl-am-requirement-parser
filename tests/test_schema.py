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


class SchemaTests(unittest.TestCase):
    def test_generated_requirement_matches_schema(
        self,
    ) -> None:
        text = (
            "设计一个用于无人机的轻量化支架，"
            "使用铝合金，通过LPBF打印，"
            "承受竖直方向800 N载荷，"
            "最小壁厚不能低于1 mm。"
        )

        spec = parse_requirement(text)
        validate_spec(spec)

        errors = validate_against_schema(
            spec.to_dict()
        )

        self.assertEqual(errors, [])

    def test_invalid_schema_version_is_rejected(
        self,
    ) -> None:
        spec = parse_requirement(
            "设计一个支架。"
        )

        data = spec.to_dict()
        data["schema_version"] = "9.9.9"

        errors = validate_against_schema(data)

        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()