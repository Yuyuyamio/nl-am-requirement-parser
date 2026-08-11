from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .gate4_runtime_v402 import (
    expected_start_phrase,
    run_gate4a_v402,
    run_gate4b_v402,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M4 Gate 4 v4.0.2 Paho ReasonCode compatibility fix")
    sub = parser.add_subparsers(dest="mode", required=True)
    p1 = sub.add_parser("preflight")
    p1.add_argument("--project-root", required=True)
    p2 = sub.add_parser("start")
    p2.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)

    try:
        access_code = getpass.getpass("X1C Developer Mode Access Code (hidden; not stored): ").strip()
        if args.mode == "preflight":
            result = run_gate4a_v402(Path(args.project_root), access_code)
        else:
            required = expected_start_phrase()
            print("FIRST REAL PRINT START GATE - v4.0.2")
            print("No automatic retry will occur after MQTT publish.")
            phrase = input(f"Type {required} to authorize exactly one print-start command: ").strip()
            result = run_gate4b_v402(Path(args.project_root), access_code, phrase)
        result["version"] = "4.0.2"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"runtime_preflight_passed", "first_print_started"} else 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 4,
            "version": "4.0.2",
            "status": "gate4_blocked",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
