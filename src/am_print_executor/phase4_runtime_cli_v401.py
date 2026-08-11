from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .gate4_runtime_v401 import expected_start_phrase, run_gate4a_v401, run_gate4b_v401


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M4 Gate 4 v4.0.1 timeout-safe runtime preflight")
    sub = parser.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("preflight")
    a.add_argument("--project-root", required=True)
    b = sub.add_parser("start")
    b.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)
    try:
        code = getpass.getpass("X1C Developer Mode Access Code (hidden; not stored): ").strip()
        if args.mode == "preflight":
            result = run_gate4a_v401(Path(args.project_root), code)
        else:
            phrase_required = expected_start_phrase()
            print("FIRST REAL PRINT START GATE v4.0.1")
            print("Do not continue unless Gate4A v4.0.1 passed and you are physically at the printer.")
            phrase = input(f"Type {phrase_required} to authorize exactly one print start: ").strip()
            result = run_gate4b_v401(Path(args.project_root), code, phrase)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"runtime_preflight_passed", "first_print_started"} else 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4", "phase": 4, "version": "4.0.1",
            "status": "gate4_blocked", "error": f"{type(exc).__name__}: {exc}"
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
