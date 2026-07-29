from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from .contracts import (
    M2PlanningError,
    build_m2_request,
    build_planned_m2_manifest,
    ensure_valid_m2_manifest,
    ensure_valid_m2_request,
    ensure_valid_m2_route,
)
from .intake import load_m1_input
from .routing import build_m2_route


ROUTE_FILENAME = "m2_route.json"
REQUEST_FILENAME = "m2_request.json"
MANIFEST_FILENAME = "m2_manifest.json"


def _write_json(
    path: Path,
    data: dict[str, Any],
) -> None:
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_json_object(
    path: Path,
) -> dict[str, Any]:
    if not path.is_file():
        raise M2PlanningError(
            "M2_OUTPUT_INCOMPLETE",
            "已有M2规划目录缺少必要文件",
            details={
                "missing_file": str(path),
            },
        )

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except json.JSONDecodeError as error:
        raise M2PlanningError(
            "M2_OUTPUT_INVALID_JSON",
            "已有M2规划文件不是合法JSON",
            details={
                "path": str(path),
                "line": error.lineno,
                "column": error.colno,
                "reason": error.msg,
            },
        ) from error
    except OSError as error:
        raise M2PlanningError(
            "M2_OUTPUT_READ_FAILED",
            "无法读取已有M2规划文件",
            details={
                "path": str(path),
                "reason": str(error),
            },
        ) from error

    if not isinstance(data, dict):
        raise M2PlanningError(
            "M2_OUTPUT_INVALID_OBJECT",
            "已有M2规划文件必须是JSON对象",
            details={
                "path": str(path),
                "actual_type": type(data).__name__,
            },
        )

    return data


def _safe_directory_name(
    request_id: str,
) -> str:
    """
    将 request_id 转换成安全目录名。

    request_id 本身不会被修改；
    这里只处理文件系统目录名称。
    """

    cleaned = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        request_id,
    ).strip("._")

    if (
        cleaned
        and cleaned == request_id
        and len(cleaned) <= 100
    ):
        return cleaned

    digest = hashlib.sha256(
        request_id.encode("utf-8")
    ).hexdigest()[:10].upper()

    prefix = (
        cleaned[:70]
        if cleaned
        else "request"
    )

    return f"{prefix}-{digest}"


def _assert_existing_plan_matches(
    output_directory: Path,
    expected_documents: dict[
        str,
        dict[str, Any],
    ],
) -> None:
    mismatches: list[str] = []

    for filename, expected_data in (
        expected_documents.items()
    ):
        path = (
            output_directory
            / filename
        )

        actual_data = _read_json_object(
            path
        )

        if actual_data != expected_data:
            mismatches.append(filename)

    if mismatches:
        raise M2PlanningError(
            "M2_OUTPUT_CONFLICT",
            "已有M2规划与当前M1规格不一致",
            details={
                "output_directory": str(
                    output_directory
                ),
                "conflicting_files": (
                    mismatches
                ),
            },
        )


def _result_data(
    *,
    request_id: str,
    output_directory: Path,
    reused_existing_plan: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M2",
        "status": "planned",
        "request_id": request_id,
        "output_directory": str(
            output_directory
        ),
        "route_file": str(
            output_directory
            / ROUTE_FILENAME
        ),
        "request_file": str(
            output_directory
            / REQUEST_FILENAME
        ),
        "manifest_file": str(
            output_directory
            / MANIFEST_FILENAME
        ),
        "reused_existing_plan": (
            reused_existing_plan
        ),
    }


def plan_m2(
    manifest_path: str | Path,
    *,
    output_root: str | Path = (
        "outputs/m2"
    ),
) -> dict[str, Any]:
    """
    从M1 Manifest生成M2规划文件。

    行为规则：
    1. 先完成所有内存构造与Schema验证；
    2. 再一次性写入临时目录；
    3. 最后将临时目录改名为正式目录；
    4. 相同任务重复执行时复用已有规划；
    5. 已有内容不同则拒绝覆盖。
    """

    loaded_input = load_m1_input(
        manifest_path
    )

    route = build_m2_route(
        loaded_input
    )

    request = build_m2_request(
        loaded_input,
        route,
    )

    output_root_path = Path(
        output_root
    ).expanduser().resolve()

    directory_name = (
        _safe_directory_name(
            route.request_id
        )
    )

    output_directory = (
        output_root_path
        / directory_name
    )

    manifest = (
        build_planned_m2_manifest(
            loaded_input,
            route,
            route_file=ROUTE_FILENAME,
            request_file=(
                REQUEST_FILENAME
            ),
        )
    )

    route_data = route.to_dict()
    request_data = request.to_dict()
    manifest_data = manifest.to_dict()

    ensure_valid_m2_route(
        route_data
    )

    ensure_valid_m2_request(
        request_data
    )

    ensure_valid_m2_manifest(
        manifest_data
    )

    expected_documents = {
        ROUTE_FILENAME: route_data,
        REQUEST_FILENAME: request_data,
        MANIFEST_FILENAME: manifest_data,
    }

    if output_directory.exists():
        if not output_directory.is_dir():
            raise M2PlanningError(
                "M2_OUTPUT_PATH_CONFLICT",
                "M2任务输出路径已存在且不是目录",
                details={
                    "path": str(
                        output_directory
                    ),
                },
            )

        _assert_existing_plan_matches(
            output_directory,
            expected_documents,
        )

        return _result_data(
            request_id=route.request_id,
            output_directory=(
                output_directory
            ),
            reused_existing_plan=True,
        )

    try:
        output_root_path.mkdir(
            parents=True,
            exist_ok=True,
        )
    except OSError as error:
        raise M2PlanningError(
            "M2_OUTPUT_ROOT_FAILED",
            "无法创建M2输出根目录",
            details={
                "path": str(
                    output_root_path
                ),
                "reason": str(error),
            },
        ) from error

    temporary_directory = (
        output_root_path
        / (
            f".{directory_name}.tmp-"
            f"{uuid.uuid4().hex}"
        )
    )

    try:
        temporary_directory.mkdir(
            parents=False,
            exist_ok=False,
        )

        for filename, document in (
            expected_documents.items()
        ):
            _write_json(
                temporary_directory
                / filename,
                document,
            )

        temporary_directory.rename(
            output_directory
        )

    except OSError as error:
        shutil.rmtree(
            temporary_directory,
            ignore_errors=True,
        )

        if output_directory.is_dir():
            _assert_existing_plan_matches(
                output_directory,
                expected_documents,
            )

            return _result_data(
                request_id=route.request_id,
                output_directory=(
                    output_directory
                ),
                reused_existing_plan=True,
            )

        raise M2PlanningError(
            "M2_OUTPUT_WRITE_FAILED",
            "无法写入M2规划文件",
            details={
                "output_directory": str(
                    output_directory
                ),
                "reason": str(error),
            },
        ) from error

    return _result_data(
        request_id=route.request_id,
        output_directory=(
            output_directory
        ),
        reused_existing_plan=False,
    )