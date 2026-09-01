from __future__ import annotations

from typing import Any

import am_print_executor.developer_mode_backend_v1120 as backend
import am_print_executor.developer_mode_final_acceptance_v1120 as acceptance


def _normalize_remote_path(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    return "/" + value.lstrip("/")


_original_upload = backend._ftps_upload_verified


def _ftps_upload_v1070(*args: Any, **kwargs: Any) -> Any:
    result = _original_upload(*args, **kwargs)
    if isinstance(result, dict):
        result = dict(result)
        if "remote_path" in result:
            result["remote_path"] = _normalize_remote_path(result.get("remote_path"))
        if result.get("remote_directory") in ("", None, "//"):
            result["remote_directory"] = "/"
    return result


backend._ftps_upload_verified = _ftps_upload_v1070


if __name__ == "__main__":
    raise SystemExit(acceptance.main())