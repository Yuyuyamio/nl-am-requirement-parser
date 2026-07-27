from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .m1_contract import (
    ensure_valid_m1_manifest,
)
from .m1_pipeline import run_m1_pipeline
from .providers.openrouter_provider import (
    OpenRouterProvider,
)


def _write_json(
    path: Path,
    data: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M1 unified natural-language "
            "requirement processing pipeline."
        )
    )

    parser.add_argument(
        "request",
        nargs="+",
        help="自然语言增材制造需求",
    )

    parser.add_argument(
        "--model",
        default=None,
        help="OpenRouter模型名称",
    )

    parser.add_argument(
        "--output-dir",
        default="outputs/m1",
        help="M1输出目录",
    )

    args = parser.parse_args()

    request_text = " ".join(
        args.request
    ).strip()

    output_dir = Path(
        args.output_dir
    )

    try:
        provider = OpenRouterProvider(
            model=args.model
        )

        result = run_m1_pipeline(
            request_text,
            provider,
        )

        route_path = (
            output_dir
            / "task_route.json"
        )

        payload_path = (
            output_dir
            / result.output_filename
        )

        manifest_path = (
            output_dir
            / "m1_manifest.json"
        )

        manifest = {
            "schema_version": (
                result.schema_version
            ),
            "module": result.module,
            "original_input": request_text,
            "task_type": result.task_type,
            "status": result.status,
            "route_file": str(
                route_path.resolve()
            ),
            "output_file": str(
                payload_path.resolve()
            ),
            "next_module": (
                result.next_module
            ),
        }

        # 在写入文件前验证M1交付契约。
        ensure_valid_m1_manifest(
            manifest
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        _write_json(
            route_path,
            result.route,
        )

        _write_json(
            payload_path,
            result.payload,
        )

        _write_json(
            manifest_path,
            manifest,
        )

    except (
        RuntimeError,
        ValueError,
    ) as error:
        print(
            f"M1处理失败：{error}"
        )
        return 1

    print(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        "\nM1处理完成。"
    )

    print(
        f"路由结果：{route_path.resolve()}"
    )

    print(
        f"正式规格：{payload_path.resolve()}"
    )

    print(
        f"流程清单：{manifest_path.resolve()}"
    )

    if result.status in {
        "needs_clarification",
        "incomplete",
        "conflict",
    }:
        print(
            "\n当前规格尚未满足进入M2的条件。"
        )
    else:
        print(
            "\n当前规格已具备进入M2的条件。"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())