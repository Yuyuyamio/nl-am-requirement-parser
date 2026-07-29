from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .contracts import M2InputError
from .submission import submit_m2_plan


def main(
    argv: Sequence[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Submit an existing M2 plan "
            "to a generation provider."
        )
    )

    parser.add_argument(
        "task_directory",
        help=(
            "M2 request-specific task directory."
        ),
    )

    parser.add_argument(
        "--provider",
        default="mock",
        help=(
            "Generation provider name. "
            "Current safe option: mock."
        ),
    )

    args = parser.parse_args(
        argv
    )

    try:
        result = submit_m2_plan(
            args.task_directory,
            provider_name=args.provider,
        )

    except M2InputError as error:
        print(
            json.dumps(
                error.to_dict(),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    except Exception as error:
        print(
            json.dumps(
                {
                    "schema_version": (
                        "0.1.0"
                    ),
                    "module": "M2",
                    "status": "failed",
                    "error_code": (
                        "M2_INTERNAL_ERROR"
                    ),
                    "message": (
                        "M2 Provider提交发生"
                        "未预期错误"
                    ),
                    "details": {
                        "error_type": (
                            type(error).__name__
                        ),
                        "reason": str(error),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())