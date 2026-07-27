from __future__ import annotations

from typing import Any


TASK_ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "task_type",
        "intent_summary",
        "object_name",
        "target_output",
        "needs_clarification",
        "clarification_question",
        "confidence",
        "routing_reason",
    ],
    "properties": {
        "schema_version": {
            "type": "string",
            "const": "0.1.0",
        },
        "task_type": {
            "type": "string",
            "enum": [
                "creative_asset",
                "engineering_part",
                "unknown",
            ],
        },
        "intent_summary": {
            "type": "string",
            "minLength": 1,
        },
        "object_name": {
            "type": [
                "string",
                "null",
            ],
        },
        "target_output": {
            "type": "string",
            "enum": [
                "3d_print",
                "3d_model",
                "clarification_required",
            ],
        },
        "needs_clarification": {
            "type": "boolean",
        },
        "clarification_question": {
            "type": [
                "string",
                "null",
            ],
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "routing_reason": {
            "type": "string",
            "minLength": 1,
        },
    },
}