from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gate6_offline_risk_v600 import Gate6V600Error, run


def main(argv=None):
    p = argparse.ArgumentParser(description="M4 Gate6A v6.0.0 offline telemetry risk validation")
    p.add_argument("--project-root", required=True)
    args = p.parse_args(argv)

    try:
        result = run(Path(args.project_root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "offline_risk_engine_validated" else 3
    except Gate6V600Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "6A",
            "version": "6.0.0",
            "status": "gate6a_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "6A",
            "version": "6.0.0",
            "status": "gate6a_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
