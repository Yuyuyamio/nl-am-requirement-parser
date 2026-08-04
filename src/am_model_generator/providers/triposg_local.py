from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlparse

from am_model_generator.contracts import M2ProviderError
from am_model_generator.wsl_bridge import WslBridge

from .base import (
    CreativeGenerationRequest,
    GenerationProvider,
    ProviderSubmission,
)


_JOB_ID_PATTERN = re.compile(
    r"^triposg-local-[0-9a-f]{16}$"
)


class TripoSGLocalProvider(GenerationProvider):
    """
    Fully local creative-mesh Provider.

    prompt -> local T2I worker -> PNG -> local TripoSG worker -> GLB
    """

    def __init__(
        self,
        *,
        bridge: WslBridge | None = None,
        cache_root: Path | None = None,
        wsl_cache_root: str | None = None,
        linux_home: str = "/home/fishcan",
        t2i_worker_path: str | None = None,
        triposg_worker_path: str | None = None,
        t2i_model_root: str | None = None,
        t2i_lora_root: str | None = None,
        t2i_environment: str = "t2i",
        triposg_environment: str = "triposg",
        t2i_steps: int = 6,
        t2i_seed: int = 42,
        t2i_guidance_scale: float = 1.0,
        image_width: int = 512,
        image_height: int = 512,
        triposg_steps: int = 20,
        triposg_seed: int = 42,
        triposg_guidance_scale: float = 7.0,
        triposg_faces: int = 5000,
        timeout_seconds: float = 1800.0,
    ) -> None:
        self._linux_home = self._normalize_linux_path(
            linux_home,
            field_name="linux_home",
        )
        self._bridge = bridge or WslBridge(
            linux_home=self._linux_home,
            timeout_seconds=timeout_seconds,
        )

        if cache_root is None:
            project_root = (
                Path(__file__)
                .resolve()
                .parents[3]
            )

            cache_root = (
                project_root
                / ".m2_local_cache"
                / "triposg_local"
            )

        self._cache_root = cache_root.expanduser().resolve()

        if wsl_cache_root is None:
            self._wsl_cache_root = self._windows_path_to_wsl(
                self._cache_root
            )
        else:
            self._wsl_cache_root = self._normalize_linux_path(
                wsl_cache_root,
                field_name="wsl_cache_root",
            )

        workspace_root = str(
            PurePosixPath(self._linux_home)
            / "workspace"
        )

        self._t2i_worker_path = self._normalize_linux_path(
            t2i_worker_path
            or (
                workspace_root
                + "/triposg_worker/"
                + "run_reference_image_worker.py"
            ),
            field_name="t2i_worker_path",
        )
        self._triposg_worker_path = self._normalize_linux_path(
            triposg_worker_path
            or (
                workspace_root
                + "/triposg_worker/"
                + "run_triposg_worker.py"
            ),
            field_name="triposg_worker_path",
        )
        self._t2i_model_root = self._normalize_linux_path(
            t2i_model_root
            or (
                workspace_root
                + "/model_cache/"
                + "stable-diffusion-v1-5-minimal"
            ),
            field_name="t2i_model_root",
        )
        self._t2i_lora_root = self._normalize_linux_path(
            t2i_lora_root
            or (
                workspace_root
                + "/model_cache/"
                + "lcm-lora-sdv1-5"
            ),
            field_name="t2i_lora_root",
        )

        self._t2i_environment = t2i_environment.strip()
        self._triposg_environment = triposg_environment.strip()

        if not self._t2i_environment:
            raise ValueError(
                "t2i_environment must not be empty"
            )
        if not self._triposg_environment:
            raise ValueError(
                "triposg_environment must not be empty"
            )

        self._t2i_steps = self._positive_int(
            t2i_steps,
            field_name="t2i_steps",
        )
        self._t2i_seed = int(t2i_seed)
        self._t2i_guidance_scale = float(
            t2i_guidance_scale
        )
        self._image_width = self._positive_int(
            image_width,
            field_name="image_width",
        )
        self._image_height = self._positive_int(
            image_height,
            field_name="image_height",
        )
        self._triposg_steps = self._positive_int(
            triposg_steps,
            field_name="triposg_steps",
        )
        self._triposg_seed = int(triposg_seed)
        self._triposg_guidance_scale = float(
            triposg_guidance_scale
        )
        self._triposg_faces = self._positive_int(
            triposg_faces,
            field_name="triposg_faces",
        )

        if self._image_width % 8 != 0:
            raise ValueError(
                "image_width must be divisible by 8"
            )
        if self._image_height % 8 != 0:
            raise ValueError(
                "image_height must be divisible by 8"
            )

        self._submissions: dict[
            str,
            ProviderSubmission,
        ] = {}

    @property
    def name(self) -> str:
        return "triposg_local"

    @staticmethod
    def _positive_int(
        value: int,
        *,
        field_name: str,
    ) -> int:
        normalized = int(value)
        if normalized <= 0:
            raise ValueError(
                f"{field_name} must be positive"
            )
        return normalized

    @staticmethod
    def _normalize_linux_path(
        value: str,
        *,
        field_name: str,
    ) -> str:
        cleaned = value.strip()
        if (
            not cleaned
            or not cleaned.startswith("/")
        ):
            raise ValueError(
                f"{field_name} must be an absolute Linux path"
            )
        return str(PurePosixPath(cleaned))

    @staticmethod
    def _windows_path_to_wsl(
        path: Path,
    ) -> str:
        pure_path = PureWindowsPath(
            str(path)
        )
        drive = pure_path.drive

        if (
            len(drive) != 2
            or drive[1] != ":"
        ):
            raise ValueError(
                "cache_root must be an absolute Windows drive path "
                "when wsl_cache_root is not supplied"
            )

        return str(
            PurePosixPath(
                "/mnt",
                drive[0].lower(),
                *pure_path.parts[1:],
            )
        )

    @staticmethod
    def _provider_job_id(
        idempotency_key: str,
    ) -> str:
        suffix = hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()[:16]
        return "triposg-local-" + suffix

    def _job_windows_directory(
        self,
        provider_job_id: str,
    ) -> Path:
        return (
            self._cache_root
            / provider_job_id
        )

    def _job_wsl_directory(
        self,
        provider_job_id: str,
    ) -> str:
        return str(
            PurePosixPath(
                self._wsl_cache_root,
                provider_job_id,
            )
        )

    def preflight_submit(
        self,
        request: CreativeGenerationRequest,
    ) -> None:
        if (
            request.provider
            .strip()
            .lower()
            != self.name
        ):
            raise M2ProviderError(
                "M2_PROVIDER_REQUEST_MISMATCH",
                "生成请求指定的Provider与TripoSGLocalProvider不一致",
                details={
                    "request_provider": request.provider,
                    "actual_provider": self.name,
                    "network_called": False,
                },
            )

        prompt = request.prompt.strip()
        if not prompt:
            raise M2ProviderError(
                "M2_TRIPOSG_LOCAL_PROMPT_EMPTY",
                "TripoSG本地生成Prompt不能为空",
                details={
                    "network_called": False,
                },
            )
        if len(prompt) > 2000:
            raise M2ProviderError(
                "M2_TRIPOSG_LOCAL_PROMPT_TOO_LONG",
                "TripoSG本地生成Prompt超过2000字符",
                details={
                    "prompt_length": len(prompt),
                    "maximum_length": 2000,
                    "network_called": False,
                },
            )

    def submit(
        self,
        request: CreativeGenerationRequest,
    ) -> ProviderSubmission:
        self.preflight_submit(request)

        existing = self._submissions.get(
            request.idempotency_key
        )
        if existing is not None:
            return replace(
                existing,
                reused_existing_submission=True,
            )

        provider_job_id = self._provider_job_id(
            request.idempotency_key
        )
        windows_job_directory = (
            self._job_windows_directory(
                provider_job_id
            )
        )
        windows_job_directory.mkdir(
            parents=True,
            exist_ok=True,
        )
        wsl_job_directory = (
            self._job_wsl_directory(
                provider_job_id
            )
        )

        reference_windows_path = (
            windows_job_directory
            / "reference_image.png"
        )
        model_windows_path = (
            windows_job_directory
            / "generated_model.glb"
        )
        reference_wsl_path = str(
            PurePosixPath(
                wsl_job_directory,
                "reference_image.png",
            )
        )
        model_wsl_path = str(
            PurePosixPath(
                wsl_job_directory,
                "generated_model.glb",
            )
        )

        t2i_arguments = [
            "--model-root",
            self._t2i_model_root,
            "--lora-root",
            self._t2i_lora_root,
            "--prompt",
            request.prompt.strip(),
        ]

        if (
            request.negative_prompt is not None
            and request.negative_prompt.strip()
        ):
            t2i_arguments.extend(
                [
                    "--negative-prompt",
                    request.negative_prompt.strip(),
                ]
            )

        t2i_arguments.extend(
            [
                "--output-path",
                reference_wsl_path,
                "--steps",
                str(self._t2i_steps),
                "--seed",
                str(self._t2i_seed),
                "--guidance-scale",
                str(self._t2i_guidance_scale),
                "--width",
                str(self._image_width),
                "--height",
                str(self._image_height),
                "--reuse-existing",
            ]
        )

        t2i_result = self._bridge.run_json_worker(
            environment_name=self._t2i_environment,
            script_path=self._t2i_worker_path,
            arguments=t2i_arguments,
        )

        if not reference_windows_path.is_file():
            raise M2ProviderError(
                "M2_TRIPOSG_LOCAL_REFERENCE_MISSING",
                "T2I Worker完成后没有找到参考图",
                details={
                    "expected_path": str(
                        reference_windows_path
                    ),
                    "worker_payload": t2i_result.payload,
                    "network_called": False,
                },
            )

        triposg_arguments = [
            "--image-input",
            reference_wsl_path,
            "--output-path",
            model_wsl_path,
            "--faces",
            str(self._triposg_faces),
            "--steps",
            str(self._triposg_steps),
            "--seed",
            str(self._triposg_seed),
            "--guidance-scale",
            str(self._triposg_guidance_scale),
            "--reuse-existing",
        ]

        triposg_result = self._bridge.run_json_worker(
            environment_name=self._triposg_environment,
            script_path=self._triposg_worker_path,
            arguments=triposg_arguments,
        )

        if (
            not model_windows_path.is_file()
            or model_windows_path.stat().st_size <= 0
        ):
            raise M2ProviderError(
                "M2_TRIPOSG_LOCAL_MODEL_MISSING",
                "TripoSG Worker完成后没有找到有效GLB",
                details={
                    "expected_path": str(
                        model_windows_path
                    ),
                    "worker_payload": (
                        triposg_result.payload
                    ),
                    "network_called": False,
                },
            )

        artifact_uri = (
            f"triposg-local://{provider_job_id}"
            "/model.glb"
        )

        submission = ProviderSubmission(
            schema_version="0.1.0",
            provider=self.name,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            provider_job_id=provider_job_id,
            status="completed",
            reused_existing_submission=False,
            artifacts=[artifact_uri],
            provider_metadata={
                "network_called": False,
                "local_generation": True,
                "pipeline": [
                    "sd15_lcm_reference_image",
                    "triposg",
                ],
                "reference_image_path": str(
                    reference_windows_path
                ),
                "generated_model_path": str(
                    model_windows_path
                ),
                "reference_worker": t2i_result.payload,
                "triposg_worker": triposg_result.payload,
                "target_height_mm": (
                    request.target_height_mm
                ),
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
            submission.provider
            .strip()
            .lower()
            != self.name
        ):
            raise M2ProviderError(
                "M2_PROVIDER_STATUS_MISMATCH",
                "Provider Submission不属于TripoSGLocalProvider",
                details={
                    "submission_provider": submission.provider,
                    "actual_provider": self.name,
                    "network_called": False,
                },
            )
        return submission

    def _source_path_from_artifact_uri(
        self,
        artifact_uri: str,
    ) -> Path:
        parsed = urlparse(
            artifact_uri
        )

        if (
            parsed.scheme != "triposg-local"
            or not _JOB_ID_PATTERN.fullmatch(
                parsed.netloc
            )
            or parsed.path != "/model.glb"
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise M2ProviderError(
                "M2_TRIPOSG_LOCAL_ARTIFACT_URI_INVALID",
                "TripoSGLocalProvider无法识别Artifact URI",
                details={
                    "artifact_uri": artifact_uri,
                    "network_called": False,
                },
            )

        return (
            self._job_windows_directory(
                parsed.netloc
            )
            / "generated_model.glb"
        )

    def download_artifact(
        self,
        artifact_uri: str,
        destination_path: Path,
    ) -> dict[str, object]:
        source_path = (
            self._source_path_from_artifact_uri(
                artifact_uri
            )
        )

        if (
            not source_path.is_file()
            or source_path.stat().st_size <= 0
        ):
            raise M2ProviderError(
                "M2_TRIPOSG_LOCAL_ARTIFACT_MISSING",
                "TripoSG本地Artifact不存在",
                details={
                    "artifact_uri": artifact_uri,
                    "source_path": str(
                        source_path
                    ),
                    "network_called": False,
                },
            )

        destination_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        source_resolved = source_path.resolve()
        destination_resolved = (
            destination_path.resolve()
        )

        if (
            os.path.normcase(
                str(source_resolved)
            )
            != os.path.normcase(
                str(destination_resolved)
            )
        ):
            shutil.copy2(
                source_resolved,
                destination_resolved,
            )

        return {
            "network_called": False,
            "local_generation": True,
            "artifact_format": "glb",
            "artifact_source": "triposg_local_cache",
            "source_path": str(source_resolved),
            "artifact_uri": artifact_uri,
        }
