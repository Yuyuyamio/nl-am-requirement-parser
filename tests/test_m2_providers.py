from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from am_model_generator.contracts import (
    LoadedM1Input,
    M2ProviderError,
    build_m2_request,
)
from am_model_generator.providers import (
    MockProvider,
    ProviderRegistry,
    build_creative_generation_request,
)
from am_model_generator.routing import (
    build_m2_route,
)


def _creative_spec() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_type": "creative_asset",
        "intent_summary": (
            "生成一只高度200毫米的恐怖老鼠"
        ),
        "object_name": (
            "Super Large Scary Mouse"
        ),
        "category": "animal",
        "visual_description": (
            "A large terrifying mouse "
            "with exaggerated features."
        ),
        "style": None,
        "pose": None,
        "target_height_mm": 200.0,
        "target_dimensions_text": (
            "高度200毫米"
        ),
        "output_target": "3d_print",
        "generation_prompt_en": (
            "Create a large terrifying mouse "
            "as a printable 3D model, "
            "200 mm tall, with exaggerated "
            "but printable features."
        ),
        "negative_prompt_en": (
            "floating geometry, open mesh, "
            "disconnected parts, fragile details"
        ),
        "system_printability_guidance": [
            "watertight closed mesh",
            "single connected main body",
            "flat and stable contact surface",
        ],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.8,
    }


def _loaded_input(
    payload: dict[str, Any] | None = None,
) -> LoadedM1Input:
    creative_payload = (
        payload
        if payload is not None
        else _creative_spec()
    )

    return LoadedM1Input(
        manifest_path=Path(
            "outputs/m1/m1_manifest.json"
        ).resolve(),
        manifest={
            "schema_version": "0.1.0",
            "module": "M1",
            "original_input": (
                "给我打印一只超级大的"
                "恐怖老鼠，高度20厘米"
            ),
            "task_type": "creative_asset",
            "status": "ready",
            "route_file": (
                "task_route.json"
            ),
            "output_file": (
                "creative_asset_spec.json"
            ),
            "next_module": "M2",
        },
        payload_path=Path(
            "outputs/m1/"
            "creative_asset_spec.json"
        ).resolve(),
        payload=creative_payload,
        task_type="creative_asset",
    )


def _provider_request(
    *,
    provider_name: str = "mock",
    payload: dict[str, Any] | None = None,
):
    loaded = _loaded_input(payload)

    route = build_m2_route(
        loaded
    )

    m2_request = build_m2_request(
        loaded,
        route,
    )

    return (
        build_creative_generation_request(
            m2_request=m2_request.to_dict(),
            source_spec=loaded.payload,
            provider_name=provider_name,
        )
    )


class TestM2Providers(unittest.TestCase):
    def test_null_style_and_pose_are_allowed(
        self,
    ) -> None:
        request = _provider_request()

        self.assertIsNone(
            request.metadata["style"]
        )

        self.assertIsNone(
            request.metadata["pose"]
        )

        self.assertEqual(
            request.target_height_mm,
            200.0,
        )

        self.assertIn(
            "terrifying mouse",
            request.prompt,
        )

    def test_idempotency_key_is_stable(
        self,
    ) -> None:
        first = _provider_request()
        second = _provider_request()

        self.assertEqual(
            first.idempotency_key,
            second.idempotency_key,
        )

    def test_changed_prompt_changes_key(
        self,
    ) -> None:
        first_payload = _creative_spec()
        second_payload = _creative_spec()

        second_payload[
            "generation_prompt_en"
        ] = (
            second_payload[
                "generation_prompt_en"
            ]
            + " Add larger ears."
        )

        first = _provider_request(
            payload=first_payload
        )

        second = _provider_request(
            payload=second_payload
        )

        self.assertNotEqual(
            first.idempotency_key,
            second.idempotency_key,
        )

    def test_registry_returns_mock_provider(
        self,
    ) -> None:
        provider = MockProvider()

        registry = ProviderRegistry(
            [provider]
        )

        self.assertEqual(
            registry.names(),
            ["mock"],
        )

        self.assertIs(
            registry.get("MOCK"),
            provider,
        )

    def test_registry_rejects_unknown_provider(
        self,
    ) -> None:
        registry = ProviderRegistry(
            [MockProvider()]
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            registry.get("meshy")

        self.assertEqual(
            context.exception.code,
            "M2_PROVIDER_NOT_FOUND",
        )

    def test_mock_submission_is_idempotent(
        self,
    ) -> None:
        provider = MockProvider()
        request = _provider_request()

        first = provider.submit(
            request
        )

        second = provider.submit(
            request
        )

        self.assertFalse(
            first.reused_existing_submission
        )

        self.assertTrue(
            second.reused_existing_submission
        )

        self.assertEqual(
            first.provider_job_id,
            second.provider_job_id,
        )

        self.assertEqual(
            first.status,
            "submitted",
        )

        self.assertFalse(
            first.provider_metadata[
                "network_called"
            ]
        )

    def test_mock_rejects_provider_mismatch(
        self,
    ) -> None:
        request = _provider_request(
            provider_name="meshy"
        )

        provider = MockProvider()

        with self.assertRaises(
            M2ProviderError
        ) as context:
            provider.submit(request)

        self.assertEqual(
            context.exception.code,
            (
                "M2_PROVIDER_REQUEST_MISMATCH"
            ),
        )


if __name__ == "__main__":
    unittest.main()