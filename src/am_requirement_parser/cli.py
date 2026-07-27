from __future__ import annotations

import argparse
import json
from pathlib import Path

from .parser import parse_requirement
from .schema_validator import validate_against_schema
from .validator import validate_spec


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Parse a Chinese additive-manufacturing "
            "requirement into structured JSON."
        )
    )

    parser.add_argument(
        "input_file",
        nargs="?",
        default="examples/input.txt",
        help="UTF-8 text file containing the requirement.",
    )

    parser.add_argument(
        "--output",
        default="requirement_spec.json",
        help="Output JSON path.",
    )

    args = parser.parse_args()

    input_path = Path(args.input_file)

    if not input_path.exists():
        raise FileNotFoundError(
            f"找不到输入文件：{input_path}"
        )

    text = input_path.read_text(encoding="utf-8")

    spec = parse_requirement(text)

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

    print(f"已生成：{output_path.resolve()}")
    print(f"Schema版本：{data['schema_version']}")
    print(f"状态：{data['status']}")

    print(
        f"缺失信息数量："
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

    if all_errors:
        print("\n验证错误：")

        for error in all_errors:
            print(f"- {error}")

    return 0 if not all_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())