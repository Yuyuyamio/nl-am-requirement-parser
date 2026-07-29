from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import M2InputError
from .models import (
    LoadedM1Input,
    M2Manifest,
    M2Request,
    M2Route,
)


def _payload_sha256(
    payload: dict[str, Any],
) -> str:
    """
    根据规范化JSON内容生成稳定SHA-256。

    字段顺序不同但内容相同的payload，
    会得到相同的哈希值。
    """

    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()


def _check_route_matches_input(
    loaded_input: LoadedM1Input,
    route: M2Route,
) -> None:
    if route.task_type != loaded_input.task_type:
        raise M2InputError(
            "M2_ROUTE_INPUT_MISMATCH",
            "M2路由与M1任务类型不一致",
            details={
                "input_task_type": loaded_input.task_type,
                "route_task_type": route.task_type,
            },
        )

    if not route.ready_for_generation:
        raise M2InputError(
            "M2_ROUTE_NOT_READY",
            "M2路由尚未达到生成条件",
            details={
                "request_id": route.request_id,
            },
        )


def build_m2_request(
    loaded_input: LoadedM1Input,
    route: M2Route,
) -> M2Request:
    """
    根据M1输入和M2路线构造标准化生成请求。
    """

    _check_route_matches_input(
        loaded_input,
        route,
    )

    return M2Request(
        schema_version="0.1.0",
        module="M2",
        request_id=route.request_id,
        task_type=route.task_type,
        source_m1_manifest=str(
            loaded_input.manifest_path
        ),
        source_spec=str(
            loaded_input.payload_path
        ),
        source_payload_sha256=(
            _payload_sha256(
                loaded_input.payload
            )
        ),
        original_input=(
            loaded_input.original_input
        ),
        route=route.route,
        generator_family=(
            route.generator_family
        ),
        preferred_provider=(
            route.preferred_provider
        ),
        generation_status="not_started",
    )


def build_planned_m2_manifest(
    loaded_input: LoadedM1Input,
    route: M2Route,
    *,
    route_file: str | Path = "m2_route.json",
    request_file: str | Path = "m2_request.json",
) -> M2Manifest:
    """
    构造尚未开始模型生成的M2 Manifest。
    """

    _check_route_matches_input(
        loaded_input,
        route,
    )

    return M2Manifest(
        schema_version="0.1.0",
        module="M2",
        request_id=route.request_id,
        task_type=route.task_type,
        status="planned",
        source_m1_manifest=str(
            loaded_input.manifest_path
        ),
        source_spec=str(
            loaded_input.payload_path
        ),
        route_file=str(route_file),
        request_file=str(request_file),
        generator_route=route.route,
        provider=None,
        primary_model=None,
        validation_file=None,
        hard_constraints_passed=None,
        next_module=None,
    )