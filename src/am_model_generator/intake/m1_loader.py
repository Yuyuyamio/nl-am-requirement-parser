from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from am_requirement_parser.creative.schema import (
    CREATIVE_ASSET_SCHEMA,
)
from am_requirement_parser.m1_contract import (
    validate_m1_manifest,
)

from ..contracts import (
    LoadedM1Input,
    M1TaskType,
    M2InputError,
)


PROJECT_ROOT = (
    Path(__file__).resolve().parents[3]
)

ENGINEERING_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "requirement_spec.schema.json"
)

READY_STATUSES = {
    "ready",
    "complete",
}

SUPPORTED_TASK_TYPES = {
    "creative_asset",
    "engineering_part",
}


def _format_error_path(
    error: Any,
) -> str:
    """
    将 jsonschema 错误路径格式化为：

    $.geometry.design_domain
    $.materials[0].grade
    """

    if not error.absolute_path:
        return "$"

    path = "$"

    for part in error.absolute_path:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"

    return path


def _read_json_object(
    path: Path,
    *,
    label: str,
    missing_code: str,
    invalid_json_code: str,
    invalid_object_code: str,
) -> dict[str, Any]:
    """
    从文件中读取一个 JSON 对象。
    """

    if not path.is_file():
        raise M2InputError(
            missing_code,
            f"{label}不存在",
            details={
                "path": str(path),
            },
        )

    try:
        raw_text = path.read_text(
            encoding="utf-8-sig"
        )
    except OSError as error:
        raise M2InputError(
            invalid_json_code,
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise M2InputError(
            invalid_json_code,
            f"{label}不是合法JSON",
            details={
                "path": str(path),
                "line": error.lineno,
                "column": error.colno,
                "reason": error.msg,
            },
        ) from error

    if not isinstance(data, dict):
        raise M2InputError(
            invalid_object_code,
            f"{label}必须是JSON对象",
            details={
                "path": str(path),
                "actual_type": type(data).__name__,
            },
        )

    return data


def _resolve_referenced_path(
    manifest_path: Path,
    raw_reference: str,
) -> Path:
    """
    解析 Manifest 中记录的文件路径。

    当前 M1 通常写入绝对路径。
    本函数同时兼容：
    1. 仍然有效的绝对路径；
    2. 相对于 Manifest 的相对路径；
    3. 项目移动后失效的旧绝对路径。
    """

    referenced = Path(
        raw_reference
    ).expanduser()

    if referenced.is_absolute():
        if referenced.is_file():
            return referenced.resolve()

        fallback = (
            manifest_path.parent
            / referenced.name
        )

        return fallback.resolve()

    return (
        manifest_path.parent
        / referenced
    ).resolve()


def _validate_against_schema(
    data: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
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


def _load_engineering_schema() -> dict[str, Any]:
    if not ENGINEERING_SCHEMA_PATH.is_file():
        raise M2InputError(
            "M2_INTERNAL_SCHEMA_MISSING",
            "工程件规格Schema不存在",
            details={
                "path": str(
                    ENGINEERING_SCHEMA_PATH
                ),
            },
        )

    try:
        data = json.loads(
            ENGINEERING_SCHEMA_PATH.read_text(
                encoding="utf-8-sig"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as error:
        raise M2InputError(
            "M2_INTERNAL_SCHEMA_INVALID",
            "工程件规格Schema无法读取",
            details={
                "path": str(
                    ENGINEERING_SCHEMA_PATH
                ),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2InputError(
            "M2_INTERNAL_SCHEMA_INVALID",
            "工程件规格Schema必须是JSON对象",
            details={
                "path": str(
                    ENGINEERING_SCHEMA_PATH
                ),
            },
        )

    return data


def _validate_creative_payload(
    payload: dict[str, Any],
) -> None:
    payload_task_type = payload.get(
        "task_type"
    )

    if payload_task_type != "creative_asset":
        raise M2InputError(
            "M2_PAYLOAD_TASK_MISMATCH",
            "创意模型规格的任务类型与Manifest不一致",
            details={
                "expected": "creative_asset",
                "actual": payload_task_type,
            },
        )

    errors = _validate_against_schema(
        payload,
        CREATIVE_ASSET_SCHEMA,
    )

    if errors:
        raise M2InputError(
            "M2_PAYLOAD_SCHEMA_INVALID",
            "创意模型规格不符合Schema",
            details={
                "errors": errors,
            },
        )

    if payload.get(
        "needs_clarification"
    ) is not False:
        raise M2InputError(
            "M2_PAYLOAD_NOT_READY",
            "创意模型规格仍需要澄清",
            details={
                "needs_clarification": (
                    payload.get(
                        "needs_clarification"
                    )
                ),
                "clarification_question": (
                    payload.get(
                        "clarification_question"
                    )
                ),
            },
        )


def _validate_engineering_payload(
    payload: dict[str, Any],
) -> None:
    schema = _load_engineering_schema()

    errors = _validate_against_schema(
        payload,
        schema,
    )

    if errors:
        raise M2InputError(
            "M2_PAYLOAD_SCHEMA_INVALID",
            "工程件规格不符合Schema",
            details={
                "errors": errors,
            },
        )

    if payload.get("status") != "complete":
        raise M2InputError(
            "M2_PAYLOAD_NOT_READY",
            "工程件规格尚未完成",
            details={
                "source_status": payload.get(
                    "status"
                ),
            },
        )


def _validate_payload(
    task_type: M1TaskType,
    payload: dict[str, Any],
) -> None:
    if task_type == "creative_asset":
        _validate_creative_payload(
            payload
        )
        return

    if task_type == "engineering_part":
        _validate_engineering_payload(
            payload
        )
        return

    raise M2InputError(
        "M2_UNSUPPORTED_TASK_TYPE",
        "M2不支持当前任务类型",
        details={
            "task_type": task_type,
        },
    )


def load_m1_input(
    manifest_path: str | Path,
) -> LoadedM1Input:
    """
    读取并验证 M1 向 M2 交付的输入。

    只有以下输入可以通过：
    - status 为 ready 或 complete；
    - next_module 为 M2；
    - task_type 为 creative_asset
      或 engineering_part；
    - output_file 存在；
    - payload 符合对应 Schema；
    - payload 已达到可进入 M2 的状态。
    """

    resolved_manifest_path = Path(
        manifest_path
    ).expanduser().resolve()

    manifest = _read_json_object(
        resolved_manifest_path,
        label="M1 Manifest",
        missing_code=(
            "M2_MANIFEST_NOT_FOUND"
        ),
        invalid_json_code=(
            "M2_MANIFEST_INVALID_JSON"
        ),
        invalid_object_code=(
            "M2_MANIFEST_INVALID_OBJECT"
        ),
    )

    manifest_errors = (
        validate_m1_manifest(
            manifest
        )
    )

    if manifest_errors:
        raise M2InputError(
            "M2_MANIFEST_SCHEMA_INVALID",
            "M1 Manifest不符合Schema",
            details={
                "errors": manifest_errors,
            },
        )

    source_status = manifest.get(
        "status"
    )

    if source_status not in READY_STATUSES:
        raise M2InputError(
            "M2_INPUT_NOT_READY",
            "M1规格尚未达到进入M2的条件",
            details={
                "source_status": source_status,
            },
        )

    next_module = manifest.get(
        "next_module"
    )

    if next_module != "M2":
        raise M2InputError(
            "M2_NEXT_MODULE_MISMATCH",
            "M1 Manifest未将任务交付给M2",
            details={
                "next_module": next_module,
            },
        )

    raw_task_type = manifest.get(
        "task_type"
    )

    if (
        raw_task_type
        not in SUPPORTED_TASK_TYPES
    ):
        raise M2InputError(
            "M2_UNSUPPORTED_TASK_TYPE",
            "M2不支持当前任务类型",
            details={
                "task_type": raw_task_type,
            },
        )

    task_type: M1TaskType = (
        raw_task_type
    )

    raw_output_file = manifest.get(
        "output_file"
    )

    if not isinstance(
        raw_output_file,
        str,
    ) or not raw_output_file.strip():
        raise M2InputError(
            "M2_OUTPUT_REFERENCE_INVALID",
            "M1 Manifest中的output_file无效",
            details={
                "output_file": raw_output_file,
            },
        )

    payload_path = (
        _resolve_referenced_path(
            resolved_manifest_path,
            raw_output_file,
        )
    )

    payload = _read_json_object(
        payload_path,
        label="M1正式规格文件",
        missing_code=(
            "M2_PAYLOAD_NOT_FOUND"
        ),
        invalid_json_code=(
            "M2_PAYLOAD_INVALID_JSON"
        ),
        invalid_object_code=(
            "M2_PAYLOAD_INVALID_OBJECT"
        ),
    )

    _validate_payload(
        task_type,
        payload,
    )

    return LoadedM1Input(
        manifest_path=(
            resolved_manifest_path
        ),
        manifest=manifest,
        payload_path=payload_path,
        payload=payload,
        task_type=task_type,
    )