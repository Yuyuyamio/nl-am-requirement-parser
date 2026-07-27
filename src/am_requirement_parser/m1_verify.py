from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .creative.schema import (
    CREATIVE_ASSET_SCHEMA,
)
from .m1_contract import (
    load_m1_manifest_schema,
    validate_m1_manifest,
)
from .schema_validator import (
    validate_against_schema,
)


PROJECT_ROOT = (
    Path(__file__).resolve().parents[2]
)


REQUIRED_FILES = [
    "pyproject.toml",
    "schemas/requirement_spec.schema.json",
    "schemas/m1_manifest.schema.json",
    "src/am_requirement_parser/main_cli.py",
    "src/am_requirement_parser/m1_pipeline.py",
    "src/am_requirement_parser/m1_contract.py",
    "src/am_requirement_parser/routing/router.py",
    "src/am_requirement_parser/creative/spec_builder.py",
    "src/am_requirement_parser/providers/openrouter_provider.py",
]


def _load_json(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(
            f"文件不存在：{path}"
        )

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except json.JSONDecodeError as error:
        raise ValueError(
            f"文件不是合法JSON：{path}"
        ) from error
    except OSError as error:
        raise ValueError(
            f"无法读取文件：{path}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError(
            f"JSON根节点必须是对象：{path}"
        )

    return data


def _make_check(
    *,
    name: str,
    passed: bool,
    details: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": (
            "passed"
            if passed
            else "failed"
        ),
        "details": details,
    }


def _check_required_files() -> dict[str, Any]:
    missing: list[str] = []

    for relative_path in REQUIRED_FILES:
        path = (
            PROJECT_ROOT
            / relative_path
        )

        if not path.exists():
            missing.append(relative_path)

    if missing:
        return _make_check(
            name="required_files",
            passed=False,
            details=(
                "缺少文件："
                + ", ".join(missing)
            ),
        )

    return _make_check(
        name="required_files",
        passed=True,
        details=(
            f"全部{len(REQUIRED_FILES)}个"
            "M1核心文件存在"
        ),
    )


def _check_schemas() -> dict[str, Any]:
    try:
        load_m1_manifest_schema()

        Draft202012Validator.check_schema(
            CREATIVE_ASSET_SCHEMA
        )

        requirement_schema_path = (
            PROJECT_ROOT
            / "schemas"
            / "requirement_spec.schema.json"
        )

        requirement_schema = _load_json(
            requirement_schema_path
        )

        Draft202012Validator.check_schema(
            requirement_schema
        )

    except Exception as error:
        return _make_check(
            name="schema_integrity",
            passed=False,
            details=str(error),
        )

    return _make_check(
        name="schema_integrity",
        passed=True,
        details=(
            "工程需求Schema、创意规格Schema和"
            "M1 Manifest Schema均合法"
        ),
    )


def _run_unit_tests() -> dict[str, Any]:
    environment = dict(os.environ)

    source_path = str(
        PROJECT_ROOT / "src"
    )

    existing_python_path = (
        environment.get(
            "PYTHONPATH",
            "",
        )
    )

    if existing_python_path:
        environment["PYTHONPATH"] = (
            source_path
            + os.pathsep
            + existing_python_path
        )
    else:
        environment["PYTHONPATH"] = (
            source_path
        )

    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
    ]

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    combined_output = (
        result.stdout
        + "\n"
        + result.stderr
    )

    test_count_match = re.search(
        r"Ran\s+(\d+)\s+tests?",
        combined_output,
    )

    test_count = (
        int(test_count_match.group(1))
        if test_count_match
        else None
    )

    if result.returncode != 0:
        output_lines = (
            combined_output
            .strip()
            .splitlines()
        )

        preview = "\n".join(
            output_lines[-40:]
        )

        return _make_check(
            name="unit_tests",
            passed=False,
            details=(
                "单元测试未全部通过。\n"
                + preview
            ),
        )

    if test_count is None:
        details = (
            "所有单元测试通过，"
            "但未识别测试数量"
        )
    else:
        details = (
            f"全部{test_count}个"
            "单元测试通过"
        )

    return _make_check(
        name="unit_tests",
        passed=True,
        details=details,
    )


