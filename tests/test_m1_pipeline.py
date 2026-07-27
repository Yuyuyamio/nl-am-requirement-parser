import unittest
from typing import Any
from unittest.mock import patch

from am_requirement_parser.m1_pipeline import (
    run_m1_pipeline,
)


class SequenceProvider:
    def __init__(
        self,
        results: list[dict[str, Any]],
    ) -> None:
        self.results = results
        self.call_count = 0

    def generate_structured(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        index = min(
            self.call_count,
            len(self.results) - 1,
        )

        result = self.results[index]
        self.call_count += 1

        return result


def creative_route() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_type": "creative_asset",
        "intent_summary": (
            "打印一只坐着的卡通小狗"
        ),
        "object_name": "卡通小狗",
        "target_output": "3d_print",
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.98,
        "routing_reason": (
            "该需求以视觉造型为主"
        ),
    }


def engineering_route() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_type": "engineering_part",
        "intent_summary": (
            "设计一个承力支架"
        ),
        "object_name": "承力支架",
        "target_output": "3d_print",
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.97,
        "routing_reason": (
            "包含载荷和工程功能要求"
        ),
    }


def unknown_route() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_type": "unknown",
        "intent_summary": (
            "需求信息不足"
        ),
        "object_name": "未知对象",
        "target_output": "3d_model",
        "needs_clarification": True,
        "clarification_question": (
            "希望生成什么对象？"
        ),
        "confidence": 0.4,
        "routing_reason": (
            "无法判断任务类别"
        ),
    }


def creative_spec() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_type": "creative_asset",
        "intent_summary": (
            "打印一只高度100毫米的"
            "坐姿卡通小狗"
        ),
        "object_name": "cartoon dog",
        "category": "animal",
        "visual_description": (
            "一只坐着的卡通小狗"
        ),
        "style": "cartoon",
        "pose": "sitting",
        "target_height_mm": 100.0,
        "target_dimensions_text": (
            "高度100毫米"
        ),
        "output_target": "3d_print",
        "generation_prompt_en": (
            "A cartoon dog sitting upright, "
            "designed as a printable 3D model."
        ),
        "negative_prompt_en": (
            "floating geometry, open mesh, "
            "thin fragile parts"
        ),
        "system_printability_guidance": [
            "watertight closed mesh",
            "single connected main body",
            "flat and stable contact surface",
            "no floating geometry",
            "avoid extremely thin fragile parts",
        ],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.9,
    }


class FakeEngineeringSpec:
    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "schema_version": "0.2.0",
            "status": "incomplete",
            "missing_information": [
                {
                    "code": "FIXED_REGION_MISSING",
                    "field": (
                        "boundary_conditions."
                        "fixed_regions"
                    ),
                    "message": (
                        "固定区域未指定"
                    ),
                    "priority": "critical",
                }
            ],
        }


class M1PipelineTests(
    unittest.TestCase
):
    def test_creative_route_builds_creative_spec(
        self,
    ) -> None:
        provider = SequenceProvider(
            [
                creative_route(),
                creative_spec(),
            ]
        )

        result = run_m1_pipeline(
            "给我打印一只坐着的卡通小狗，"
            "高度10厘米",
            provider,
        )

        self.assertEqual(
            result.task_type,
            "creative_asset",
        )

        self.assertEqual(
            result.output_filename,
            "creative_asset_spec.json",
        )

        self.assertEqual(
            result.next_module,
            "M2",
        )

        self.assertEqual(
            provider.call_count,
            2,
        )

    def test_engineering_route_builds_requirement_spec(
        self,
    ) -> None:
        provider = SequenceProvider(
            [
                engineering_route(),
            ]
        )

        with patch(
            (
                "am_requirement_parser."
                "m1_pipeline.parse_requirement"
            ),
            return_value=(
                FakeEngineeringSpec()
            ),
        ):
            result = run_m1_pipeline(
                "设计一个承受800N载荷的"
                "铝合金支架",
                provider,
            )

        self.assertEqual(
            result.task_type,
            "engineering_part",
        )

        self.assertEqual(
            result.output_filename,
            "requirement_spec.json",
        )

        self.assertEqual(
            result.status,
            "incomplete",
        )

        self.assertIsNone(
            result.next_module
        )

    def test_unknown_route_stops_in_m1(
        self,
    ) -> None:
        provider = SequenceProvider(
            [
                unknown_route(),
            ]
        )

        result = run_m1_pipeline(
            "帮我做一个东西",
            provider,
        )

        self.assertEqual(
            result.task_type,
            "unknown",
        )

        self.assertEqual(
            result.status,
            "needs_clarification",
        )

        self.assertEqual(
            result.output_filename,
            "unresolved_task.json",
        )

        self.assertIsNone(
            result.next_module
        )


if __name__ == "__main__":
    unittest.main()