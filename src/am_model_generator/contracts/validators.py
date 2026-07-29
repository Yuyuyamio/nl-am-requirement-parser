from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(
    __file__
).resolve().parents[3]

M2_REQUEST_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_request.schema.json"
)

M2_ROUTE_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_route.schema.json"
)

M2_MANIFEST_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_manifest.schema.json"
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


def _load_schema(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(
            f"{label}不存在：{path}"
        )

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{label}不是合法JSON：{path}"
        ) from error
    except OSError as error:
        raise ValueError(
            f"无法读取{label}：{path}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError(
            f"{label}必须是JSON对象"
        )

    try:
        Draft202012Validator.check_schema(
            data
        )
    except Exception as error:
        raise ValueError(
            f"{label}自身不合法：{error}"
        ) from error

    return data


def _validate(
    data: Any,
    *,
    schema_path: Path,
    label: str,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            f"$: {label}必须是JSON对象"
        ]

    schema = _load_schema(
        schema_path,
        label=f"{label} Schema",
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


def _ensure_valid(
    data: Any,
    *,
    schema_path: Path,
    label: str,
) -> None:
    errors = _validate(
        data,
        schema_path=schema_path,
        label=label,
    )

    if errors:
        raise ValueError(
            f"{label}不符合Schema：\n"
            + "\n".join(errors)
        )


def validate_m2_request(
    data: Any,
) -> list[str]:
    return _validate(
        data,
        schema_path=(
            M2_REQUEST_SCHEMA_PATH
        ),
        label="M2 Request",
    )


def ensure_valid_m2_request(
    data: Any,
) -> None:
    _ensure_valid(
        data,
        schema_path=(
            M2_REQUEST_SCHEMA_PATH
        ),
        label="M2 Request",
    )


def validate_m2_route(
    data: Any,
) -> list[str]:
    return _validate(
        data,
        schema_path=(
            M2_ROUTE_SCHEMA_PATH
        ),
        label="M2 Route",
    )


def ensure_valid_m2_route(
    data: Any,
) -> None:
    _ensure_valid(
        data,
        schema_path=(
            M2_ROUTE_SCHEMA_PATH
        ),
        label="M2 Route",
    )


def validate_m2_manifest(
    data: Any,
) -> list[str]:
    return _validate(
        data,
        schema_path=(
            M2_MANIFEST_SCHEMA_PATH
        ),
        label="M2 Manifest",
    )


def ensure_valid_m2_manifest(
    data: Any,
) -> None:
    _ensure_valid(
        data,
        schema_path=(
            M2_MANIFEST_SCHEMA_PATH
        ),
        label="M2 Manifest",
    )