def _validate_creative_payload(
    payload: dict[str, Any],
) -> list[str]:
    validator = Draft202012Validator(
        CREATIVE_ASSET_SCHEMA
    )

    schema_errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    errors = [
        error.message
        for error in schema_errors
    ]

    if payload.get("task_type") != (
        "creative_asset"
    ):
        errors.append(
            "task_type不是creative_asset"
        )

    if payload.get(
        "needs_clarification"
    ) is not False:
        errors.append(
            "创意验收规格仍需要澄清"
        )

    if payload.get(
        "target_height_mm"
    ) is None:
        errors.append(
            "创意打印规格缺少目标高度"
        )

    prompt = payload.get(
        "generation_prompt_en"
    )

    if (
        not isinstance(prompt, str)
        or not prompt.strip()
    ):
        errors.append(
            "generation_prompt_en为空"
        )

    return errors


def _check_acceptance_case(
    *,
    case_name: str,
    directory: Path,
    expected_task_type: str,
    expected_status: str,
    expected_next_module: str | None,
    expected_output_name: str,
) -> dict[str, Any]:
    try:
        manifest_path = (
            directory
            / "m1_manifest.json"
        )

        manifest = _load_json(
            manifest_path
        )

        manifest_errors = (
            validate_m1_manifest(
                manifest
            )
        )

        if manifest_errors:
            raise ValueError(
                "Manifest不合法：\n"
                + "\n".join(
                    manifest_errors
                )
            )

        if manifest.get(
            "task_type"
        ) != expected_task_type:
            raise ValueError(
                "task_type不符合预期："
                f"{manifest.get('task_type')}"
            )

        if manifest.get(
            "status"
        ) != expected_status:
            raise ValueError(
                "status不符合预期："
                f"{manifest.get('status')}"
            )

        if manifest.get(
            "next_module"
        ) != expected_next_module:
            raise ValueError(
                "next_module不符合预期："
                f"{manifest.get('next_module')}"
            )

        route_path = Path(
            str(manifest["route_file"])
        )

        output_path = Path(
            str(manifest["output_file"])
        )

        if not route_path.exists():
            raise ValueError(
                f"路由文件不存在：{route_path}"
            )

        if not output_path.exists():
            raise ValueError(
                f"正式规格不存在：{output_path}"
            )

        if output_path.name != (
            expected_output_name
        ):
            raise ValueError(
                "正式规格文件名不符合预期："
                f"{output_path.name}"
            )

        route_data = _load_json(
            route_path
        )

        if route_data.get(
            "task_type"
        ) != expected_task_type:
            raise ValueError(
                "路由结果与Manifest不一致"
            )

        payload = _load_json(
            output_path
        )

        if expected_task_type == (
            "creative_asset"
        ):
            payload_errors = (
                _validate_creative_payload(
                    payload
                )
            )
        else:
            payload_errors = (
                validate_against_schema(
                    payload
                )
            )

            if payload.get(
                "status"
            ) != expected_status:
                payload_errors.append(
                    "工程规格status与"
                    "Manifest不一致"
                )

        if payload_errors:
            raise ValueError(
                "正式规格验证失败：\n"
                + "\n".join(
                    payload_errors
                )
            )

    except Exception as error:
        return _make_check(
            name=case_name,
            passed=False,
            details=str(error),
        )

    return _make_check(
        name=case_name,
        passed=True,
        details=(
            f"{expected_task_type}验收案例通过；"
            f"status={expected_status}；"
            f"next_module={expected_next_module}"
        ),
    )


