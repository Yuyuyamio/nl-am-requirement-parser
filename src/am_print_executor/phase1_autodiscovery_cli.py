from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .readonly_probe_autodiscovery import REQUEST_ID_DEFAULT, ProbeError, run_probe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Passive X1C MQTT DEVICE_ID autodiscovery. No serial number required.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--request-id", default=REQUEST_ID_DEFAULT)
    parser.add_argument("--ip")
    parser.add_argument("--attempts", type=int, default=1)
    args = parser.parse_args(argv)

    ip_address = args.ip or input("X1C private IPv4 address: ").strip()
    access_code = getpass.getpass("X1C Developer Mode Access Code (hidden; not stored): ").strip()

    try:
        result = run_probe(Path(args.project_root), args.request_id, ip_address, access_code, attempts=args.attempts)
    except ProbeError as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 1,
            "status": "readonly_probe_blocked",
            "request_id": args.request_id,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "readonly_probe_passed" else 2


if __name__ == "__main__":
    sys.exit(main())
