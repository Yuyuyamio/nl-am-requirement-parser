import unittest

from am_requirement_parser.parser import parse_requirement
from am_requirement_parser.validator import validate_spec


class ParserTests(unittest.TestCase):
    def test_example_requirement(self) -> None:
        text = (
            "设计一个用于无人机的轻量化支架，"
            "使用铝合金，通过LPBF打印，"
            "承受竖直方向800 N载荷，"
            "最小壁厚不能低于1 mm。"
        )

        spec = parse_requirement(text)
        loads = spec.boundary_conditions["loads"]

        self.assertEqual(
            spec.schema_version,
            "0.2.0",
        )

        self.assertEqual(
            spec.materials[0]["material_family"],
            "铝合金",
        )

        self.assertEqual(
            loads[0]["magnitude"],
            800.0,
        )

        self.assertEqual(
            loads[0]["unit"],
            "N",
        )

        self.assertEqual(
            spec.additive_manufacturing[
                "process"
            ]["name"],
            "LPBF",
        )

        self.assertEqual(
            spec.additive_manufacturing[
                "minimum_wall_thickness"
            ]["value"],
            1.0,
        )

        self.assertEqual(
            validate_spec(spec),
            [],
        )

    def test_kn_conversion(self) -> None:
        spec = parse_requirement(
            "设计一个支架，承受1.2 kN载荷。"
        )

        load = spec.boundary_conditions["loads"][0]

        self.assertEqual(
            load["magnitude"],
            1200.0,
        )

        self.assertEqual(
            load["unit"],
            "N",
        )

    def test_loads_are_serialized(self) -> None:
        spec = parse_requirement(
            "设计一个支架，承受500 N向下载荷。"
        )

        data = spec.to_dict()

        self.assertNotIn(
            "load_cases",
            data,
        )

        self.assertEqual(
            data["boundary_conditions"][
                "loads"
            ][0]["magnitude"],
            500.0,
        )

    def test_missing_information_is_structured(self) -> None:
        spec = parse_requirement(
            "设计一个轻量化支架。"
        )

        self.assertTrue(
            spec.missing_information
        )

        for item in spec.missing_information:
            self.assertIn("code", item)
            self.assertIn("field", item)
            self.assertIn("message", item)
            self.assertIn("priority", item)


if __name__ == "__main__":
    unittest.main()