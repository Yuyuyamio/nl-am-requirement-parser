from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .creative.spec_builder import (
    build_creative_spec,
)
from .parser import parse_requirement
from .providers.base import (
    StructuredOutputProvider,
)
from .routing.router import route_request


@dataclass(frozen=True)
class M1PipelineResult:
    """
    M1统一流程结果。

    payload是M1交付给后续模块的正式任务规格。
    """

    schema_version: str
    module: str
    task_type: str
    status: str
    output_filename: str
    next_module: str | None
    route: dict[str, Any]
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_m1_pipeline(
    text: str,
    provider: StructuredOutputProvider,
) -> M1PipelineResult:
    cleaned = text.strip()

    if not cleaned:
        raise ValueError(
            "用户输入不能为空"
        )

    route = route_request(
        cleaned,
        provider,
    )

    route_data = route.to_dict()

    if route.task_type == "creative_asset":
        creative_spec = build_creative_spec(
            cleaned,
            provider,
        )

        payload = creative_spec.to_dict()

        if creative_spec.needs_clarification:
            status = "needs_clarification"
            next_module = None
        else:
            status = "ready"
            next_module = "M2"

        return M1PipelineResult(
            schema_version="0.1.0",
            module="M1",
            task_type="creative_asset",
            status=status,
            output_filename=(
                "creative_asset_spec.json"
            ),
            next_module=next_module,
            route=route_data,
            payload=payload,
        )

    if route.task_type == "engineering_part":
        engineering_spec = parse_requirement(
            cleaned
        )

        payload = engineering_spec.to_dict()

        engineering_status = payload.get(
            "status",
            "incomplete",
        )

        if engineering_status == "complete":
            next_module = "M2"
        else:
            next_module = None

        return M1PipelineResult(
            schema_version="0.1.0",
            module="M1",
            task_type="engineering_part",
            status=str(engineering_status),
            output_filename=(
                "requirement_spec.json"
            ),
            next_module=next_module,
            route=route_data,
            payload=payload,
        )

    return M1PipelineResult(
        schema_version="0.1.0",
        module="M1",
        task_type="unknown",
        status="needs_clarification",
        output_filename=(
            "unresolved_task.json"
        ),
        next_module=None,
        route=route_data,
        payload=route_data,
    )