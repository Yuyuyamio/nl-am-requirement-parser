from __future__ import annotations

import argparse
import json
from pathlib import Path

from .creative.spec_builder import (
    build_creative_spec,
)
from .providers.openrouter_provider import (
    OpenRouterProvider,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a creative text-to-3D "
            "task specification."
        )
    )

    parser.add_argument(
        "request",
        nargs="+",
        help="创意3D生成或打印需求",
    )

    parser.add_argument(
        "--model",
        default=None,
        help="OpenRouter模型名称",
    )

    parser.add_argument(
        "--output",
        default="creative_asset_spec.json",
        help="输出JSON文件路径",
    )

    args = parser.parse_args()

    request_text = " ".join(
        args.request
    ).strip()

    try:
        provider = OpenRouterProvider(
            model=args.model
        )

        spec = build_creative_spec(
            request_text,
            provider,
        )

    except (
        RuntimeError,
        ValueError,
    ) as error:
        print(
            f"创意任务规格生成失败：{error}"
        )
        return 1

    data = spec.to_dict()

    output_path = Path(args.output)

    output_path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        f"\n已生成：{output_path.resolve()}"
    )

    if spec.needs_clarification:
        print(
            "需要确认："
            f"{spec.clarification_question}"
        )
    else:
        print(
            "创意3D任务规格已准备完成。"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())