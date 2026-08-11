from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .gate4_runtime_v406 import Gate4V406Error, run_gate4a_v406


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="M4 Gate4A v4.0.6 FINISH-safe runtime preflight"
    )
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)

    access_code = getpass.getpass(
        "X1C Developer Mode Access Code (hidden; not stored): "
    ).strip()

    try:
        result = run_gate4a_v406(Path(args.project_root), access_code)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Gate4V406Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 4,
            "version": "4.0.6",
            "status": "gate4_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 4,
            "version": "4.0.6",
            "status": "gate4_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
