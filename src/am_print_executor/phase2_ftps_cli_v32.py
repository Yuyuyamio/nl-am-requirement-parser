from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .ftps_probe_v32 import Gate2V32Error, REQUEST_ID_DEFAULT, run_gate2_v32


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M4 Gate 2 v3.2 FTPS canary probe with TLS session reuse.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--request-id", default=REQUEST_ID_DEFAULT)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--remote-dir", default="/cache")
    args = parser.parse_args(argv)

    access_code = getpass.getpass(
        "X1C Developer Mode Access Code (hidden; not stored): "
    ).strip()

    try:
        result = run_gate2_v32(
            Path(args.project_root),
            args.request_id,
            access_code,
            attempts=args.attempts,
            remote_dir=args.remote_dir,
        )
    except Gate2V32Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 2,
            "stage": "developer_mode_ftps_canary_probe_v32",
            "request_id": args.request_id,
            "status": "ftps_probe_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ftps_probe_passed" else 2


if __name__ == "__main__":
    sys.exit(main())
