from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .contracts import (
    M2ProviderError,
    ensure_valid_m2_manifest,
    ensure_valid_m2_request,
)
from .providers.defaults import build_default_provider_registry
from .providers import (
    MockProvider,
    ProviderRegistry,
    ProviderSubmission,
    ensure_valid_provider_request,
    ensure_valid_provider_submission,
)


M2_REQUEST_FILENAME = "m2_request.json"
M2_MANIFEST_FILENAME = "m2_manifest.json"
PROVIDER_REQUEST_FILENAME = "provider_request.json"
PROVIDER_SUBMISSION_FILENAME = "provider_submission.json"


def _read_json_object(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_STATUS_FILE_MISSING",
            f"{label}不存在",
            details={
                "path": str(path),
            },
        )

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except json.JSONDecodeError as error:
        raise M2ProviderError(
            "M2_STATUS_INVALID_JSON",
            f"{label}不是合法JSON",
            details={
                "path": str(path),
                "line": error.lineno,
                "column": error.colno,
                "reason": error.msg,
            },
        ) from error
    except OSError as error:
        raise M2ProviderError(
            "M2_STATUS_READ_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_STATUS_INVALID_OBJECT",
            f"{label}必须是JSON对象",
            details={
                "path": str(path),
            },
        )

    return data


