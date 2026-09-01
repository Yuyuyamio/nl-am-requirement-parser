from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gate6_vision_contract_v610 import Gate6BV610Error, run


def main(argv=None):
    p = argparse.ArgumentParser(description="M4 Gate6B v6.1.0 offline vision interface validation")
    p.add_argument("--project-root", required=True)
    args = p.parse_args(argv)

    try:
        result = run(Path(args.project_root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "offline_vision_contract_validated" else 3
    except Gate6BV610Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "6B",
            "version": "6.1.0",
            "status": "gate6b_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "6B",
            "version": "6.1.0",
            "status": "gate6b_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
