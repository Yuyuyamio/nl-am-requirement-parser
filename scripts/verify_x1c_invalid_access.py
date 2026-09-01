from __future__ import annotations

import argparse
import json
import secrets
import sys

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from am_print_executor.x1c_connection import (  # noqa: E402
    PrinterConnectionError,
    connect_printer,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that an X1C rejects a random in-memory access code."
    )
    parser.add_argument("--ip", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    invalid_access_code = secrets.token_urlsafe(32)
    try:
        connect_printer(
            ip=args.ip,
            access_code=invalid_access_code,
            device_id=args.device_id,
            timeout_seconds=args.timeout,
        )
    except PrinterConnectionError as exc:
        passed = exc.code == "authentication_failed"
        print(f"WRONG_ACCESS_CODE_GATE = {'PASS' if passed else 'FAIL'}")
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return 0 if passed else 2
    finally:
        invalid_access_code = ""

    print("WRONG_ACCESS_CODE_GATE = UNEXPECTED_PASS")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
