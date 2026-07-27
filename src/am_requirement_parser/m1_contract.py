from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


PROJECT_ROOT = (
    Path(__file__).resolve().parents[2]
)

DEFAULT_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m1_manifest.schema.json"
)


def _format_error_path(
    error: Any,
) -> str:
    if not error.absolute_path:
        return "$"

    path = "$"

    for part in error.absolute_path:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"

    return path


def load_m1_manifest_schema(
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    path = (
        Path(schema_path)
        if schema_path is not None
        else DEFAULT_SCHEMA_PATH
    )

    if not path.exists():
        raise ValueError(
            "M1 Manifest Schema不存在："
            f"{path}"
        )

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except json.JSONDecodeError as error:
        raise ValueError(
            "M1 Manifest Schema不是合法JSON："
            f"{path}"
        ) from error
    except OSError as error:
        raise ValueError(
            "无法读取M1 Manifest Schema："
            f"{path}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError(
            "M1 Manifest Schema必须是JSON对象"
        )

    try:
        Draft202012Validator.check_schema(
            data
        )
    except Exception as error:
        raise ValueError(
            "M1 Manifest Schema自身不合法："
            f"{error}"
        ) from error

    return data


def validate_m1_manifest(
    data: Any,
    *,
    schema_path: str | Path | None = None,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: M1 Manifest必须是JSON对象"
        ]

    schema = load_m1_manifest_schema(
        schema_path
    )

    validator = Draft202012Validator(
        schema
    )

    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    return [
        (
            f"{_format_error_path(error)}: "
            f"{error.message}"
        )
        for error in errors
    ]


def ensure_valid_m1_manifest(
    data: Any,
    *,
    schema_path: str | Path | None = None,
) -> None:
    errors = validate_m1_manifest(
        data,
        schema_path=schema_path,
    )

    if errors:
        raise ValueError(
            "M1 Manifest不符合Schema：\n"
            + "\n".join(errors)
        )