from __future__ import annotations
import argparse
import getpass
import json
import sys
from pathlib import Path

from .gate4b_runtime_v411 import AUTH_PHRASE, Gate4BV411Error, run_gate4b_v411

def main(argv=None):
    p = argparse.ArgumentParser(description="M4 Gate4B v4.1.1 Developer Mode first real print")
    p.add_argument("--project-root", required=True)
    args = p.parse_args(argv)

    print("")
    print("WARNING: THIS STEP CAN START A REAL PHYSICAL PRINT.")
    print("Required physical checks:")
    print("  - You are beside the X1C.")
    print("  - Build plate is installed, clear, and ready.")
    print("  - External-spool PLA is loaded and ready.")
    print("  - Toolhead and bed are unobstructed.")
    print("  - LAN Only Mode and Developer Mode remain ON.")
    print("")
    print("Type exactly to authorize ONE print-start command:")
    print(AUTH_PHRASE)
    authorization = input("Authorization phrase: ").strip()

    if authorization != AUTH_PHRASE:
        print(json.dumps({
            "module": "M4", "phase": "4B", "version": "4.1.1",
            "status": "manual_authorization_not_granted",
            "print_start_command_count": 0,
        }, ensure_ascii=False, indent=2))
        return 2

    access_code = getpass.getpass("X1C Developer Mode Access Code (hidden; not stored): ").strip()

    try:
        result = run_gate4b_v411(Path(args.project_root), access_code, authorization)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "first_print_started" else 3
    except Gate4BV411Error as exc:
        print(json.dumps({
            "module": "M4", "phase": "4B", "version": "4.1.1",
            "status": "gate4b_blocked_or_failed", "error": str(exc)
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4", "phase": "4B", "version": "4.1.1",
            "status": "gate4b_failed", "error": f"{type(exc).__name__}: {exc}"
        }, ensure_ascii=False, indent=2))
        return 2

if __name__ == "__main__":
    sys.exit(main())
