from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gate7b_profile_patch_contract_v710 import Gate7BV710Error, run


def main(argv=None):
    p = argparse.ArgumentParser(
        description="M4 Gate7B v7.1.0 profile patch contract validation"
    )
    p.add_argument("--project-root", required=True)
    args = p.parse_args(argv)

    try:
        result = run(Path(args.project_root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "profile_patch_contract_validated" else 3
    except Gate7BV710Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "7B",
            "version": "7.1.0",
            "status": "gate7b_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "7B",
            "version": "7.1.0",
            "status": "gate7b_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
