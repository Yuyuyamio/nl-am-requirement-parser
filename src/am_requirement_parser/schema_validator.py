from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "requirement_spec.schema.json"
)


def _format_error_path(error: Any) -> str:
    if not error.absolute_path:
        return "$"

    path = "$"

    for part in error.absolute_path:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"

    return path


def validate_against_schema(
    data: dict[str, Any],
    schema_path: Path | None = None,
) -> list[str]:
    target_path = schema_path or DEFAULT_SCHEMA_PATH

    if not target_path.exists():
        return [
            f"找不到JSON Schema文件：{target_path}"
        ]

    schema = json.loads(
        target_path.read_text(encoding="utf-8")
    )

    validator = Draft202012Validator(schema)

    errors = sorted(
        validator.iter_errors(data),
        key=lambda item: (
            list(item.absolute_path),
            item.message,
        ),
    )

    return [
        f"{_format_error_path(error)}: {error.message}"
        for error in errors
    ]