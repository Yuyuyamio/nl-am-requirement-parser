import unittest
from typing import Any

from am_requirement_parser.creative.spec_builder import (
    build_creative_spec,
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


def valid_dog_spec() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_type": "creative_asset",
        "intent_summary": (
            "生成并打印一只高度100毫米的"
            "坐姿卡通小狗"
        ),
        "object_name": "坐姿卡通小狗",
        "category": "animal",
        "visual_description": (
            "一只坐着的可爱卡通小狗，"
            "头部略大，身体圆润。"
        ),
        "style": "可爱卡通风格",
        "pose": "坐姿",
        "target_height_mm": 100.0,
        "target_dimensions_text": "高度10厘米",
        "output_target": "3d_print",
        "generation_prompt_en": (
            "A cute stylized cartoon puppy sitting "
            "upright, rounded body, slightly oversized "
            "head, friendly expression, designed as a "
            "single printable 3D figurine."
        ),
        "negative_prompt_en": (
            "floating parts, disconnected limbs, "
            "open mesh, non-manifold geometry, "
            "extremely thin legs, unstable base"
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
        "confidence": 0.98,
    }


class CreativeSpecTests(unittest.TestCase):
    def test_dog_spec_is_created(
        self,
    ) -> None:
        provider = SequenceProvider(
            [valid_dog_spec()]
        )

        spec = build_creative_spec(
            "给我打印一只坐着的卡通小狗，"
            "高度10厘米",
            provider,
        )

        self.assertEqual(
            spec.task_type,
            "creative_asset",
        )

        self.assertEqual(
            spec.target_height_mm,
            100.0,
        )

        self.assertEqual(
            spec.output_target,
            "3d_print",
        )

    def test_missing_size_can_request_clarification(
        self,
    ) -> None:
        result = valid_dog_spec()
        result["target_height_mm"] = None
        result["target_dimensions_text"] = None
        result["needs_clarification"] = True
        result["clarification_question"] = (
            "希望成品高度是多少毫米？"
        )

        provider = SequenceProvider([result])

        spec = build_creative_spec(
            "给我打印一只小狗",
            provider,
        )

        self.assertTrue(
            spec.needs_clarification
        )

    def test_invalid_first_result_is_repaired(
        self,
    ) -> None:
        provider = SequenceProvider(
            [
                {
                    "task_type": (
                        "creative_asset"
                    )
                },
                valid_dog_spec(),
            ]
        )

        spec = build_creative_spec(
            "打印一只卡通小狗，高度10厘米",
            provider,
        )

        self.assertEqual(
            provider.call_count,
            2,
        )

        self.assertEqual(
            spec.object_name,
            "坐姿卡通小狗",
        )

    def test_invalid_result_after_retry_is_rejected(
        self,
    ) -> None:
        provider = SequenceProvider(
            [
                {"task_type": "creative_asset"},
                {"task_type": "creative_asset"},
            ]
        )

        with self.assertRaises(
            ValueError
        ):
            build_creative_spec(
                "打印一只小狗",
                provider,
            )


if __name__ == "__main__":
    unittest.main()