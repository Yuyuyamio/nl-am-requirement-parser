from __future__ import annotations

from typing import Any, Protocol


class StructuredOutputProvider(Protocol):
    """能够按照JSON Schema输出数据的模型提供商。"""

    def generate_structured(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """生成符合指定JSON Schema的数据。"""