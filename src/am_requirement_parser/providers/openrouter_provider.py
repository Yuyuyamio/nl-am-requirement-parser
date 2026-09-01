from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI, OpenAIError


def _extract_json_object(
    raw_text: str,
) -> dict[str, Any]:
    """
    从模型文本中提取JSON对象。

    依次处理：
    1. 纯JSON；
    2. Markdown ```json 代码块；
    3. JSON前后混入说明文字。
    """

    cleaned = raw_text.strip()

    if not cleaned:
        raise RuntimeError(
            "OpenRouter返回了空文本。"
        )

    # 情况1：标准JSON
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        return data

    # 情况2：Markdown代码块
    fenced_match = re.search(
        r"```(?:json)?\s*(\{.*\})\s*```",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if fenced_match:
        candidate = fenced_match.group(1)

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            data = None

        if isinstance(data, dict):
            return data

    # 情况3：前后包含解释文字
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")

    if (
        first_brace >= 0
        and last_brace > first_brace
    ):
        candidate = cleaned[
            first_brace:last_brace + 1
        ]

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as error:
            preview = cleaned[:500]

            raise RuntimeError(
                "OpenRouter返回内容包含JSON，"
                "但JSON语法仍然无效。\n"
                f"返回内容预览：\n{preview}"
            ) from error

        if isinstance(data, dict):
            return data

    preview = cleaned[:500]

    raise RuntimeError(
        "OpenRouter返回的内容中没有找到"
        "合法JSON对象。\n"
        f"返回内容预览：\n{preview}"
    )


class OpenRouterProvider:
    """通过OpenRouter获得JSON Schema结构化输出。"""

    def __init__(
        self,
        model: str | None = None,
    ) -> None:
        api_key = os.getenv(
            "OPENROUTER_API_KEY"
        )

        if not api_key:
            raise RuntimeError(
                "没有检测到OPENROUTER_API_KEY。"
                "请先在当前PowerShell终端"
                "设置OpenRouter密钥。"
            )

        self.model = (
            model
            or os.getenv("OPENROUTER_MODEL")
            or "openrouter/free"
        )

        try:
            request_timeout_seconds = float(
                os.getenv("OPENROUTER_TIMEOUT_SECONDS", "45")
            )
        except ValueError:
            request_timeout_seconds = 45.0

        try:
            max_retries = int(
                os.getenv("OPENROUTER_MAX_RETRIES", "2")
            )
        except ValueError:
            max_retries = 2

        self.client = OpenAI(
            base_url=(
                "https://openrouter.ai/api/v1"
            ),
            api_key=api_key,
            timeout=request_timeout_seconds,
            max_retries=max_retries,
            default_headers={
                "X-OpenRouter-Title": (
                    "NL AM Requirement Parser"
                ),
            },
        )

        self.last_model: str | None = None

    def generate_structured(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = (
                self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": instructions,
                        },
                        {
                            "role": "user",
                            "content": user_input,
                        },
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": schema,
                        },
                    },
                    temperature=0,
                    stream=False,
                    extra_body={
                        "provider": {
                            "require_parameters": True,
                        },
                        "plugins": [
                            {
                                "id": (
                                    "response-healing"
                                )
                            }
                        ],
                    },
                )
            )

        except OpenAIError as error:
            raise RuntimeError(
                "OpenRouter API调用失败："
                f"{error}"
            ) from error

        self.last_model = response.model

        if not response.choices:
            raise RuntimeError(
                "OpenRouter没有返回任何候选结果。"
            )

        message = response.choices[0].message
        output_text = message.content

        if output_text is None:
            raise RuntimeError(
                "OpenRouter没有返回文本内容。"
            )

        if not isinstance(output_text, str):
            raise RuntimeError(
                "OpenRouter返回了非字符串内容，"
                "当前程序无法解析。"
            )

        return _extract_json_object(
            output_text
        )