from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from am_model_generator.contracts import (
    M2ProviderError,
)
from am_model_generator.providers.base import (
    CreativeGenerationRequest,
    ProviderSubmission,
)
from am_model_generator.providers.meshy import (
    MeshyProvider,
)


def _request() -> CreativeGenerationRequest:
    return CreativeGenerationRequest(
        schema_version="0.1.0",
        request_id="M2-TEST-MESHY",
        route="creative_mesh",
        provider="meshy",
        idempotency_key=(
            "M2SUB-0123456789ABCDEF0123"
        ),
        source_payload_sha256=(
            "a" * 64
        ),
        prompt=(
            "A seated cartoon puppy, "
            "single connected printable body."
        ),
        negative_prompt=(
            "floating parts, open mesh"
        ),
        target_height_mm=100.0,
        metadata={
            "style": "cartoon",
            "pose": "seated",
        },
    )


def _submission() -> ProviderSubmission:
    return ProviderSubmission(
        schema_version="0.1.0",
        provider="meshy",
        request_id="M2-TEST-MESHY",
        idempotency_key=(
            "M2SUB-0123456789ABCDEF0123"
        ),
        provider_job_id="task-123",
        status="submitted",
        reused_existing_submission=False,
        artifacts=[],
        provider_metadata={
            "network_called": True,
        },
    )


class TestMeshyProvider(
    unittest.TestCase
):
    def test_submit_is_blocked_without_paid_permission(
        self,
    ) -> None:
        network_called = False

        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            nonlocal network_called
            network_called = True

            return httpx.Response(
                202,
                json={
                    "result": "task-123"
                },
            )

        provider = MeshyProvider(
            api_key="test-key",
            allow_paid_requests=False,
            transport=httpx.MockTransport(
                handler
            ),
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            provider.submit(
                _request()
            )

        self.assertEqual(
            context.exception.code,
            (
                "M2_MESHY_PAID_REQUEST_NOT_ALLOWED"
            ),
        )

        self.assertFalse(
            network_called
        )

    def test_missing_api_key_is_rejected(
        self,
    ) -> None:
        provider = MeshyProvider(
            api_key="",
            allow_paid_requests=True,
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            provider.submit(
                _request()
            )

        self.assertEqual(
            context.exception.code,
            (
                "M2_MESHY_API_KEY_MISSING"
            ),
        )

    def test_submit_creates_preview_task(
        self,
    ) -> None:
        captured_payload = None
        captured_authorization = None

        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            nonlocal captured_payload
            nonlocal captured_authorization

            captured_payload = json.loads(
                request.content.decode(
                    "utf-8"
                )
            )

            captured_authorization = (
                request.headers.get(
                    "Authorization"
                )
            )

            return httpx.Response(
                202,
                json={
                    "result": "task-123"
                },
            )

        provider = MeshyProvider(
            api_key="test-key",
            allow_paid_requests=True,
            transport=httpx.MockTransport(
                handler
            ),
        )

        result = provider.submit(
            _request()
        )

        self.assertEqual(
            result.provider,
            "meshy",
        )

        self.assertEqual(
            result.provider_job_id,
            "task-123",
        )

        self.assertEqual(
            result.status,
            "submitted",
        )

        self.assertEqual(
            result.artifacts,
            [],
        )

        self.assertEqual(
            captured_authorization,
            "Bearer test-key",
        )

        self.assertEqual(
            captured_payload["mode"],
            "preview",
        )

        self.assertEqual(
            captured_payload[
                "target_formats"
            ],
            [
                "glb"
            ],
        )

        self.assertFalse(
            captured_payload[
                "auto_size"
            ]
        )

        self.assertNotIn(
            "negative_prompt",
            captured_payload,
        )

    def test_pending_status_maps_to_submitted(
        self,
    ) -> None:
        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "task-123",
                    "status": "IN_PROGRESS",
                    "progress": 45,
                    "consumed_credits": 20,
                },
            )

        provider = MeshyProvider(
            api_key="test-key",
            transport=httpx.MockTransport(
                handler
            ),
        )

        result = provider.get_status(
            _submission()
        )

        self.assertEqual(
            result.status,
            "submitted",
        )

        self.assertEqual(
            result.artifacts,
            [],
        )

        self.assertEqual(
            result.provider_metadata[
                "meshy_status"
            ],
            "IN_PROGRESS",
        )

        self.assertEqual(
            result.provider_metadata[
                "progress"
            ],
            45,
        )

    def test_succeeded_status_returns_glb(
        self,
    ) -> None:
        glb_url = (
            "https://assets.meshy.ai/"
            "tasks/task-123/model.glb"
            "?Expires=999999"
        )

        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "task-123",
                    "status": "SUCCEEDED",
                    "progress": 100,
                    "model_urls": {
                        "glb": glb_url
                    },
                    "task_error": {
                        "message": ""
                    },
                    "consumed_credits": 20,
                },
            )

        provider = MeshyProvider(
            api_key="test-key",
            transport=httpx.MockTransport(
                handler
            ),
        )

        result = provider.get_status(
            _submission()
        )

        self.assertEqual(
            result.status,
            "completed",
        )

        self.assertEqual(
            result.artifacts,
            [
                glb_url
            ],
        )

    def test_failed_status_maps_to_failed(
        self,
    ) -> None:
        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "task-123",
                    "status": "FAILED",
                    "progress": 100,
                    "task_error": {
                        "type": (
                            "invalid_input"
                        ),
                        "message": (
                            "Prompt rejected"
                        ),
                    },
                    "consumed_credits": 0,
                },
            )

        provider = MeshyProvider(
            api_key="test-key",
            transport=httpx.MockTransport(
                handler
            ),
        )

        result = provider.get_status(
            _submission()
        )

        self.assertEqual(
            result.status,
            "failed",
        )

        self.assertEqual(
            result.provider_metadata[
                "task_error"
            ][
                "message"
            ],
            "Prompt rejected",
        )

    def test_payment_error_is_mapped(
        self,
    ) -> None:
        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            return httpx.Response(
                402,
                json={
                    "message": (
                        "Insufficient credits"
                    )
                },
            )

        provider = MeshyProvider(
            api_key="test-key",
            allow_paid_requests=True,
            transport=httpx.MockTransport(
                handler
            ),
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            provider.submit(
                _request()
            )

        self.assertEqual(
            context.exception.code,
            (
                "M2_MESHY_INSUFFICIENT_CREDITS"
            ),
        )

    def test_download_does_not_send_api_key(
        self,
    ) -> None:
        observed_authorization = (
            "NOT_CHECKED"
        )

        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            nonlocal observed_authorization

            observed_authorization = (
                request.headers.get(
                    "Authorization"
                )
            )

            return httpx.Response(
                200,
                headers={
                    "Content-Type": (
                        "model/gltf-binary"
                    )
                },
                content=(
                    b"glTF"
                    + b"\x00" * 64
                ),
            )

        provider = MeshyProvider(
            api_key="secret-key",
            transport=httpx.MockTransport(
                handler
            ),
        )

        with tempfile.TemporaryDirectory() as temporary:
            destination = (
                Path(temporary)
                / "model.glb"
            )

            metadata = (
                provider.download_artifact(
                    (
                        "https://assets.meshy.ai/"
                        "model.glb?Expires=123"
                    ),
                    destination,
                )
            )

            self.assertTrue(
                destination.is_file()
            )

            self.assertGreater(
                destination.stat().st_size,
                0,
            )

            self.assertIsNone(
                observed_authorization
            )

            self.assertFalse(
                metadata[
                    "authorization_header_sent"
                ]
            )


if __name__ == "__main__":
    unittest.main()