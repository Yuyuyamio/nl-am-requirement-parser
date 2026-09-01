from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .gate8b_printguard_adapter_v810 import (
    DEFAULT_BASE_URL,
    Gate8BV810Error,
    run,
)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="M4 Gate8B v8.1.0 real local AI inference validation"
    )
    p.add_argument("--project-root", required=True)
    p.add_argument("--image", action="append", required=True)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = p.parse_args(argv)

    token = os.environ.get("PRINTGUARD_READ_TOKEN") or None

    try:
        result = run(
            Path(args.project_root),
            [Path(x) for x in args.image],
            base_url=args.base_url,
            token=token,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Gate8BV810Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "8B",
            "version": "8.1.0",
            "status": "gate8b_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "8B",
            "version": "8.1.0",
            "status": "gate8b_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
