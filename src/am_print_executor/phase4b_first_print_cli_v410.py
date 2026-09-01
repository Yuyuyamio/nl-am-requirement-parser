from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .gate4b_runtime_v410 import AUTH_PHRASE, Gate4BV410Error, run_gate4b_v410


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="M4 Gate4B v4.1.0: one-shot manually authorized first real print start"
    )
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)

    print("")
    print("WARNING: Gate4B CAN START A REAL PHYSICAL PRINT.")
    print("Before continuing, physically verify:")
    print("  1) You are beside the X1C.")
    print("  2) The build plate is clear and correctly installed.")
    print("  3) External-spool PLA is loaded and ready; this run does NOT use AMS.")
    print("  4) Nothing obstructs the toolhead or bed.")
    print("")
    print("To authorize exactly ONE print-start publish, type exactly:")
    print(AUTH_PHRASE)
    print("")
    authorization = input("Authorization phrase: ").strip()

    if authorization != AUTH_PHRASE:
        print(json.dumps({
            "module": "M4",
            "phase": "4B",
            "version": "4.1.0",
            "status": "manual_authorization_not_granted",
            "print_start_command_count": 0,
        }, ensure_ascii=False, indent=2))
        return 2

    access_code = getpass.getpass(
        "X1C Developer Mode Access Code (hidden; not stored): "
    ).strip()

    try:
        result = run_gate4b_v410(
            Path(args.project_root),
            access_code,
            authorization,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "first_print_started" else 3
    except Gate4BV410Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "4B",
            "version": "4.1.0",
            "status": "gate4b_blocked_or_failed",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "4B",
            "version": "4.1.0",
            "status": "gate4b_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
