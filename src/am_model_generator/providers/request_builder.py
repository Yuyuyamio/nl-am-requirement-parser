from __future__ import annotations

import hashlib
import json
from typing import Any

from am_model_generator.contracts import (
    M2ProviderError,
)

from .base import CreativeGenerationRequest


def _clean_required_string(
    value: Any,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise M2ProviderError(
            "M2_PROVIDER_FIELD_INVALID",
            f"{field_name}必须是非空字符串",
            details={
                "field": field_name,
                "actual_type": (
                    type(value).__name__
                ),
            },
        )

    cleaned = value.strip()

    if not cleaned:
        raise M2ProviderError(
            "M2_PROVIDER_FIELD_EMPTY",
            f"{field_name}不能为空",
            details={
                "field": field_name,
            },
        )

    return cleaned


def _clean_optional_string(
    value: Any,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    cleaned = value.strip()

    return cleaned or None


def _normalize_target_height(
    value: Any,
) -> float | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:
        height = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None

    if height <= 0:
        return None

    return height


def _build_idempotency_key(
    *,
    request_id: str,
    provider_name: str,
    source_payload_sha256: str,
    prompt: str,
    negative_prompt: str | None,
    target_height_mm: float | None,
) -> str:
    """
    根据真正会影响Provider请求的内容构造稳定键。

    同一个任务和同一套生成参数会得到同一个键；
    修改Provider、Prompt或尺寸后会得到新键。
    """

    canonical_data = {
        "request_id": request_id,
        "provider": provider_name,
        "source_payload_sha256": (
            source_payload_sha256
        ),
        "prompt": prompt,
        "negative_prompt": (
            negative_prompt
        ),
        "target_height_mm": (
            target_height_mm
        ),
    }

    canonical_json = json.dumps(
        canonical_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    digest = hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()

    return (
        "M2SUB-"
        + digest[:20].upper()
    )


def build_creative_generation_request(
    *,
    m2_request: dict[str, Any],
    source_spec: dict[str, Any],
    provider_name: str,
) -> CreativeGenerationRequest:
    """
    将M2 Request和M1创意规格转换成统一Provider请求。

    style和pose属于可选增强信息；
    即使为null也不得阻止请求构造。
    """

    provider = _clean_required_string(
        provider_name,
        field_name="provider_name",
    ).lower()

    if (
        m2_request.get("task_type")
        != "creative_asset"
    ):
        raise M2ProviderError(
            "M2_PROVIDER_TASK_UNSUPPORTED",
            "当前Provider请求构造器只支持creative_asset",
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
            "M2_PROVIDER_ROUTE_UNSUPPORTED",
            "当前Provider请求构造器只支持creative_mesh",
            details={
                "route": m2_request.get(
                    "route"
                ),
            },
        )

    request_id = _clean_required_string(
        m2_request.get("request_id"),
        field_name="request_id",
    )

    source_payload_sha256 = (
        _clean_required_string(
            m2_request.get(
                "source_payload_sha256"
            ),
            field_name=(
                "source_payload_sha256"
            ),
        )
    )

    prompt = _clean_required_string(
        source_spec.get(
            "generation_prompt_en"
        ),
        field_name=(
            "generation_prompt_en"
        ),
    )

    negative_prompt = (
        _clean_optional_string(
            source_spec.get(
                "negative_prompt_en"
            )
        )
    )

    target_height_mm = (
        _normalize_target_height(
            source_spec.get(
                "target_height_mm"
            )
        )
    )

    metadata = {
        "original_input": (
            m2_request.get(
                "original_input"
            )
        ),
        "object_name": source_spec.get(
            "object_name"
        ),
        "visual_description": (
            source_spec.get(
                "visual_description"
            )
        ),
        "style": source_spec.get(
            "style"
        ),
        "pose": source_spec.get(
            "pose"
        ),
        "output_target": (
            source_spec.get(
                "output_target"
            )
        ),
        "system_printability_guidance": (
            source_spec.get(
                "system_printability_guidance",
                [],
            )
        ),
    }

    idempotency_key = (
        _build_idempotency_key(
            request_id=request_id,
            provider_name=provider,
            source_payload_sha256=(
                source_payload_sha256
            ),
            prompt=prompt,
            negative_prompt=(
                negative_prompt
            ),
            target_height_mm=(
                target_height_mm
            ),
        )
    )

    return CreativeGenerationRequest(
        schema_version="0.1.0",
        request_id=request_id,
        route="creative_mesh",
        provider=provider,
        idempotency_key=(
            idempotency_key
        ),
        source_payload_sha256=(
            source_payload_sha256
        ),
        prompt=prompt,
        negative_prompt=(
            negative_prompt
        ),
        target_height_mm=(
            target_height_mm
        ),
        metadata=metadata,
    )