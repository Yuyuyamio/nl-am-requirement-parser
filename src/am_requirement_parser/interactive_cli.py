from __future__ import annotations

import argparse
import json
from pathlib import Path

from .answer_applier import (
    apply_clarification_answer,
)
from .parser import parse_requirement
from .schema_validator import (
    validate_against_schema,
)
from .validator import validate_spec


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive additive-manufacturing "
            "requirement clarification."
        )
    )

    parser.add_argument(
        "input_file",
        nargs="?",
        default="examples/input.txt",
    )

    parser.add_argument(
        "--output",
        default="requirement_spec_interactive.json",
    )

    args = parser.parse_args()

    input_path = Path(args.input_file)

    if not input_path.exists():
        raise FileNotFoundError(
            f"找不到输入文件：{input_path}"
        )

    text = input_path.read_text(
        encoding="utf-8"
    )

    spec = parse_requirement(text)

    print("\n自然语言增材制造需求确认")
    print("=" * 40)

    while spec.clarification_questions:
        question = (
            spec.clarification_questions[0]
        )

        print(
            f"\n[{question['question_id']}] "
            f"{question['question']}"
        )

        print(
            f"优先级：{question['priority']}"
        )

        answer = input(
            "请输入回答，直接回车可暂时结束："
        ).strip()

        if not answer:
            print("已停止继续追问。")
            break

        try:
            spec = apply_clarification_answer(
                spec,
                question["question_id"],
                answer,
            )
        except ValueError as error:
            print(f"回答无效：{error}")
            continue

        print("回答已写入需求对象。")

    engineering_errors = validate_spec(spec)

    preliminary_data = spec.to_dict()
    schema_errors = validate_against_schema(
        preliminary_data
    )

    all_errors = [
        *[
            f"JSON Schema：{error}"
            for error in schema_errors
        ],
        *[
            f"工程规则：{error}"
            for error in engineering_errors
        ],
    ]

    spec.validation["schema_valid"] = (
        not schema_errors
    )
    spec.validation["errors"] = all_errors

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

    print("\n" + "=" * 40)
    print(f"最终状态：{data['status']}")
    print(
        "剩余缺失信息："
        f"{len(data['missing_information'])}"
    )
    print(
        "JSON Schema验证："
        + (
            "通过"
            if not schema_errors
            else "失败"
        )
    )
    print(
        "工程规则验证："
        + (
            "通过"
            if not engineering_errors
            else "失败"
        )
    )
    print(f"已生成：{output_path.resolve()}")

    return 0 if not all_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())