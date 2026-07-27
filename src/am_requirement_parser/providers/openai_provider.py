from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI


class OpenAIProvider:
    """通过OpenAI Responses API获得结构化输出。"""

    def __init__(
        self,
        model: str | None = None,
    ) -> None:
        api_key = os.getenv(
            "OPENAI_API_KEY"
        )

        if not api_key:
            raise RuntimeError(
                "没有检测到OPENAI_API_KEY。"
                "请先在当前PowerShell终端设置API Key。"
            )

        self.model = (
            model
            or os.getenv("OPENAI_MODEL")
            or "gpt-5-mini"
        )

        self.client = OpenAI(
            api_key=api_key
        )

    def generate_structured(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        response = self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=user_input,
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "description": (
                        "增材制造任务路由结果"
                    ),
                    "schema": schema,
                    "strict": True,
                }
            },
            store=False,
        )

        output_text = response.output_text

        if not output_text:
            raise RuntimeError(
                "OpenAI API没有返回可解析文本。"
            )

        try:
            data = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "OpenAI API返回的内容不是合法JSON。"
            ) from error

        if not isinstance(data, dict):
            raise RuntimeError(
                "OpenAI API的结构化输出必须是对象。"
            )

        return data