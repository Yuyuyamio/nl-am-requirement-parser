from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .gate4_runtime_v403 import run_gate4a_v403


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M4 Gate4A v4.0.3 runtime-only preflight")
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)
    try:
        access_code = getpass.getpass("X1C Developer Mode Access Code (hidden; not stored): ").strip()
        result = run_gate4a_v403(Path(args.project_root), access_code)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "runtime_preflight_passed" else 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 4,
            "version": "4.0.3",
            "status": "gate4_blocked",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
