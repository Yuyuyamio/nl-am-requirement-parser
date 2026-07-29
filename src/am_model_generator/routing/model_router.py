from __future__ import annotations

import hashlib
import json

from ..contracts import (
    LoadedM1Input,
    M2InputError,
    M2Route,
)


def _build_stable_request_id(
    loaded_input: LoadedM1Input,
) -> str:
    """
    为没有 request_id 的任务生成稳定编号。

    工程件如果已经有 M1 request_id，则直接复用。

    创意任务当前没有 request_id，因此根据正式
    payload 内容生成稳定哈希。同一个规格会得到
    同一个编号，避免后续重复提交付费任务。
    """

    existing_request_id = (
        loaded_input.request_id
    )

    if existing_request_id is not None:
        return existing_request_id

    canonical_data = {
        "task_type": (
            loaded_input.task_type
        ),
        "payload": loaded_input.payload,
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
        "M2-"
        + digest[:12].upper()
    )


def build_m2_route(
    loaded_input: LoadedM1Input,
) -> M2Route:
    """
    根据已经通过输入闸门的 M1 任务，
    确定 M2 模型生成路线。

    路由完全由 M1 task_type 决定，
    本阶段不调用大模型进行二次判断。
    """

    request_id = (
        _build_stable_request_id(
            loaded_input
        )
    )

    if (
        loaded_input.task_type
        == "creative_asset"
    ):
        return M2Route(
            schema_version="0.1.0",
            request_id=request_id,
            task_type="creative_asset",
            route="creative_mesh",
            generator_family=(
                "generation_provider"
            ),
            preferred_provider=None,
            reason_code=(
                "CREATIVE_ASSET_ROUTE"
            ),
            ready_for_generation=True,
        )

    if (
        loaded_input.task_type
        == "engineering_part"
    ):
        return M2Route(
            schema_version="0.1.0",
            request_id=request_id,
            task_type="engineering_part",
            route="parametric_cad",
            generator_family="cadquery",
            preferred_provider="cadquery",
            reason_code=(
                "ENGINEERING_PART_ROUTE"
            ),
            ready_for_generation=True,
        )

    raise M2InputError(
        "M2_UNSUPPORTED_TASK_TYPE",
        "无法为当前任务选择M2生成路线",
        details={
            "task_type": (
                loaded_input.task_type
            ),
        },
    )