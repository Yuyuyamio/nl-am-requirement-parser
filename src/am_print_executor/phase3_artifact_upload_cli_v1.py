from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .audited_artifact_upload_v1 import Gate3Error, REQUEST_ID_DEFAULT, run_gate3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upload the exact M3-audited .gcode.3mf by implicit FTPS and verify by download SHA-256. Does not start printing."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--request-id", default=REQUEST_ID_DEFAULT)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    access_code = getpass.getpass("X1C Developer Mode Access Code (hidden; not stored): ").strip()
    try:
        result = run_gate3(Path(args.project_root), args.request_id, access_code, timeout=args.timeout)
    except Gate3Error as exc:
        print(json.dumps({
            "schema_version": "0.1.0",
            "module": "M4",
            "phase": 3,
            "status": "audited_artifact_upload_blocked",
            "request_id": args.request_id,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "schema_version": "0.1.0",
            "module": "M4",
            "phase": 3,
            "status": "audited_artifact_upload_failed",
            "request_id": args.request_id,
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
