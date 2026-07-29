from __future__ import annotations
from pathlib import Path
from abc import ABC, abstractmethod
from dataclasses import (
    asdict,
    dataclass,
    field,
)
from typing import Any, Literal


ProviderSubmissionStatus = Literal[
    "submitted",
    "completed",
    "failed",
]


@dataclass(frozen=True)
class CreativeGenerationRequest:
    """
    M2内部交给文生3D Provider的统一请求。
    """

    schema_version: str
    request_id: str
    route: Literal["creative_mesh"]
    provider: str
    idempotency_key: str
    source_payload_sha256: str
    prompt: str
    negative_prompt: str | None
    target_height_mm: float | None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderSubmission:
    """
    Provider任务的统一提交和状态结果。

    status:
    - submitted：已提交，尚未完成；
    - completed：Provider报告生成完成；
    - failed：Provider报告任务失败。
    """

    schema_version: str
    provider: str
    request_id: str
    idempotency_key: str
    provider_job_id: str
    status: ProviderSubmissionStatus
    reused_existing_submission: bool
    artifacts: list[str] = field(
        default_factory=list
    )
    provider_metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GenerationProvider(ABC):
    """
    文生3D Provider统一接口。

    Provider不负责重新解析用户自然语言。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Provider唯一名称。
        """

    def preflight_submit(
        self,
        request: CreativeGenerationRequest,
    ) -> None:
        """
        ???????????????????

        ??Provider?????????
        ????Provider??????????????
        ??????????
        """

        return None

    @abstractmethod
    def submit(
        self,
        request: CreativeGenerationRequest,
    ) -> ProviderSubmission:
        """
        提交一次模型生成请求。
        """

    @abstractmethod
    def get_status(
        self,
        submission: ProviderSubmission,
    ) -> ProviderSubmission:
        """
        查询一个已提交Provider任务的当前状态。
        """

    @abstractmethod
    def download_artifact(
        self,
        artifact_uri: str,
        destination_path: Path,
    ) -> dict[str, Any]:
        """
        将Provider资源取得到指定临时路径。

        返回值只记录Provider侧元数据；
        文件哈希、大小和最终落盘由M2统一处理。
        """