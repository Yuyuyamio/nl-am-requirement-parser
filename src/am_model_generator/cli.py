from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .contracts import M2InputError
from .planning import plan_m2


def main(
    argv: Sequence[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an M1 handoff and "
            "create an M2 generation plan."
        )
    )

    parser.add_argument(
        "manifest",
        help=(
            "Path to outputs/m1/"
            "m1_manifest.json"
        ),
    )

    parser.add_argument(
        "--output-dir",
        default="outputs/m2",
        help=(
            "M2 output root directory. "
            "A request-specific directory "
            "will be created inside it."
        ),
    )

    args = parser.parse_args(
        argv
    )

    try:
        result = plan_m2(
            args.manifest,
            output_root=(
                args.output_dir
            ),
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
                        "M2规划发生未预期错误"
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