from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CreativeAssetSpec:
    schema_version: str
    task_type: str
    intent_summary: str
    object_name: str
    category: str
    visual_description: str
    style: str | None
    pose: str | None
    target_height_mm: float | None
    target_dimensions_text: str | None
    output_target: str
    generation_prompt_en: str
    negative_prompt_en: str
    system_printability_guidance: list[str]
    needs_clarification: bool
    clarification_question: str | None
    confidence: float

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "CreativeAssetSpec":
        target_height = data[
            "target_height_mm"
        ]

        return cls(
            schema_version=data[
                "schema_version"
            ],
            task_type=data["task_type"],
            intent_summary=data[
                "intent_summary"
            ],
            object_name=data["object_name"],
            category=data["category"],
            visual_description=data[
                "visual_description"
            ],
            style=data["style"],
            pose=data["pose"],
            target_height_mm=(
                None
                if target_height is None
                else float(target_height)
            ),
            target_dimensions_text=data[
                "target_dimensions_text"
            ],
            output_target=data[
                "output_target"
            ],
            generation_prompt_en=data[
                "generation_prompt_en"
            ],
            negative_prompt_en=data[
                "negative_prompt_en"
            ],
            system_printability_guidance=list(
                data[
                    "system_printability_guidance"
                ]
            ),
            needs_clarification=data[
                "needs_clarification"
            ],
            clarification_question=data[
                "clarification_question"
            ],
            confidence=float(
                data["confidence"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)