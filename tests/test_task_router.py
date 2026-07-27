import unittest
from typing import Any

from am_requirement_parser.routing.router import (
    route_request,
)


class FakeProvider:
    def __init__(
        self,
        result: dict[str, Any],
    ) -> None:
        self.result = result

    def generate_structured(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        return self.result


class TaskRouterTests(unittest.TestCase):
    def test_creative_asset_route(
        self,
    ) -> None:
        provider = FakeProvider(
            {
                "schema_version": "0.1.0",
                "task_type": (
                    "creative_asset"
                ),
                "intent_summary": (
                    "生成并打印一只坐着的"
                    "卡通小狗"
                ),
                "object_name": "卡通小狗",
                "target_output": "3d_print",
                "needs_clarification": False,
                "clarification_question": None,
                "confidence": 0.98,
                "routing_reason": (
                    "需求以动物造型和"
                    "视觉风格为主。"
                ),
            }
        )

        route = route_request(
            "给我打印一只坐着的卡通小狗",
            provider,
        )

        self.assertEqual(
            route.task_type,
            "creative_asset",
        )

        self.assertEqual(
            route.target_output,
            "3d_print",
        )

    def test_engineering_part_route(
        self,
    ) -> None:
        provider = FakeProvider(
            {
                "schema_version": "0.1.0",
                "task_type": (
                    "engineering_part"
                ),
                "intent_summary": (
                    "设计承受800 N载荷的"
                    "无人机支架"
                ),
                "object_name": "无人机支架",
                "target_output": "3d_model",
                "needs_clarification": False,
                "clarification_question": None,
                "confidence": 0.99,
                "routing_reason": (
                    "需求包含明确载荷和"
                    "功能性工程约束。"
                ),
            }
        )

        route = route_request(
            "设计一个承受800 N载荷的无人机支架",
            provider,
        )

        self.assertEqual(
            route.task_type,
            "engineering_part",
        )

    def test_invalid_result_is_rejected(
        self,
    ) -> None:
        provider = FakeProvider(
            {
                "task_type": "dog",
            }
        )

        with self.assertRaises(
            ValueError
        ):
            route_request(
                "打印一只小狗",
                provider,
            )

    def test_empty_input_is_rejected(
        self,
    ) -> None:
        provider = FakeProvider({})

        with self.assertRaises(
            ValueError
        ):
            route_request(
                "   ",
                provider,
            )


if __name__ == "__main__":
    unittest.main()