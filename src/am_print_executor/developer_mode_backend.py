from __future__ import annotations

import sys

from am_print_executor import (
    developer_mode_backend_v1120 as legacy,
)

from am_print_executor import (
    multimaterial_fixture,
)

from am_print_executor import (
    multimaterial_project,
)


def main(
    argv: list[str] | None = None,
) -> int:
    args = list(
        sys.argv[1:]
        if argv is None
        else argv
    )

    if (
        not args
        or args[0] in {
            "-h",
            "--help",
        }
    ):
        print(
            "NL-AM canonical M4 backend"
        )
        print()
        print(
            "Permanent commands:"
        )
        print(
            "  prepare-ams-fixture"
        )
        print(
            "  assemble-multimaterial-project"
        )
        print()
        print(
            "Compatibility alias:"
        )
        print(
            "  assemble-ams-project"
        )

        return 0

    command = args[0]

    if (
        command
        == "prepare-ams-fixture"
    ):
        return (
            multimaterial_fixture
            .cli_main(
                args[1:]
            )
        )

    if command in {
        "assemble-multimaterial-project",
        "assemble-ams-project",
    }:
        return (
            multimaterial_project
            .cli_main(
                args[1:]
            )
        )

    original = list(
        sys.argv
    )

    try:
        sys.argv = [
            original[0],
            *args,
        ]

        return legacy.main()

    except legacy.DeveloperBackendError as exc:
        print(
            f"\n[DEVELOPER BACKEND FAIL] {exc}",
            file=sys.stderr,
        )

        return 2

    except SystemExit as exc:
        return (
            int(exc.code)
            if isinstance(
                exc.code,
                int,
            )
            else 0
        )

    finally:
        sys.argv = original


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
