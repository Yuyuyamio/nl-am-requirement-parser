from __future__ import annotations

import hashlib
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
    build_creative_generation_request,
    ensure_valid_provider_request,
    ensure_valid_provider_submission,
)


M2_REQUEST_FILENAME = "m2_request.json"
M2_MANIFEST_FILENAME = "m2_manifest.json"
PROVIDER_REQUEST_FILENAME = (
    "provider_request.json"
)
PROVIDER_SUBMISSION_FILENAME = (
    "provider_submission.json"
)
MANUFACTURABILITY_FEEDBACK_FILENAME = (
    "manufacturability_feedback.json"
)


def _read_json_object(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_SUBMISSION_FILE_MISSING",
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
            "M2_SUBMISSION_INVALID_JSON",
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
            "M2_SUBMISSION_READ_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_SUBMISSION_INVALID_OBJECT",
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
            "M2_SUBMISSION_WRITE_FAILED",
            "无法写入Provider提交文件",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _canonical_payload_sha256(
    payload: dict[str, Any],
) -> str:
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()


def _resolve_source_spec(
    *,
    task_directory: Path,
    m2_request: dict[str, Any],
) -> Path:
    raw_path = m2_request.get(
        "source_spec"
    )

    if (
        not isinstance(raw_path, str)
        or not raw_path.strip()
    ):
        raise M2ProviderError(
            "M2_SOURCE_SPEC_PATH_INVALID",
            "M2 Request中的source_spec无效",
        )

    source_path = Path(
        raw_path
    ).expanduser()

    candidates: list[Path] = []

    if source_path.is_absolute():
        candidates.append(source_path)
    else:
        candidates.append(
            task_directory
            / source_path
        )

    manifest_path_value = m2_request.get(
        "source_m1_manifest"
    )

    if isinstance(
        manifest_path_value,
        str,
    ):
        manifest_path = Path(
            manifest_path_value
        ).expanduser()

        candidates.append(
            manifest_path.parent
            / source_path.name
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise M2ProviderError(
        "M2_SOURCE_SPEC_MISSING",
        "无法找到M1创意任务规格文件",
        details={
            "source_spec": raw_path,
            "checked_paths": [
                str(path)
                for path in candidates
            ],
        },
    )


def _verify_source_spec(
    *,
    source_spec: dict[str, Any],
    m2_request: dict[str, Any],
) -> None:
    actual_sha256 = (
        _canonical_payload_sha256(
            source_spec
        )
    )

    expected_sha256 = m2_request.get(
        "source_payload_sha256"
    )

    if actual_sha256 != expected_sha256:
        raise M2ProviderError(
            "M2_SOURCE_SPEC_CHANGED",
            "M1规格在M2规划完成后发生了变化，请重新生成M2规划",
            details={
                "expected_sha256": (
                    expected_sha256
                ),
                "actual_sha256": (
                    actual_sha256
                ),
            },
        )


def _validate_plan_state(
    *,
    m2_request: dict[str, Any],
    manifest: dict[str, Any],
    provider_name: str,
) -> None:
    if (
        m2_request.get("request_id")
        != manifest.get("request_id")
    ):
        raise M2ProviderError(
            "M2_PLAN_REQUEST_ID_MISMATCH",
            "M2 Request与Manifest的request_id不一致",
        )

    if (
        m2_request.get("task_type")
        != "creative_asset"
    ):
        raise M2ProviderError(
            "M2_SUBMISSION_TASK_UNSUPPORTED",
            "当前提交阶段只支持creative_asset",
            details={
                "task_type": m2_request.get(
                    "task_type"
                ),
            },
        )

    if (
        m2_request.get("route")
        != "creative_mesh"
    ):
        raise M2ProviderError(
            "M2_SUBMISSION_ROUTE_UNSUPPORTED",
            "当前提交阶段只支持creative_mesh",
            details={
                "route": m2_request.get(
                    "route"
                ),
            },
        )

    status = manifest.get("status")

    if status not in {
        "planned",
        "generating",
    }:
        raise M2ProviderError(
            "M2_SUBMISSION_STATUS_INVALID",
            "当前Manifest状态不允许提交Provider任务",
            details={
                "status": status,
            },
        )

    existing_provider = manifest.get(
        "provider"
    )

    if (
        existing_provider is not None
        and existing_provider
        != provider_name
    ):
        raise M2ProviderError(
            "M2_SUBMISSION_PROVIDER_CONFLICT",
            "Manifest中的Provider与本次Provider不一致",
            details={
                "manifest_provider": (
                    existing_provider
                ),
                "requested_provider": (
                    provider_name
                ),
            },
        )


def _updated_manifest(
    manifest: dict[str, Any],
    *,
    provider_name: str,
) -> dict[str, Any]:
    updated = dict(manifest)

    updated["status"] = "generating"
    updated["provider"] = provider_name
    updated["primary_model"] = None
    updated["validation_file"] = None
    updated[
        "hard_constraints_passed"
    ] = None
    updated["next_module"] = None

    ensure_valid_m2_manifest(
        updated
    )

    return updated


def _result_data(
    *,
    task_directory: Path,
    submission: dict[str, Any],
    reused_existing_submission: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "generating",
        "request_id": submission[
            "request_id"
        ],
        "provider": submission[
            "provider"
        ],
        "provider_job_id": submission[
            "provider_job_id"
        ],
        "task_directory": str(
            task_directory
        ),
        "provider_request_file": str(
            task_directory
            / PROVIDER_REQUEST_FILENAME
        ),
        "provider_submission_file": str(
            task_directory
            / PROVIDER_SUBMISSION_FILENAME
        ),
        "manifest_file": str(
            task_directory
            / M2_MANIFEST_FILENAME
        ),
        "reused_existing_submission": (
            reused_existing_submission
        ),
        "network_called": bool(
            submission.get(
                "provider_metadata",
                {},
            ).get(
                "network_called",
                False,
            )
        ),
    }


def submit_m2_plan(
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

    m2_request = _read_json_object(
        m2_request_path,
        label="M2 Request",
    )

    manifest = _read_json_object(
        manifest_path,
        label="M2 Manifest",
    )

    ensure_valid_m2_request(
        m2_request
    )

    ensure_valid_m2_manifest(
        manifest
    )

    _validate_plan_state(
        m2_request=m2_request,
        manifest=manifest,
        provider_name=(
            normalized_provider
        ),
    )

    source_spec_path = (
        _resolve_source_spec(
            task_directory=task_path,
            m2_request=m2_request,
        )
    )

    source_spec = _read_json_object(
        source_spec_path,
        label="M1 Source Spec",
    )

    _verify_source_spec(
        source_spec=source_spec,
        m2_request=m2_request,
    )

    feedback_path = (
        task_path
        / MANUFACTURABILITY_FEEDBACK_FILENAME
    )

    manufacturability_feedback = None

    if feedback_path.is_file():
        manufacturability_feedback = (
            _read_json_object(
                feedback_path,
                label=(
                    "Manufacturability Feedback"
                ),
            )
        )

    provider_request = (
        build_creative_generation_request(
            m2_request=m2_request,
            source_spec=source_spec,
            provider_name=(
                normalized_provider
            ),
            manufacturability_feedback=(
                manufacturability_feedback
            ),
        )
    )

    provider_request_data = (
        provider_request.to_dict()
    )

    ensure_valid_provider_request(
        provider_request_data
    )

    request_exists = (
        provider_request_path.exists()
    )

    submission_exists = (
        provider_submission_path.exists()
    )

    if request_exists != submission_exists:
        # Only providers with an explicit local-only recovery capability may
        # finish cached postprocessing. Never resubmit an unknown remote job.
        if request_exists and not submission_exists:
            recovery_registry = registry if registry is not None else build_default_provider_registry()
            recover = getattr(recovery_registry.get(normalized_provider), "recover_local_submission", None)
            if callable(recover):
                existing_request = _read_json_object(provider_request_path, label="Existing Provider Request")
                ensure_valid_provider_request(existing_request)
                if existing_request != provider_request_data:
                    raise M2ProviderError("M2_PROVIDER_REQUEST_CONFLICT", "已有Provider Request与当前请求不一致")
                recovered = recover(provider_request)
                if recovered is not None:
                    recovered_data = recovered.to_dict()
                    ensure_valid_provider_submission(recovered_data)
                    if (recovered.request_id != provider_request.request_id
                            or recovered.provider != normalized_provider
                            or recovered.idempotency_key != provider_request.idempotency_key
                            or recovered.status != "completed"):
                        raise M2ProviderError("M2_PROVIDER_RESPONSE_MISMATCH", "本地恢复结果与原请求不一致")
                    _write_json_atomic(provider_submission_path, recovered_data)
                    _write_json_atomic(manifest_path, _updated_manifest(manifest, provider_name=normalized_provider))
                    return _result_data(task_directory=task_path, submission=recovered_data,
                                        reused_existing_submission=True)
        raise M2ProviderError(
            "M2_SUBMISSION_STATE_INCOMPLETE",
            "Provider提交状态不完整，为避免重复提交已停止",
            details={
                "provider_request_exists": (
                    request_exists
                ),
                "provider_submission_exists": (
                    submission_exists
                ),
                "task_directory": str(
                    task_path
                ),
            },
        )

    if (
        request_exists
        and submission_exists
    ):
        existing_request = (
            _read_json_object(
                provider_request_path,
                label=(
                    "Existing Provider Request"
                ),
            )
        )

        existing_submission = (
            _read_json_object(
                provider_submission_path,
                label=(
                    "Existing Provider Submission"
                ),
            )
        )

        ensure_valid_provider_request(
            existing_request
        )

        ensure_valid_provider_submission(
            existing_submission
        )

        if (
            existing_request
            != provider_request_data
        ):
            raise M2ProviderError(
                "M2_PROVIDER_REQUEST_CONFLICT",
                "已有Provider Request与当前请求不一致",
                details={
                    "path": str(
                        provider_request_path
                    ),
                },
            )

        if (
            existing_submission.get(
                "request_id"
            )
            != provider_request.request_id
            or existing_submission.get(
                "provider"
            )
            != normalized_provider
            or existing_submission.get(
                "idempotency_key"
            )
            != provider_request.idempotency_key
        ):
            raise M2ProviderError(
                "M2_PROVIDER_SUBMISSION_CONFLICT",
                "已有Provider Submission与当前请求不一致",
            )

        updated_manifest = (
            _updated_manifest(
                manifest,
                provider_name=(
                    normalized_provider
                ),
            )
        )

        if updated_manifest != manifest:
            _write_json_atomic(
                manifest_path,
                updated_manifest,
            )

        return _result_data(
            task_directory=task_path,
            submission=(
                existing_submission
            ),
            reused_existing_submission=True,
        )

    active_registry = (
        registry
        if registry is not None
        else build_default_provider_registry()
    )

    provider = active_registry.get(
        normalized_provider
    )

    provider.preflight_submit(
        provider_request
    )

    # 先持久化请求。
    # 如果进程在Provider返回前中断，
    # 下次会因状态不完整而停止，
    # 避免盲目重复提交。
    _write_json_atomic(
        provider_request_path,
        provider_request_data,
    )

    submission = provider.submit(
        provider_request
    )

    submission_data = (
        submission.to_dict()
    )

    ensure_valid_provider_submission(
        submission_data
    )

    if (
        submission.request_id
        != provider_request.request_id
        or submission.provider
        != normalized_provider
        or submission.idempotency_key
        != provider_request.idempotency_key
    ):
        raise M2ProviderError(
            "M2_PROVIDER_RESPONSE_MISMATCH",
            "Provider返回结果与提交请求不一致",
        )

    _write_json_atomic(
        provider_submission_path,
        submission_data,
    )

    updated_manifest = (
        _updated_manifest(
            manifest,
            provider_name=(
                normalized_provider
            ),
        )
    )

    _write_json_atomic(
        manifest_path,
        updated_manifest,
    )

    return _result_data(
        task_directory=task_path,
        submission=submission_data,
        reused_existing_submission=False,
    )