def _build_markdown_report(
    report: dict[str, Any],
) -> str:
    lines = [
        "# M1 模块验收报告",
        "",
        f"- 模块：`{report['module']}`",
        (
            "- 验收状态："
            f"`{report['verification_status']}`"
        ),
        (
            "- 生成时间（UTC）："
            f"`{report['generated_at_utc']}`"
        ),
        "",
        "## 验收检查",
        "",
        "| 检查项 | 状态 | 说明 |",
        "|---|---|---|",
    ]

    for check in report["checks"]:
        details = str(
            check["details"]
        ).replace(
            "\n",
            "<br>",
        )

        lines.append(
            "| "
            f"{check['name']} | "
            f"{check['status']} | "
            f"{details} |"
        )

    lines.extend(
        [
            "",
            "## M1 输出边界",
            "",
            (
                "M1负责将自然语言需求转换为"
                "经过验证的结构化任务规格。"
            ),
            "",
            "- 创意需求输出："
            "`creative_asset_spec.json`",
            "- 工程需求输出："
            "`requirement_spec.json`",
            "- 路由结果："
            "`task_route.json`",
            "- 流程契约："
            "`m1_manifest.json`",
            "",
            (
                "只有当Manifest中的"
                "`next_module`为`M2`时，"
                "后续三维模型生成模块才允许启动。"
            ),
            "",
            "## 结论",
            "",
        ]
    )

    if report[
        "verification_status"
    ] == "passed":
        lines.append(
            "**M1需求输入与语义解析模块验收通过。**"
        )
    else:
        lines.append(
            "**M1验收未通过，"
            "不得进入M2。**"
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the final M1 acceptance "
            "verification."
        )
    )

    parser.add_argument(
        "--dog-dir",
        default=(
            "outputs/m1/acceptance_dog"
        ),
        help="创意需求验收输出目录",
    )

    parser.add_argument(
        "--bracket-dir",
        default=(
            "outputs/m1/acceptance_bracket"
        ),
        help="工程需求验收输出目录",
    )

    parser.add_argument(
        "--report-json",
        default=(
            "outputs/m1/"
            "m1_acceptance_report.json"
        ),
        help="JSON验收报告路径",
    )

    parser.add_argument(
        "--report-md",
        default=(
            "docs/M1_ACCEPTANCE_REPORT.md"
        ),
        help="Markdown验收报告路径",
    )

    args = parser.parse_args()

    dog_directory = (
        PROJECT_ROOT
        / args.dog_dir
    ).resolve()

    bracket_directory = (
        PROJECT_ROOT
        / args.bracket_dir
    ).resolve()

    checks = [
        _check_required_files(),
        _check_schemas(),
        _run_unit_tests(),
        _check_acceptance_case(
            case_name=(
                "creative_acceptance_case"
            ),
            directory=dog_directory,
            expected_task_type=(
                "creative_asset"
            ),
            expected_status="ready",
            expected_next_module="M2",
            expected_output_name=(
                "creative_asset_spec.json"
            ),
        ),
        _check_acceptance_case(
            case_name=(
                "engineering_acceptance_case"
            ),
            directory=bracket_directory,
            expected_task_type=(
                "engineering_part"
            ),
            expected_status="incomplete",
            expected_next_module=None,
            expected_output_name=(
                "requirement_spec.json"
            ),
        ),
    ]

    passed = all(
        check["status"] == "passed"
        for check in checks
    )

    report = {
        "schema_version": "0.1.0",
        "module": "M1",
        "verification_status": (
            "passed"
            if passed
            else "failed"
        ),
        "generated_at_utc": (
            datetime.now(timezone.utc)
            .isoformat()
        ),
        "checks": checks,
    }

    report_json_path = (
        PROJECT_ROOT
        / args.report_json
    ).resolve()

    report_md_path = (
        PROJECT_ROOT
        / args.report_md
    ).resolve()

    report_json_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_md_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_json_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report_md_path.write_text(
        _build_markdown_report(
            report
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        "\nJSON验收报告："
        f"{report_json_path}"
    )

    print(
        "Markdown验收报告："
        f"{report_md_path}"
    )

    if passed:
        print(
            "\nM1最终验收通过。"
        )
        return 0

    print(
        "\nM1最终验收失败，"
        "请查看failed检查项。"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())