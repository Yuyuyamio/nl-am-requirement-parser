from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gate7_optimization_recommendation_v700 import Gate7V700Error, run


def main(argv=None):
    p = argparse.ArgumentParser(
        description="M4 Gate7 v7.0.0 offline optimization recommendation validation"
    )
    p.add_argument("--project-root", required=True)
    args = p.parse_args(argv)

    try:
        result = run(Path(args.project_root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "offline_optimization_recommendation_validated" else 3
    except Gate7V700Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 7,
            "version": "7.0.0",
            "status": "gate7_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 7,
            "version": "7.0.0",
            "status": "gate7_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
