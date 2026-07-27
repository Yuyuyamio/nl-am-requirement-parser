from __future__ import annotations

import argparse
import json

from .providers.openrouter_provider import (
    OpenRouterProvider,
)
from .routing.router import route_request


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Route a natural-language request "
            "to creative or engineering 3D generation."
        )
    )

    parser.add_argument(
        "request",
        nargs="+",
        help="自然语言3D打印需求",
    )

    parser.add_argument(
        "--model",
        default=None,
        help=(
            "OpenRouter模型名称。"
            "未指定时读取OPENROUTER_MODEL。"
        ),
    )

    args = parser.parse_args()

    request_text = " ".join(
        args.request
    ).strip()

    try:
        provider = OpenRouterProvider(
            model=args.model
        )

        route = route_request(
            request_text,
            provider,
        )

    except (
        RuntimeError,
        ValueError,
    ) as error:
        print(f"任务路由失败：{error}")
        return 1

    print(
        json.dumps(
            route.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
    )

    if route.task_type == "creative_asset":
        print("\n下一模块：创意3D生成流程")

    elif route.task_type == "engineering_part":
        print("\n下一模块：工程需求解析流程")

    else:
        print("\n需要用户补充信息后再判断。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())