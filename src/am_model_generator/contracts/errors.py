from __future__ import annotations

from typing import Any


class M2InputError(ValueError):
    """
    M2 输入闸门产生的结构化错误。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message
        self.details = (
            dict(details)
            if details is not None
            else {}
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": "0.1.0",
            "module": "M2",
            "status": "rejected",
            "error_code": self.code,
            "message": self.message,
        }

        if self.details:
            result["details"] = self.details

        return result


class M2PlanningError(M2InputError):
    """
    M2 规划文件生成或已有输出冲突错误。
    """

class M2ProviderError(M2InputError):
    """
    M2生成Provider选择、请求构造或提交错误。
    """