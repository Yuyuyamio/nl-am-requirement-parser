from __future__ import annotations

import hashlib
import json
import struct
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .contracts import (
    M2ProviderError,
    ensure_valid_m2_manifest,
)
from .providers.defaults import build_default_provider_registry
from .providers import (
    MockProvider,
    ProviderRegistry,
    ProviderSubmission,
    ensure_valid_provider_submission,
)


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]

ARTIFACT_RECEIPT_SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "m2_artifact_receipt.schema.json"
)

M2_MANIFEST_FILENAME = "m2_manifest.json"
PROVIDER_SUBMISSION_FILENAME = "provider_submission.json"
RAW_MODEL_FILENAME = "raw_model.glb"
ARTIFACT_RECEIPT_FILENAME = "artifact_receipt.json"


def _read_json_object(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2ProviderError(
            "M2_ARTIFACT_FILE_MISSING",
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
            "M2_ARTIFACT_INVALID_JSON",
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
            "M2_ARTIFACT_READ_FAILED",
            f"无法读取{label}",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2ProviderError(
            "M2_ARTIFACT_INVALID_OBJECT",
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
            "M2_ARTIFACT_WRITE_FAILED",
            "无法写入Artifact记录",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error


def _load_receipt_schema() -> dict[str, Any]:
    schema = json.loads(
        ARTIFACT_RECEIPT_SCHEMA_PATH.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(schema, dict):
        raise M2ProviderError(
            "M2_ARTIFACT_SCHEMA_INVALID",
            "Artifact Receipt Schema必须是JSON对象",
        )

    Draft202012Validator.check_schema(
        schema
    )

    return schema


def validate_artifact_receipt(
    data: Any,
) -> list[str]:
    if not isinstance(data, dict):
        return [
            "$: document must be a JSON object"
        ]

    validator = Draft202012Validator(
        _load_receipt_schema()
    )

    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    formatted_errors: list[str] = []

    for error in errors:
        path = "$"

        for part in error.absolute_path:
            if isinstance(part, int):
                path += f"[{part}]"
            else:
                path += f".{part}"

        formatted_errors.append(
            f"{path}: {error.message}"
        )

    return formatted_errors


def ensure_valid_artifact_receipt(
    data: Any,
) -> None:
    errors = validate_artifact_receipt(
        data
    )

    if errors:
        raise M2ProviderError(
            "M2_ARTIFACT_RECEIPT_INVALID",
            "Artifact Receipt不符合Schema",
            details={
                "errors": errors,
            },
        )


def _sha256_file(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            block = file.read(
                1024 * 1024
            )

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


def _validate_glb_file(
    path: Path,
) -> None:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise M2ProviderError(
            "M2_ARTIFACT_MODEL_READ_FAILED",
            "无法读取本地GLB文件",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if len(data) < 20:
        raise M2ProviderError(
            "M2_ARTIFACT_GLB_INVALID",
            "GLB文件过小或已损坏",
            details={
                "path": str(path),
                "size_bytes": len(data),
            },
        )

    magic, version, declared_length = struct.unpack(
        "<4sII",
        data[:12],
    )

    if magic != b"glTF":
        raise M2ProviderError(
            "M2_ARTIFACT_GLB_INVALID",
            "文件缺少GLB魔数",
            details={
                "path": str(path),
            },
        )

    if version != 2:
        raise M2ProviderError(
            "M2_ARTIFACT_GLB_INVALID",
            "当前只接受GLB 2.0",
            details={
                "version": version,
            },
        )

    if declared_length != len(data):
        raise M2ProviderError(
            "M2_ARTIFACT_GLB_LENGTH_MISMATCH",
            "GLB声明长度与实际长度不一致",
            details={
                "declared_length": declared_length,
                "actual_length": len(data),
            },
        )


def _select_glb_uri(
    submission: dict[str, Any],
) -> str:
    artifacts = submission.get(
        "artifacts"
    )

    if not isinstance(
        artifacts,
        list,
    ):
        raise M2ProviderError(
            "M2_ARTIFACT_LIST_INVALID",
            "Provider artifacts必须是数组",
        )

    for value in artifacts:
        if (
            isinstance(value, str)
            and value.strip()
            and value.lower().endswith(
                ".glb"
            )
        ):
            return value.strip()

    raise M2ProviderError(
        "M2_ARTIFACT_GLB_NOT_FOUND",
        "Provider未返回可用的GLB资源",
        details={
            "artifacts": artifacts,
        },
    )


def _updated_manifest(
    manifest: dict[str, Any],
    *,
    provider_name: str,
) -> dict[str, Any]:
    updated = dict(manifest)

    updated["status"] = "generated"
    updated["provider"] = provider_name
    updated["primary_model"] = RAW_MODEL_FILENAME
    updated["validation_file"] = None
    updated["hard_constraints_passed"] = None
    updated["next_module"] = None

    ensure_valid_m2_manifest(
        updated
    )

    return updated


def _result_data(
    *,
    task_path: Path,
    receipt: dict[str, Any],
    reused_existing_artifact: bool,
    manifest_changed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "generated",
        "artifact_status": "available",
        "request_id": receipt["request_id"],
        "provider": receipt["provider"],
        "provider_job_id": receipt[
            "provider_job_id"
        ],
        "source_uri": receipt["source_uri"],
        "primary_model": receipt["local_file"],
        "sha256": receipt["sha256"],
        "size_bytes": receipt["size_bytes"],
        "task_directory": str(task_path),
        "model_file": str(
            task_path
            / receipt["local_file"]
        ),
        "artifact_receipt_file": str(
            task_path
            / ARTIFACT_RECEIPT_FILENAME
        ),
        "reused_existing_artifact": (
            reused_existing_artifact
        ),
        "manifest_changed": manifest_changed,
        "network_called": bool(
            receipt.get(
                "provider_metadata",
                {},
            ).get(
                "network_called",
                False,
            )
        ),
    }


def acquire_m2_artifact(
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

    manifest_path = (
        task_path
        / M2_MANIFEST_FILENAME
    )

    submission_path = (
        task_path
        / PROVIDER_SUBMISSION_FILENAME
    )

    model_path = (
        task_path
        / RAW_MODEL_FILENAME
    )

    receipt_path = (
        task_path
        / ARTIFACT_RECEIPT_FILENAME
    )

    manifest = _read_json_object(
        manifest_path,
        label="M2 Manifest",
    )

    submission_data = _read_json_object(
        submission_path,
        label="Provider Submission",
    )

    ensure_valid_m2_manifest(
        manifest
    )

    ensure_valid_provider_submission(
        submission_data
    )

    if manifest.get("status") != "generated":
        raise M2ProviderError(
            "M2_ARTIFACT_MANIFEST_NOT_GENERATED",
            "只有generated任务可以取得Artifact",
            details={
                "status": manifest.get(
                    "status"
                ),
            },
        )

    if (
        submission_data.get("status")
        != "completed"
    ):
        raise M2ProviderError(
            "M2_ARTIFACT_PROVIDER_NOT_COMPLETED",
            "Provider任务尚未完成",
            details={
                "provider_status": (
                    submission_data.get(
                        "status"
                    )
                ),
            },
        )

    if (
        manifest.get("request_id")
        != submission_data.get(
            "request_id"
        )
    ):
        raise M2ProviderError(
            "M2_ARTIFACT_REQUEST_ID_MISMATCH",
            "Manifest与Provider Submission的request_id不一致",
        )

    if (
        manifest.get("provider")
        != normalized_provider
        or submission_data.get(
            "provider"
        )
        != normalized_provider
    ):
        raise M2ProviderError(
            "M2_ARTIFACT_PROVIDER_MISMATCH",
            "Artifact Provider不一致",
            details={
                "manifest_provider": (
                    manifest.get("provider")
                ),
                "submission_provider": (
                    submission_data.get(
                        "provider"
                    )
                ),
                "requested_provider": (
                    normalized_provider
                ),
            },
        )

    source_uri = _select_glb_uri(
        submission_data
    )

    model_exists = model_path.exists()
    receipt_exists = receipt_path.exists()

    if model_exists != receipt_exists:
        raise M2ProviderError(
            "M2_ARTIFACT_STATE_INCOMPLETE",
            "本地模型和Artifact Receipt状态不完整，已停止以避免覆盖",
            details={
                "model_exists": model_exists,
                "receipt_exists": receipt_exists,
            },
        )

    if model_exists and receipt_exists:
        receipt = _read_json_object(
            receipt_path,
            label="Artifact Receipt",
        )

        ensure_valid_artifact_receipt(
            receipt
        )

        if (
            receipt.get("request_id")
            != submission_data.get(
                "request_id"
            )
            or receipt.get("provider")
            != normalized_provider
            or receipt.get(
                "provider_job_id"
            )
            != submission_data.get(
                "provider_job_id"
            )
            or receipt.get("source_uri")
            != source_uri
            or receipt.get("local_file")
            != RAW_MODEL_FILENAME
        ):
            raise M2ProviderError(
                "M2_ARTIFACT_RECEIPT_CONFLICT",
                "已有Artifact Receipt与当前任务不一致",
            )

        _validate_glb_file(
            model_path
        )

        actual_sha256 = _sha256_file(
            model_path
        )

        actual_size = (
            model_path.stat().st_size
        )

        if (
            receipt.get("sha256")
            != actual_sha256
            or receipt.get("size_bytes")
            != actual_size
        ):
            raise M2ProviderError(
                "M2_ARTIFACT_FILE_CHANGED",
                "本地模型文件在落盘后发生了变化",
                details={
                    "expected_sha256": (
                        receipt.get("sha256")
                    ),
                    "actual_sha256": actual_sha256,
                    "expected_size": (
                        receipt.get(
                            "size_bytes"
                        )
                    ),
                    "actual_size": actual_size,
                },
            )

        updated_manifest = _updated_manifest(
            manifest,
            provider_name=(
                normalized_provider
            ),
        )

        manifest_changed = (
            updated_manifest != manifest
        )

        if manifest_changed:
            _write_json_atomic(
                manifest_path,
                updated_manifest,
            )

        return _result_data(
            task_path=task_path,
            receipt=receipt,
            reused_existing_artifact=True,
            manifest_changed=(
                manifest_changed
            ),
        )

    active_registry = (
        registry
        if registry is not None
        else build_default_provider_registry()
    )

    provider = active_registry.get(
        normalized_provider
    )

    submission = ProviderSubmission(
        **submission_data
    )

    temporary_model_path = (
        task_path
        / (
            f".{RAW_MODEL_FILENAME}."
            f"tmp-{uuid.uuid4().hex}"
        )
    )

    try:
        provider_metadata = (
            provider.download_artifact(
                source_uri,
                temporary_model_path,
            )
        )

        if not temporary_model_path.is_file():
            raise M2ProviderError(
                "M2_ARTIFACT_DOWNLOAD_EMPTY",
                "Provider没有生成本地Artifact文件",
            )

        _validate_glb_file(
            temporary_model_path
        )

        sha256 = _sha256_file(
            temporary_model_path
        )

        size_bytes = (
            temporary_model_path.stat().st_size
        )

        temporary_model_path.replace(
            model_path
        )

    except Exception:
        try:
            temporary_model_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise

    if not isinstance(
        provider_metadata,
        dict,
    ):
        provider_metadata = {}

    receipt = {
        "schema_version": "0.1.0",
        "module": "M2",
        "request_id": submission.request_id,
        "provider": submission.provider,
        "provider_job_id": (
            submission.provider_job_id
        ),
        "source_uri": source_uri,
        "local_file": RAW_MODEL_FILENAME,
        "media_type": (
            "model/gltf-binary"
        ),
        "sha256": sha256,
        "size_bytes": size_bytes,
        "status": "available",
        "provider_metadata": (
            provider_metadata
        ),
    }

    ensure_valid_artifact_receipt(
        receipt
    )

    _write_json_atomic(
        receipt_path,
        receipt,
    )

    updated_manifest = _updated_manifest(
        manifest,
        provider_name=(
            normalized_provider
        ),
    )

    manifest_changed = (
        updated_manifest != manifest
    )

    if manifest_changed:
        _write_json_atomic(
            manifest_path,
            updated_manifest,
        )

    return _result_data(
        task_path=task_path,
        receipt=receipt,
        reused_existing_artifact=False,
        manifest_changed=(
            manifest_changed
        ),
    )
