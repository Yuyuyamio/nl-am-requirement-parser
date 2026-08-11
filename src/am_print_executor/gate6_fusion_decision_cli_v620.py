from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gate6_fusion_decision_v620 import Gate6CV620Error, run


def main(argv=None):
    p = argparse.ArgumentParser(
        description="M4 Gate6C v6.2.0 offline device+vision fusion decision validation"
    )
    p.add_argument("--project-root", required=True)
    args = p.parse_args(argv)

    try:
        result = run(Path(args.project_root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "offline_fusion_decision_validated" else 3
    except Gate6CV620Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "6C",
            "version": "6.2.0",
            "status": "gate6c_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "6C",
            "version": "6.2.0",
            "status": "gate6c_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
