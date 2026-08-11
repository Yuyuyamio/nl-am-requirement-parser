from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .gate4_runtime_v40 import Gate4V40Error, expected_start_phrase, run_gate4a, run_gate4b


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M4 Gate 4 v4.0 runtime preflight and first print start")
    sub = parser.add_subparsers(dest="mode", required=True)
    p1 = sub.add_parser("preflight")
    p1.add_argument("--project-root", required=True)
    p2 = sub.add_parser("start")
    p2.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)
    try:
        access_code = getpass.getpass("X1C Developer Mode Access Code (hidden; not stored): ").strip()
        if args.mode == "preflight":
            result = run_gate4a(Path(args.project_root), access_code)
        else:
            required = expected_start_phrase()
            print("FIRST REAL PRINT START GATE")
            print("No automatic retry will occur after MQTT publish.")
            print("Use external spool PLA for this first automated start; AMS is deliberately excluded from Gate 4 v4.0.")
            phrase = input(f"Type {required} to authorize exactly one project_file print-start command: ").strip()
            result = run_gate4b(Path(args.project_root), access_code, phrase)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        status = result.get("status")
        return 0 if status in {"runtime_preflight_passed", "first_print_started"} else 2
    except Gate4V40Error as exc:
        print(json.dumps({
            "module": "M4", "phase": 4, "status": "gate4_blocked", "error": str(exc)
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4", "phase": 4, "status": "gate4_failed",
            "error": f"{type(exc).__name__}: {exc}"
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
