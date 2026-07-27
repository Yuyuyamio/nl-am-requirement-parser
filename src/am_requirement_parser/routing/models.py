from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


VALID_TASK_TYPES = {
    "creative_asset",
    "engineering_part",
    "unknown",
}

VALID_TARGET_OUTPUTS = {
    "3d_print",
    "3d_model",
    "clarification_required",
}


@dataclass(frozen=True)
class TaskRoute:
    schema_version: str
    task_type: str
    intent_summary: str
    object_name: str | None
    target_output: str
    needs_clarification: bool
    clarification_question: str | None
    confidence: float
    routing_reason: str

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "TaskRoute":
        return cls(
            schema_version=data[
                "schema_version"
            ],
            task_type=data["task_type"],
            intent_summary=data[
                "intent_summary"
            ],
            object_name=data["object_name"],
            target_output=data[
                "target_output"
            ],
            needs_clarification=data[
                "needs_clarification"
            ],
            clarification_question=data[
                "clarification_question"
            ],
            confidence=float(
                data["confidence"]
            ),
            routing_reason=data[
                "routing_reason"
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)