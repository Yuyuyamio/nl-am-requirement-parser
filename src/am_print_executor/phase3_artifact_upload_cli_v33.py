from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .audited_artifact_upload_v33 import (
    Gate3V33Error,
    REQUEST_ID_DEFAULT,
    expected_authorization_phrase,
    run_gate3_v33,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="M4 Gate 3 v3.3: upload the Phase 5 audited gcode.3mf via the locked Gate 2 v3.2 FTPS transport."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--request-id", default=REQUEST_ID_DEFAULT)
    parser.add_argument("--remote-dir", default="/cache")
    args = parser.parse_args(argv)

    required = expected_authorization_phrase(args.request_id)
    print("Gate 3 will upload the already-audited .gcode.3mf to X1C FTPS only.")
    print("It will NOT publish MQTT commands and will NOT start printing.")
    authorization = input(f"Type {required} to authorize this artifact upload: ").strip()
    access_code = getpass.getpass(
        "X1C Developer Mode Access Code (hidden; not stored): "
    ).strip()

    try:
        result = run_gate3_v33(
            Path(args.project_root),
            args.request_id,
            access_code,
            authorization,
            remote_dir=args.remote_dir,
        )
    except Gate3V33Error as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 3,
            "stage": "audited_artifact_upload_v33",
            "request_id": args.request_id,
            "status": "artifact_upload_blocked",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 3,
            "stage": "audited_artifact_upload_v33",
            "request_id": args.request_id,
            "status": "artifact_upload_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
