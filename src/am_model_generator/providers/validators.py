from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(
    __file__
).resolve().parents[3]

PROVIDER_REQUEST_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_provider_request.schema.json"
)

PROVIDER_SUBMISSION_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_provider_submission.schema.json"
)


def _load_schema(
    path: Path,
) -> dict[str, Any]:
    data = json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(data, dict):
        raise ValueError(
            f"Schema必须是JSON对象：{path}"
        )

    Draft202012Validator.check_schema(
        data
    )

    return data


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


def _validate(
    data: Any,
    *,
    schema_path: Path,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: document must be a JSON object"
        ]

    schema = _load_schema(
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


def validate_provider_request(
    data: Any,
) -> list[str]:
    return _validate(
        data,
        schema_path=(
            PROVIDER_REQUEST_SCHEMA_PATH
        ),
    )


def validate_provider_submission(
    data: Any,
) -> list[str]:
    return _validate(
        data,
        schema_path=(
            PROVIDER_SUBMISSION_SCHEMA_PATH
        ),
    )


def ensure_valid_provider_request(
    data: Any,
) -> None:
    errors = validate_provider_request(
        data
    )

    if errors:
        raise ValueError(
            "Provider Request不符合Schema：\n"
            + "\n".join(errors)
        )


def ensure_valid_provider_submission(
    data: Any,
) -> None:
    errors = validate_provider_submission(
        data
    )

    if errors:
        raise ValueError(
            "Provider Submission不符合Schema：\n"
            + "\n".join(errors)
        )