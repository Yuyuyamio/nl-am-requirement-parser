from __future__ import annotations

from typing import Any


CREATIVE_ASSET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "task_type",
        "intent_summary",
        "object_name",
        "category",
        "visual_description",
        "style",
        "pose",
        "target_height_mm",
        "target_dimensions_text",
        "output_target",
        "generation_prompt_en",
        "negative_prompt_en",
        "system_printability_guidance",
        "needs_clarification",
        "clarification_question",
        "confidence",
    ],
    "properties": {
        "schema_version": {
            "type": "string",
            "const": "0.1.0",
        },
        "task_type": {
            "type": "string",
            "const": "creative_asset",
        },
        "intent_summary": {
            "type": "string",
            "minLength": 1,
        },
        "object_name": {
            "type": "string",
            "minLength": 1,
        },
        "category": {
            "type": "string",
            "enum": [
                "animal",
                "character",
                "sculpture",
                "toy",
                "decoration",
                "container",
                "household_object",
                "other",
            ],
        },
        "visual_description": {
            "type": "string",
            "minLength": 1,
        },
        "style": {
            "type": [
                "string",
                "null",
            ],
        },
        "pose": {
            "type": [
                "string",
                "null",
            ],
        },
        "target_height_mm": {
            "type": [
                "number",
                "null",
            ],
            "exclusiveMinimum": 0,
        },
        "target_dimensions_text": {
            "type": [
                "string",
                "null",
            ],
        },
        "output_target": {
            "type": "string",
            "enum": [
                "3d_print",
                "3d_model",
            ],
        },
        "generation_prompt_en": {
            "type": "string",
            "minLength": 1,
        },
        "negative_prompt_en": {
            "type": "string",
            "minLength": 1,
        },
        "system_printability_guidance": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
            },
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
    },
}