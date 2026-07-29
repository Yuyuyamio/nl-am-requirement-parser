from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


M1TaskType = Literal[
    "creative_asset",
    "engineering_part",
]

M2RouteName = Literal[
    "creative_mesh",
    "parametric_cad",
]

M2GeneratorFamily = Literal[
    "generation_provider",
    "cadquery",
]

M2GenerationStatus = Literal[
    "not_started",
]

M2ManifestStatus = Literal[
    "planned",
    "generation_pending",
    "generating",
    "generated",
    "validating",
    "complete",
    "rejected",
    "failed",
]


@dataclass(frozen=True)
class LoadedM1Input:
    """
    已通过M2输入闸门检查的M1交付数据。
    """

    manifest_path: Path
    manifest: dict[str, Any]
    payload_path: Path
    payload: dict[str, Any]
    task_type: M1TaskType

    @property
    def status(self) -> str:
        return str(self.manifest["status"])

    @property
    def original_input(self) -> str:
        return str(self.manifest["original_input"])

    @property
    def request_id(self) -> str | None:
        value = self.payload.get("request_id")

        if not isinstance(value, str):
            return None

        cleaned = value.strip()

        if not cleaned:
            return None

        return cleaned


@dataclass(frozen=True)
class M2Route:
    """
    M2确定性模型生成路线。
    """

    schema_version: str
    request_id: str
    task_type: M1TaskType
    route: M2RouteName
    generator_family: M2GeneratorFamily
    preferred_provider: str | None
    reason_code: str
    ready_for_generation: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class M2Request:
    """
    M2交给具体模型生成器的标准化请求。
    """

    schema_version: str
    module: str
    request_id: str
    task_type: M1TaskType
    source_m1_manifest: str
    source_spec: str
    source_payload_sha256: str
    original_input: str
    route: M2RouteName
    generator_family: M2GeneratorFamily
    preferred_provider: str | None
    generation_status: M2GenerationStatus

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class M2Manifest:
    """
    M2流程状态清单。

    planned阶段不得提前填写模型文件或M3。
    """

    schema_version: str
    module: str
    request_id: str
    task_type: M1TaskType
    status: M2ManifestStatus
    source_m1_manifest: str
    source_spec: str
    route_file: str
    request_file: str
    generator_route: M2RouteName
    provider: str | None
    primary_model: str | None
    validation_file: str | None
    hard_constraints_passed: bool | None
    next_module: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)