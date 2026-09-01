from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .readonly_probe import (
    DeviceIdentity,
    REQUEST_ID_DEFAULT,
    ReadonlyProbeError,
    run_readonly_probe,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Passive, read-only MQTT status probe for a Bambu X1C in Developer Mode."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--request-id", default=REQUEST_ID_DEFAULT)
    parser.add_argument("--ip")
    parser.add_argument("--serial")
    parser.add_argument("--firmware")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def _existing_identity(project_root: Path, request_id: str) -> tuple[str | None, str | None]:
    pin_path = project_root / "outputs" / "m4" / request_id / "m4_printer_tls_pin.json"
    if not pin_path.exists():
        return None, None
    try:
        data = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    return data.get("ip_address"), data.get("serial_number")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(args.project_root)
    saved_ip, saved_serial = _existing_identity(project_root, args.request_id)

    ip_address = args.ip or saved_ip or input("X1C private IPv4 address: ").strip()
    serial_number = args.serial or saved_serial or input("X1C serial number: ").strip()
    firmware = args.firmware
    if firmware is None and saved_ip is None:
        firmware = input("Firmware version shown on the X1C (optional): ").strip() or None

    access_code = getpass.getpass(
        "X1C LAN/Developer Mode Access Code (hidden; not stored): "
    ).strip()

    try:
        result = run_readonly_probe(
            project_root,
            args.request_id,
            DeviceIdentity(ip_address, serial_number, firmware),
            access_code,
            attempts=args.attempts,
            timeout_seconds=args.timeout,
        )
    except ReadonlyProbeError as exc:
        print(
            json.dumps(
                {
                    "module": "M4",
                    "phase": 1,
                    "status": "readonly_probe_blocked",
                    "request_id": args.request_id,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "readonly_probe_passed" else 2


if __name__ == "__main__":
    sys.exit(main())
