from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gate8_offline_end_to_end_v800 import Gate8V800Error, run


def main(argv=None):
    p = argparse.ArgumentParser(
        description="M4 Gate8 v8.0.0 office offline end-to-end validation"
    )
    p.add_argument("--project-root", required=True)
    args = p.parse_args(argv)

    try:
        result = run(Path(args.project_root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "office_offline_end_to_end_validated" else 3
    except Gate8V800Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 8,
            "version": "8.0.0",
            "status": "gate8_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 8,
            "version": "8.0.0",
            "status": "gate8_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
