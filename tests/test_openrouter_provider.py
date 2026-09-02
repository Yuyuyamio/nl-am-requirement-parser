from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from am_requirement_parser.providers.openrouter_provider import (
    OpenRouterProvider,
)


def _response(
    content: object,
    *,
    model: str = "free/model",
    finish_reason: str = "stop",
) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content),
            )
        ],
    )


def _provider(*responses: SimpleNamespace) -> OpenRouterProvider:
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider.model = "openrouter/free"
    provider.content_retries = 2
    provider.last_model = None
    create = Mock(side_effect=list(responses))
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )
    )
    return provider


class OpenRouterProviderTests(unittest.TestCase):
    def test_retries_empty_content_then_returns_json(self) -> None:
        provider = _provider(
            _response(None, model="free/empty", finish_reason="length"),
            _response('{"ok": true}', model="free/good"),
        )

        result = provider.generate_structured(
            instructions="Return JSON.",
            user_input="hello",
            schema_name="result",
            schema={"type": "object"},
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            provider.client.chat.completions.create.call_count,
            2,
        )
        self.assertEqual(provider.last_model, "free/good")

    def test_retries_invalid_json_then_returns_json(self) -> None:
        provider = _provider(
            _response("not json"),
            _response('{"ok": true}'),
        )

        result = provider.generate_structured(
            instructions="Return JSON.",
            user_input="hello",
            schema_name="result",
            schema={"type": "object"},
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            provider.client.chat.completions.create.call_count,
            2,
        )

    def test_final_error_includes_model_and_finish_reason(self) -> None:
        provider = _provider(
            _response(None, model="free/one", finish_reason="length"),
            _response(None, model="free/two", finish_reason="length"),
            _response(None, model="free/three", finish_reason="length"),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "实际模型：free/three.*结束原因：length.*自动尝试3次",
        ):
            provider.generate_structured(
                instructions="Return JSON.",
                user_input="hello",
                schema_name="result",
                schema={"type": "object"},
            )


if __name__ == "__main__":
    unittest.main()