def _write_json_atomic(
    path: Path,
    data: dict[str, Any],
) -> None:
    temporary_path = path.with_name(
        f".{path.name}.tmp-{uuid.uuid4().hex}"
    )

    try:
        temporary_path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        temporary_path.replace(path)

    except OSError as error:
        try:
            temporary_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise M2ProviderError(
            "M2_STATUS_WRITE_FAILED",
            "无法写入Provider状态文件",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _validate_cross_document_state(
    *,
    m2_request: dict[str, Any],
    manifest: dict[str, Any],
    provider_request: dict[str, Any],
    provider_submission: dict[str, Any],
    provider_name: str,
) -> None:
    request_ids = {
        m2_request.get("request_id"),
        manifest.get("request_id"),
        provider_request.get("request_id"),
        provider_submission.get("request_id"),
    }

    if len(request_ids) != 1:
        raise M2ProviderError(
            "M2_STATUS_REQUEST_ID_MISMATCH",
            "M2状态文件中的request_id不一致",
            details={
                "request_ids": sorted(
                    str(value)
                    for value in request_ids
                ),
            },
        )

    if manifest.get("provider") != provider_name:
        raise M2ProviderError(
            "M2_STATUS_PROVIDER_MISMATCH",
            "Manifest中的Provider与状态查询Provider不一致",
            details={
                "manifest_provider": (
                    manifest.get("provider")
                ),
                "requested_provider": (
                    provider_name
                ),
            },
        )

    if (
        provider_request.get("provider")
        != provider_name
        or provider_submission.get("provider")
        != provider_name
    ):
        raise M2ProviderError(
            "M2_STATUS_PROVIDER_MISMATCH",
            "Provider Request或Submission中的Provider不一致",
        )

    if (
        provider_request.get("idempotency_key")
        != provider_submission.get("idempotency_key")
    ):
        raise M2ProviderError(
            "M2_STATUS_IDEMPOTENCY_MISMATCH",
            "Provider Request与Submission的幂等键不一致",
        )

    if (
        provider_request.get("source_payload_sha256")
        != m2_request.get("source_payload_sha256")
    ):
        raise M2ProviderError(
            "M2_STATUS_SOURCE_HASH_MISMATCH",
            "Provider Request与M2 Request的源规格哈希不一致",
        )

    manifest_status = manifest.get("status")

    if manifest_status not in {
        "generating",
        "generated",
    }:
        raise M2ProviderError(
            "M2_STATUS_MANIFEST_STATE_INVALID",
            "当前Manifest状态不允许查询Provider状态",
            details={
                "status": manifest_status,
            },
        )

    if (
        manifest_status == "generated"
        and provider_submission.get("status")
        != "completed"
    ):
        raise M2ProviderError(
            "M2_STATUS_STATE_CONFLICT",
            "Manifest已经generated，但Provider任务尚未completed",
        )


def _updated_manifest(
    manifest: dict[str, Any],
    *,
    provider_name: str,
    provider_status: str,
) -> dict[str, Any]:
    updated = dict(manifest)

    updated["provider"] = provider_name
    updated["primary_model"] = None
    updated["validation_file"] = None
    updated["hard_constraints_passed"] = None
    updated["next_module"] = None

    if provider_status == "submitted":
        updated["status"] = "generating"

    elif provider_status == "completed":
        updated["status"] = "generated"

    elif provider_status == "failed":
        updated["status"] = "failed"

    else:
        raise M2ProviderError(
            "M2_PROVIDER_STATUS_INVALID",
            "Provider返回了无法识别的状态",
            details={
                "provider_status": (
                    provider_status
                ),
            },
        )

    ensure_valid_m2_manifest(
        updated
    )

    return updated


def poll_m2_task(
    task_directory: str | Path,
    *,
    provider_name: str = "mock",
    registry: ProviderRegistry | None = None,
) -> dict[str, Any]:
    task_path = Path(
        task_directory
    ).expanduser().resolve()

    if not task_path.is_dir():
        raise M2ProviderError(
            "M2_TASK_DIRECTORY_MISSING",
            "M2任务目录不存在",
            details={
                "path": str(task_path),
            },
        )

    normalized_provider = (
        provider_name.strip().lower()
    )

    if not normalized_provider:
        raise M2ProviderError(
            "M2_PROVIDER_NAME_EMPTY",
            "Provider名称不能为空",
        )

    m2_request_path = (
        task_path
        / M2_REQUEST_FILENAME
    )

    manifest_path = (
        task_path
        / M2_MANIFEST_FILENAME
    )

    provider_request_path = (
        task_path
        / PROVIDER_REQUEST_FILENAME
    )

    provider_submission_path = (
        task_path
        / PROVIDER_SUBMISSION_FILENAME
    )

    m2_request_data = _read_json_object(
        m2_request_path,
        label="M2 Request",
    )

    manifest_data = _read_json_object(
        manifest_path,
        label="M2 Manifest",
    )

    provider_request_data = _read_json_object(
        provider_request_path,
        label="Provider Request",
    )

    provider_submission_data = _read_json_object(
        provider_submission_path,
        label="Provider Submission",
    )

    ensure_valid_m2_request(
        m2_request_data
    )

    ensure_valid_m2_manifest(
        manifest_data
    )

    ensure_valid_provider_request(
        provider_request_data
    )

    ensure_valid_provider_submission(
        provider_submission_data
    )

    _validate_cross_document_state(
        m2_request=m2_request_data,
        manifest=manifest_data,
        provider_request=(
            provider_request_data
        ),
        provider_submission=(
            provider_submission_data
        ),
        provider_name=(
            normalized_provider
        ),
    )

    existing_submission = ProviderSubmission(
        **provider_submission_data
    )

    active_registry = (
        registry
        if registry is not None
        else build_default_provider_registry()
    )

    provider = active_registry.get(
        normalized_provider
    )

    updated_submission = provider.get_status(
        existing_submission
    )

    updated_submission_data = (
        updated_submission.to_dict()
    )

    ensure_valid_provider_submission(
        updated_submission_data
    )

    if (
        updated_submission.request_id
        != provider_request_data.get("request_id")
        or updated_submission.provider
        != normalized_provider
        or updated_submission.idempotency_key
        != provider_request_data.get("idempotency_key")
        or updated_submission.provider_job_id
        != existing_submission.provider_job_id
    ):
        raise M2ProviderError(
            "M2_PROVIDER_STATUS_RESPONSE_MISMATCH",
            "Provider状态查询结果与原提交记录不一致",
        )

    updated_manifest_data = _updated_manifest(
        manifest_data,
        provider_name=(
            normalized_provider
        ),
        provider_status=(
            updated_submission.status
        ),
    )

    submission_changed = (
        updated_submission_data
        != provider_submission_data
    )

    manifest_changed = (
        updated_manifest_data
        != manifest_data
    )

    if submission_changed:
        _write_json_atomic(
            provider_submission_path,
            updated_submission_data,
        )

    if manifest_changed:
        _write_json_atomic(
            manifest_path,
            updated_manifest_data,
        )

    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": (
            updated_manifest_data["status"]
        ),
        "request_id": (
            updated_submission.request_id
        ),
        "provider": (
            updated_submission.provider
        ),
        "provider_job_id": (
            updated_submission.provider_job_id
        ),
        "provider_status": (
            updated_submission.status
        ),
        "artifacts": list(
            updated_submission.artifacts
        ),
        "task_directory": str(
            task_path
        ),
        "manifest_file": str(
            manifest_path
        ),
        "provider_submission_file": str(
            provider_submission_path
        ),
        "status_changed": (
            submission_changed
            or manifest_changed
        ),
        "network_called": bool(
            updated_submission.provider_metadata.get(
                "network_called",
                False,
            )
        ),
    }
