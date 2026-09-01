from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from am_model_generator.contracts import (
    M2ProviderError,
)

from .base import CreativeGenerationRequest


_PROVIDER_PROMPT_LIMITS = {
    "meshy": 600,
    "triposg_local": 1800,
}

_DEFAULT_PROMPT_LIMIT = 600

_MANDATORY_FDM_CONTRACT = (
    "FDM-printable single watertight solid; "
    "upright with feet or rump down on a continuous planar bottom; "
    "stable center of mass; "
    "avoid unsupported cantilever features and isolated appendages; "
    "robust overhang roots; "
    "finished object only, never temporary support pillars; "
    "accessible undersides for removable slicer supports; "
    "preserve identity and dimensions."
)

_DEFAULT_FDM_GUIDANCE = (
    "Prefer gradual overhang transitions.",
    "Avoid large near-horizontal downward-facing surfaces.",
    "Keep thin protrusions robust at the requested scale.",
    "Prefer geometry that grows continuously from lower layers.",
)


def _clean_required_string(
    value: Any,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise M2ProviderError(
            "M2_PROVIDER_FIELD_INVALID",
            f"{field_name}????????",
            details={
                "field": field_name,
                "actual_type": type(value).__name__,
            },
        )

    cleaned = value.strip()

    if not cleaned:
        raise M2ProviderError(
            "M2_PROVIDER_FIELD_EMPTY",
            f"{field_name}????",
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
    if value is None or isinstance(value, bool):
        return None

    try:
        height = float(value)
    except (TypeError, ValueError):
        return None

    if height <= 0:
        return None

    return height


def _normalize_whitespace(
    value: str,
) -> str:
    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def _truncate_at_word(
    value: str,
    maximum_length: int,
) -> str:
    cleaned = _normalize_whitespace(
        value
    )

    if len(cleaned) <= maximum_length:
        return cleaned

    if maximum_length <= 3:
        return cleaned[:maximum_length]

    candidate = cleaned[
        : maximum_length - 3
    ].rstrip()

    split_at = candidate.rfind(" ")

    if split_at >= max(
        20,
        int(maximum_length * 0.60),
    ):
        candidate = candidate[:split_at]

    return candidate.rstrip(
        " ,;:."
    ) + "..."


def _normalize_guidance_items(
    value: Any,
) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        values = [value]

    elif isinstance(value, (list, tuple)):
        values = list(value)

    else:
        return []

    result: list[str] = []
    seen: set[str] = set()

    for item in values:
        if not isinstance(item, str):
            continue

        cleaned = _normalize_whitespace(
            item
        )

        if not cleaned:
            continue

        key = cleaned.casefold()

        if key in seen:
            continue

        seen.add(key)
        result.append(cleaned)

    return result


def _repair_feedback_guidance(
    feedback: Any,
) -> list[str]:
    if not isinstance(feedback, dict):
        return []

    guidance: list[str] = []

    requested = (
        feedback.get(
            "requested_geometry_changes"
        )
    )

    guidance.extend(
        _normalize_guidance_items(
            requested
        )
    )

    status = str(
        feedback.get(
            "status",
            "",
        )
    ).strip().lower()

    if status in {
        "requires_geometry_regeneration",
        "needs_geometry_regeneration",
        "blocked",
    }:
        guidance.append(
            "Previous slicing failed validation; redesign unsafe or inaccessible overhangs without welding temporary support pillars into the finished object."
        )

    observations = feedback.get(
        "observations"
    )

    if isinstance(observations, dict):
        dangerous = observations.get(
            "dangerous_layer_count"
        )

        if isinstance(
            dangerous,
            (int, float),
        ) and not isinstance(
            dangerous,
            bool,
        ):
            guidance.append(
                "Reduce geometry that produced unsupported toolpath regions across multiple layers."
            )

    return _normalize_guidance_items(
        guidance
    )


def _provider_prompt_limit(
    provider_name: str,
) -> int:
    return int(
        _PROVIDER_PROMPT_LIMITS.get(
            provider_name.strip().lower(),
            _DEFAULT_PROMPT_LIMIT,
        )
    )


def _compose_generation_prompt(
    base_prompt: str,
    source_spec: dict[str, Any],
    manufacturability_feedback: dict[str, Any] | None = None,
    *,
    maximum_length: int,
) -> str:
    if maximum_length < 400:
        raise ValueError(
            "maximum_length is too small for mandatory FDM contract"
        )

    base = _normalize_whitespace(
        base_prompt
    )

    mandatory = (
        "FDM constraints: "
        + _MANDATORY_FDM_CONTRACT
    )

    # Mandatory contract is never truncated.
    reserved = len(mandatory) + 3

    base_budget = max(
        100,
        min(
            650,
            maximum_length - reserved,
        ),
    )

    base_part = _truncate_at_word(
        base,
        base_budget,
    )

    pieces = [
        base_part,
        mandatory,
    ]

    source_guidance = (
        _normalize_guidance_items(
            source_spec.get(
                "system_printability_guidance",
                [],
            )
        )
    )

    extra_guidance = list(
        _DEFAULT_FDM_GUIDANCE
    )

    extra_guidance.extend(
        source_guidance
    )

    extra_guidance.extend(
        _repair_feedback_guidance(
            manufacturability_feedback
        )
    )

    extra_guidance = (
        _normalize_guidance_items(
            extra_guidance
        )
    )

    for item in extra_guidance:
        candidate = (
            " ".join(pieces)
            + " "
            + item
        )

        if len(candidate) <= maximum_length:
            pieces.append(item)

    prompt = _normalize_whitespace(
        " ".join(pieces)
    )

    # Defensive assertion: never silently truncate mandatory text.
    if len(prompt) > maximum_length:
        raise M2ProviderError(
            "M2_PROVIDER_PROMPT_CONTRACT_OVERFLOW",
            "Provider Prompt????Provider????",
            details={
                "prompt_length": len(prompt),
                "maximum_length": maximum_length,
            },
        )

    mandatory_terms = (
        "fdm",
        "printable",
        "watertight",
        "continuous",
        "cantilever",
        "appendages",
        "overhang",
    )

    lowered = prompt.casefold()

    missing = [
        term
        for term in mandatory_terms
        if term not in lowered
    ]

    if missing:
        raise M2ProviderError(
            "M2_FDM_PROMPT_CONTRACT_INCOMPLETE",
            "FDM??????????Provider Prompt",
            details={
                "missing_terms": missing,
                "prompt": prompt,
            },
        )

    return prompt


def _feedback_fingerprint(
    feedback: dict[str, Any] | None,
) -> str | None:
    if not isinstance(feedback, dict):
        return None

    canonical = json.dumps(
        feedback,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _build_idempotency_key(
    *,
    request_id: str,
    provider_name: str,
    source_payload_sha256: str,
    prompt: str,
    negative_prompt: str | None,
    target_height_mm: float | None,
    manufacturability_feedback_sha256: str | None = None,
) -> str:
    canonical_data = {
        "request_id":
            request_id,

        "provider":
            provider_name,

        "source_payload_sha256":
            source_payload_sha256,

        "prompt":
            prompt,

        "negative_prompt":
            negative_prompt,

        "target_height_mm":
            target_height_mm,

        "manufacturability_feedback_sha256":
            manufacturability_feedback_sha256,
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
    manufacturability_feedback: dict[str, Any] | None = None,
) -> CreativeGenerationRequest:
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
            "??Provider????????creative_asset",
            details={
                "task_type":
                    m2_request.get(
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
            "??Provider????????creative_mesh",
            details={
                "route":
                    m2_request.get(
                        "route"
                    ),
            },
        )

    request_id = (
        _clean_required_string(
            m2_request.get(
                "request_id"
            ),
            field_name="request_id",
        )
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

    base_prompt = (
        _clean_required_string(
            source_spec.get(
                "generation_prompt_en"
            ),
            field_name=(
                "generation_prompt_en"
            ),
        )
    )

    prompt_limit = (
        _provider_prompt_limit(
            provider
        )
    )

    prompt = (
        _compose_generation_prompt(
            base_prompt,
            source_spec,
            manufacturability_feedback,
            maximum_length=prompt_limit,
        )
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

    feedback_sha256 = (
        _feedback_fingerprint(
            manufacturability_feedback
        )
    )

    metadata = {
        "original_input":
            m2_request.get(
                "original_input"
            ),

        "object_name":
            source_spec.get(
                "object_name"
            ),

        "visual_description":
            source_spec.get(
                "visual_description"
            ),

        "style":
            source_spec.get(
                "style"
            ),

        "pose":
            source_spec.get(
                "pose"
            ),

        "output_target":
            source_spec.get(
                "output_target"
            ),

        "system_printability_guidance":
            source_spec.get(
                "system_printability_guidance",
                [],
            ),

        "fdm_prompt_contract":
            True,

        "prompt_limit":
            prompt_limit,

        "manufacturability_feedback_present":
            isinstance(
                manufacturability_feedback,
                dict,
            ),

        "manufacturability_feedback_sha256":
            feedback_sha256,
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
            manufacturability_feedback_sha256=(
                feedback_sha256
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
