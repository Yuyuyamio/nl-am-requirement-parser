from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from am_model_generator.contracts import (
    M2ProviderError,
)

from .base import (
    CreativeGenerationRequest,
    GenerationProvider,
    ProviderSubmission,
)


class MeshyProvider(GenerationProvider):
    """
    Meshy Text-to-3D Preview Provider。

    安全规则：
    1. API密钥只从构造参数或MESHY_API_KEY读取；
    2. 只有显式允许付费请求时，submit才可访问网络；
    3. 状态查询不会创建新任务；
    4. 下载签名URL时不发送Meshy API密钥；
    5. 第一版只生成无纹理GLB Preview。
    """

    DEFAULT_BASE_URL = (
        "https://api.meshy.ai"
    )

    CREATE_PATH = (
        "/openapi/v2/text-to-3d"
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        allow_paid_requests: bool | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key_override = api_key
        self._allow_paid_override = (
            allow_paid_requests
        )
        self._base_url = (
            base_url.rstrip("/")
        )
        self._timeout_seconds = float(
            timeout_seconds
        )
        self._transport = transport

        if self._timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

    @property
    def name(self) -> str:
        return "meshy"

    def _require_api_key(self) -> str:
        if self._api_key_override is not None:
            value = self._api_key_override
        else:
            value = os.getenv(
                "MESHY_API_KEY",
                "",
            )

        normalized = value.strip()

        if not normalized:
            raise M2ProviderError(
                "M2_MESHY_API_KEY_MISSING",
                "未设置Meshy API密钥",
                details={
                    "environment_variable": (
                        "MESHY_API_KEY"
                    ),
                },
            )

        return normalized

    def _paid_requests_allowed(
        self,
    ) -> bool:
        if (
            self._allow_paid_override
            is not None
        ):
            return bool(
                self._allow_paid_override
            )

        environment_value = os.getenv(
            "MESHY_ALLOW_PAID_REQUESTS",
            "",
        )

        return (
            environment_value
            .strip()
            .lower()
            == "true"
        )

    @staticmethod
    def _extract_error_message(
        response: httpx.Response,
    ) -> str:
        try:
            data = response.json()
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            data = None

        if isinstance(data, dict):
            message = data.get(
                "message"
            )

            if (
                isinstance(message, str)
                and message.strip()
            ):
                return message.strip()

            task_error = data.get(
                "task_error"
            )

            if isinstance(
                task_error,
                dict,
            ):
                task_message = (
                    task_error.get(
                        "message"
                    )
                )

                if (
                    isinstance(
                        task_message,
                        str,
                    )
                    and task_message.strip()
                ):
                    return (
                        task_message.strip()
                    )

        text = response.text.strip()

        if text:
            return text[:500]

        return (
            f"HTTP {response.status_code}"
        )

    def _raise_http_error(
        self,
        response: httpx.Response,
        *,
        operation: str,
    ) -> None:
        status_code = (
            response.status_code
        )

        message = (
            self._extract_error_message(
                response
            )
        )

        error_code_by_status = {
            400: "M2_MESHY_BAD_REQUEST",
            401: "M2_MESHY_UNAUTHORIZED",
            402: (
                "M2_MESHY_INSUFFICIENT_CREDITS"
            ),
            403: "M2_MESHY_FORBIDDEN",
            404: "M2_MESHY_NOT_FOUND",
            429: "M2_MESHY_RATE_LIMITED",
        }

        if status_code in error_code_by_status:
            error_code = (
                error_code_by_status[
                    status_code
                ]
            )
        elif status_code >= 500:
            error_code = (
                "M2_MESHY_SERVER_ERROR"
            )
        else:
            error_code = (
                "M2_MESHY_HTTP_ERROR"
            )

        raise M2ProviderError(
            error_code,
            "Meshy API请求失败",
            details={
                "operation": operation,
                "http_status": (
                    status_code
                ),
                "remote_message": (
                    message
                ),
            },
        )

    def _api_request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[
            str,
            Any,
        ] | None = None,
    ) -> dict[str, Any]:
        api_key = self._require_api_key()

        url = (
            f"{self._base_url}{path}"
        )

        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = client.request(
                    method,
                    url,
                    headers={
                        "Authorization": (
                            f"Bearer {api_key}"
                        ),
                        "Content-Type": (
                            "application/json"
                        ),
                        "Accept": (
                            "application/json"
                        ),
                    },
                    json=json_body,
                )
        except httpx.TimeoutException as error:
            raise M2ProviderError(
                "M2_MESHY_TIMEOUT",
                "Meshy API请求超时",
                details={
                    "method": method,
                    "path": path,
                    "reason": str(error),
                },
            ) from error
        except httpx.RequestError as error:
            raise M2ProviderError(
                "M2_MESHY_NETWORK_ERROR",
                "无法连接Meshy API",
                details={
                    "method": method,
                    "path": path,
                    "reason": str(error),
                },
            ) from error

        if response.status_code >= 400:
            self._raise_http_error(
                response,
                operation=(
                    f"{method} {path}"
                ),
            )

        try:
            data = response.json()
        except (
            json.JSONDecodeError,
            ValueError,
        ) as error:
            raise M2ProviderError(
                "M2_MESHY_RESPONSE_JSON_INVALID",
                "Meshy API返回了非JSON响应",
                details={
                    "method": method,
                    "path": path,
                    "http_status": (
                        response.status_code
                    ),
                },
            ) from error

        if not isinstance(data, dict):
            raise M2ProviderError(
                "M2_MESHY_RESPONSE_OBJECT_INVALID",
                "Meshy API响应必须是JSON对象",
                details={
                    "actual_type": (
                        type(data).__name__
                    ),
                },
            )

        return data

    def preflight_submit(
        self,
        request: CreativeGenerationRequest,
    ) -> None:
        """
        ???????????????????
        """

        if (
            request.provider
            .strip()
            .lower()
            != self.name
        ):
            raise M2ProviderError(
                "M2_PROVIDER_REQUEST_MISMATCH",
                "???????Provider?MeshyProvider???",
                details={
                    "request_provider": (
                        request.provider
                    ),
                    "actual_provider": (
                        self.name
                    ),
                    "network_called": False,
                },
            )

        if not self._paid_requests_allowed():
            raise M2ProviderError(
                "M2_MESHY_PAID_REQUEST_NOT_ALLOWED",
                "Meshy?????????????",
                details={
                    "required_environment": (
                        "MESHY_ALLOW_PAID_REQUESTS=true"
                    ),
                    "network_called": False,
                },
            )

        prompt = request.prompt.strip()

        if not prompt:
            raise M2ProviderError(
                "M2_MESHY_PROMPT_EMPTY",
                "Meshy??Prompt????",
                details={
                    "network_called": False,
                },
            )

        if len(prompt) > 600:
            raise M2ProviderError(
                "M2_MESHY_PROMPT_TOO_LONG",
                "Meshy??Prompt??600??",
                details={
                    "prompt_length": (
                        len(prompt)
                    ),
                    "maximum_length": 600,
                    "network_called": False,
                },
            )

        self._require_api_key()

    def submit(
        self,
        request: CreativeGenerationRequest,
    ) -> ProviderSubmission:
        self.preflight_submit(
            request
        )
        if (
            request.provider
            .strip()
            .lower()
            != self.name
        ):
            raise M2ProviderError(
                "M2_PROVIDER_REQUEST_MISMATCH",
                "生成请求指定的Provider与MeshyProvider不一致",
                details={
                    "request_provider": (
                        request.provider
                    ),
                    "actual_provider": (
                        self.name
                    ),
                },
            )

        if not self._paid_requests_allowed():
            raise M2ProviderError(
                "M2_MESHY_PAID_REQUEST_NOT_ALLOWED",
                "Meshy付费生成请求未获得显式授权",
                details={
                    "required_environment": (
                        "MESHY_ALLOW_PAID_REQUESTS=true"
                    ),
                    "network_called": False,
                },
            )

        prompt = request.prompt.strip()

        if not prompt:
            raise M2ProviderError(
                "M2_MESHY_PROMPT_EMPTY",
                "Meshy生成Prompt不能为空",
            )

        if len(prompt) > 600:
            raise M2ProviderError(
                "M2_MESHY_PROMPT_TOO_LONG",
                "Meshy生成Prompt超过600字符",
                details={
                    "prompt_length": (
                        len(prompt)
                    ),
                    "maximum_length": 600,
                },
            )

        payload = {
            "mode": "preview",
            "prompt": prompt,
            "model_type": "standard",
            "ai_model": "latest",
            "should_remesh": True,
            "topology": "triangle",
            "target_polycount": 30000,
            "target_formats": [
                "glb"
            ],
            "auto_size": False,
        }

        data = self._api_request_json(
            "POST",
            self.CREATE_PATH,
            json_body=payload,
        )

        provider_job_id = data.get(
            "result"
        )

        if (
            not isinstance(
                provider_job_id,
                str,
            )
            or not provider_job_id.strip()
        ):
            raise M2ProviderError(
                "M2_MESHY_TASK_ID_MISSING",
                "Meshy创建任务成功但未返回任务ID",
                details={
                    "response_keys": sorted(
                        data.keys()
                    ),
                },
            )

        return ProviderSubmission(
            schema_version="0.1.0",
            provider=self.name,
            request_id=request.request_id,
            idempotency_key=(
                request.idempotency_key
            ),
            provider_job_id=(
                provider_job_id.strip()
            ),
            status="submitted",
            reused_existing_submission=False,
            artifacts=[],
            provider_metadata={
                "network_called": True,
                "meshy_api_version": "v2",
                "meshy_mode": "preview",
                "meshy_status": "CREATED",
                "ai_model": "latest",
                "model_type": "standard",
                "topology": "triangle",
                "target_polycount": 30000,
                "target_formats": [
                    "glb"
                ],
                "auto_size": False,
                "negative_prompt_sent": (
                    False
                ),
            },
        )

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
                "Provider Submission不属于Meshy",
                details={
                    "submission_provider": (
                        submission.provider
                    ),
                    "actual_provider": (
                        self.name
                    ),
                },
            )

        provider_job_id = (
            submission.provider_job_id
            .strip()
        )

        if not provider_job_id:
            raise M2ProviderError(
                "M2_MESHY_TASK_ID_MISSING",
                "Provider Submission缺少Meshy任务ID",
            )

        data = self._api_request_json(
            "GET",
            (
                f"{self.CREATE_PATH}/"
                f"{provider_job_id}"
            ),
        )

        returned_id = data.get("id")

        if (
            isinstance(returned_id, str)
            and returned_id.strip()
            and returned_id.strip()
            != provider_job_id
        ):
            raise M2ProviderError(
                "M2_MESHY_TASK_ID_MISMATCH",
                "Meshy状态响应返回了不同的任务ID",
                details={
                    "expected_task_id": (
                        provider_job_id
                    ),
                    "actual_task_id": (
                        returned_id
                    ),
                },
            )

        remote_status_value = (
            data.get("status")
        )

        if not isinstance(
            remote_status_value,
            str,
        ):
            raise M2ProviderError(
                "M2_MESHY_STATUS_MISSING",
                "Meshy状态响应缺少status",
            )

        remote_status = (
            remote_status_value
            .strip()
            .upper()
        )

        progress = data.get(
            "progress"
        )

        consumed_credits = data.get(
            "consumed_credits"
        )

        task_error = data.get(
            "task_error"
        )

        if not isinstance(
            task_error,
            dict,
        ):
            task_error = {}

        metadata = dict(
            submission.provider_metadata
        )

        metadata.update(
            {
                "network_called": True,
                "meshy_status": (
                    remote_status
                ),
                "progress": progress,
                "consumed_credits": (
                    consumed_credits
                ),
                "task_error": task_error,
            }
        )

        if remote_status in {
            "PENDING",
            "IN_PROGRESS",
        }:
            return replace(
                submission,
                status="submitted",
                artifacts=[],
                provider_metadata=metadata,
            )

        if remote_status == "SUCCEEDED":
            model_urls = data.get(
                "model_urls"
            )

            if not isinstance(
                model_urls,
                dict,
            ):
                raise M2ProviderError(
                    "M2_MESHY_MODEL_URLS_MISSING",
                    "Meshy任务成功但未返回model_urls",
                )

            glb_url = model_urls.get(
                "glb"
            )

            if (
                not isinstance(
                    glb_url,
                    str,
                )
                or not glb_url.strip()
            ):
                raise M2ProviderError(
                    "M2_MESHY_GLB_URL_MISSING",
                    "Meshy任务成功但未返回GLB地址",
                    details={
                        "available_formats": (
                            sorted(
                                model_urls.keys()
                            )
                        ),
                    },
                )

            return replace(
                submission,
                status="completed",
                artifacts=[
                    glb_url.strip()
                ],
                provider_metadata=metadata,
            )

        if remote_status in {
            "FAILED",
            "CANCELED",
        }:
            return replace(
                submission,
                status="failed",
                artifacts=[],
                provider_metadata=metadata,
            )

        raise M2ProviderError(
            "M2_MESHY_STATUS_UNKNOWN",
            "Meshy返回了无法识别的任务状态",
            details={
                "remote_status": (
                    remote_status
                ),
            },
        )

    def download_artifact(
        self,
        artifact_uri: str,
        destination_path: Path,
    ) -> dict[str, object]:
        if (
            not isinstance(
                artifact_uri,
                str,
            )
            or not artifact_uri.strip()
        ):
            raise M2ProviderError(
                "M2_MESHY_ARTIFACT_URI_INVALID",
                "Meshy Artifact地址不能为空",
            )

        normalized_uri = (
            artifact_uri.strip()
        )

        parsed_uri = urlparse(
            normalized_uri
        )

        if parsed_uri.scheme != "https":
            raise M2ProviderError(
                "M2_MESHY_ARTIFACT_URI_INVALID",
                "Meshy Artifact必须使用HTTPS地址",
                details={
                    "scheme": (
                        parsed_uri.scheme
                    ),
                },
            )

        destination_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                with client.stream(
                    "GET",
                    normalized_uri,
                    headers={
                        "Accept": (
                            "model/gltf-binary,"
                            "application/octet-stream,"
                            "*/*"
                        ),
                    },
                ) as response:
                    if (
                        response.status_code
                        >= 400
                    ):
                        self._raise_http_error(
                            response,
                            operation=(
                                "DOWNLOAD GLB"
                            ),
                        )

                    with destination_path.open(
                        "wb"
                    ) as output_file:
                        for chunk in (
                            response.iter_bytes()
                        ):
                            if chunk:
                                output_file.write(
                                    chunk
                                )

                    content_type = (
                        response.headers.get(
                            "content-type",
                            "",
                        )
                    )
        except httpx.TimeoutException as error:
            raise M2ProviderError(
                "M2_MESHY_DOWNLOAD_TIMEOUT",
                "Meshy GLB下载超时",
                details={
                    "host": (
                        parsed_uri.hostname
                    ),
                    "reason": str(error),
                },
            ) from error
        except httpx.RequestError as error:
            raise M2ProviderError(
                "M2_MESHY_DOWNLOAD_NETWORK_ERROR",
                "无法下载Meshy GLB",
                details={
                    "host": (
                        parsed_uri.hostname
                    ),
                    "reason": str(error),
                },
            ) from error
        except OSError as error:
            raise M2ProviderError(
                "M2_MESHY_DOWNLOAD_WRITE_FAILED",
                "无法写入Meshy GLB临时文件",
                details={
                    "path": str(
                        destination_path
                    ),
                    "reason": str(error),
                },
            ) from error

        if (
            not destination_path.is_file()
            or destination_path.stat().st_size
            <= 0
        ):
            raise M2ProviderError(
                "M2_MESHY_DOWNLOAD_EMPTY",
                "Meshy下载结果为空文件",
                details={
                    "path": str(
                        destination_path
                    ),
                },
            )

        return {
            "network_called": True,
            "provider": self.name,
            "source_host": (
                parsed_uri.hostname
            ),
            "artifact_format": "glb",
            "content_type": (
                content_type
            ),
            "authorization_header_sent": (
                False
            ),
        }