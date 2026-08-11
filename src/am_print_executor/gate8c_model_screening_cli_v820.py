from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .gate8c_model_screening_v820 import (
    DEFAULT_BASE_URL,
    Gate8CV820Error,
    run,
)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="M4 Gate8C v8.2.0 labeled AI screening + multiframe replay"
    )
    p.add_argument("--project-root", required=True)
    p.add_argument("--success-dir", required=True)
    p.add_argument("--failure-dir", required=True)
    p.add_argument("--sequence-manifest")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = p.parse_args(argv)

    token = os.environ.get("PRINTGUARD_READ_TOKEN") or None

    try:
        result = run(
            Path(args.project_root),
            Path(args.success_dir),
            Path(args.failure_dir),
            sequence_manifest=(
                Path(args.sequence_manifest)
                if args.sequence_manifest
                else None
            ),
            base_url=args.base_url,
            token=token,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Gate8CV820Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "8C",
            "version": "8.2.0",
            "status": "gate8c_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": "8C",
            "version": "8.2.0",
            "status": "gate8c_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
