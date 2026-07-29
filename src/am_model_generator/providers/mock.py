from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import trimesh

from am_model_generator.contracts import (
    M2ProviderError,
)

from .base import (
    CreativeGenerationRequest,
    GenerationProvider,
    ProviderSubmission,
)


class MockProvider(GenerationProvider):
    """
    不访问网络的测试Provider。

    submit:
        模拟提交生成任务。

    get_status:
        模拟生成任务完成。

    download_artifact:
        生成一个包含真实三角几何体的GLB立方体，
        用于验证Artifact和Mesh检查流程。
    """

    def __init__(self) -> None:
        self._submissions: dict[
            str,
            ProviderSubmission,
        ] = {}

    @property
    def name(self) -> str:
        return "mock"

    def submit(
        self,
        request: CreativeGenerationRequest,
    ) -> ProviderSubmission:
        if (
            request.provider.lower()
            != self.name
        ):
            raise M2ProviderError(
                "M2_PROVIDER_REQUEST_MISMATCH",
                "生成请求指定的Provider与当前Provider不一致",
                details={
                    "request_provider": (
                        request.provider
                    ),
                    "actual_provider": (
                        self.name
                    ),
                },
            )

        existing = self._submissions.get(
            request.idempotency_key
        )

        if existing is not None:
            return replace(
                existing,
                reused_existing_submission=True,
            )

        provider_job_id = (
            "mock-job-"
            + hashlib.sha256(
                request.idempotency_key.encode(
                    "utf-8"
                )
            ).hexdigest()[:16]
        )

        submission = ProviderSubmission(
            schema_version="0.1.0",
            provider=self.name,
            request_id=request.request_id,
            idempotency_key=(
                request.idempotency_key
            ),
            provider_job_id=(
                provider_job_id
            ),
            status="submitted",
            reused_existing_submission=False,
            artifacts=[],
            provider_metadata={
                "network_called": False,
                "mock": True,
            },
        )

        self._submissions[
            request.idempotency_key
        ] = submission

        return submission

    def get_status(
        self,
        submission: ProviderSubmission,
    ) -> ProviderSubmission:
        if (
            submission.provider.lower()
            != self.name
        ):
            raise M2ProviderError(
                "M2_PROVIDER_STATUS_MISMATCH",
                "状态记录中的Provider与当前Provider不一致",
                details={
                    "submission_provider": (
                        submission.provider
                    ),
                    "actual_provider": (
                        self.name
                    ),
                },
            )

        if submission.status == "completed":
            return submission

        if submission.status == "failed":
            return submission

        if submission.status != "submitted":
            raise M2ProviderError(
                "M2_PROVIDER_STATUS_INVALID",
                "Provider任务状态无法识别",
                details={
                    "status": (
                        submission.status
                    ),
                },
            )

        artifact_uri = (
            f"mock://{submission.provider_job_id}"
            "/model.glb"
        )

        metadata = dict(
            submission.provider_metadata
        )

        metadata.update(
            {
                "network_called": False,
                "mock": True,
                "status_source": (
                    "deterministic_mock"
                ),
            }
        )

        return replace(
            submission,
            status="completed",
            artifacts=[
                artifact_uri
            ],
            provider_metadata=metadata,
        )

    def download_artifact(
        self,
        artifact_uri: str,
        destination_path: Path,
    ) -> dict[str, object]:
        """
        写入一个真实包含三角面的封闭立方体GLB。

        数值尺寸为20 × 20 × 20。
        此处只用于管线测试，不代表AI实际生成结果。
        """

        if (
            not isinstance(
                artifact_uri,
                str,
            )
            or not artifact_uri.startswith(
                "mock://"
            )
            or not artifact_uri.endswith(
                "/model.glb"
            )
        ):
            raise M2ProviderError(
                "M2_MOCK_ARTIFACT_URI_INVALID",
                "MockProvider无法识别Artifact URI",
                details={
                    "artifact_uri": (
                        artifact_uri
                    ),
                },
            )

        destination_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        mesh = trimesh.creation.box(
            extents=(
                20.0,
                20.0,
                20.0,
            )
        )

        # 将模型底面移动到z=0。
        mesh.apply_translation(
            (
                0.0,
                0.0,
                10.0,
            )
        )

        scene = trimesh.Scene(
            mesh
        )

        glb_data = (
            trimesh.exchange.gltf.export_glb(
                scene
            )
        )

        if not isinstance(
            glb_data,
            bytes,
        ):
            raise M2ProviderError(
                "M2_MOCK_GLB_EXPORT_INVALID",
                "MockProvider没有得到GLB二进制数据",
                details={
                    "actual_type": (
                        type(
                            glb_data
                        ).__name__
                    ),
                },
            )

        destination_path.write_bytes(
            glb_data
        )

        return {
            "network_called": False,
            "mock": True,
            "artifact_format": "glb",
            "artifact_source": (
                "trimesh_box"
            ),
            "mock_geometry": "box",
            "mock_extents": [
                20.0,
                20.0,
                20.0,
            ],
            "units_assumption": (
                "millimeter"
            ),
        }