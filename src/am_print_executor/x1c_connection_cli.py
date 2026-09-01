from __future__ import annotations

import argparse
import getpass
import json
import sys

from .x1c_connection import PrinterConnectionError, connect_printer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only X1C MQTT/TLS connection verification."
    )
    parser.add_argument("--ip")
    parser.add_argument("--device-id")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--attempts", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    access_code = ""
    try:
        ip_address = args.ip or input("Printer IP: ").strip()
        device_id = (
            args.device_id
            if args.device_id is not None
            else input("Device ID (Enter to auto-discover): ").strip() or None
        )
        access_code = getpass.getpass("Access Code: ").strip()
        status = connect_printer(
            ip=ip_address,
            access_code=access_code,
            device_id=device_id,
            timeout_seconds=args.timeout,
            attempts=args.attempts,
        )
    except (EOFError, KeyboardInterrupt):
        print("\nX1C_CONNECTION_GATE = CANCELLED")
        return 130
    except PrinterConnectionError as exc:
        print("X1C_CONNECTION_GATE = FAIL")
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return 2
    finally:
        access_code = ""

    print("X1C_CONNECTION_GATE = PASS")
    print(f"IP = {status.printer_ip}")
    print(f"Device ID = {status.device_id}")
    print(f"transport = {status.transport}")
    print(f"connection elapsed = {status.elapsed_seconds:.3f}s")
    print(
        "printer status observed = "
        + str(status.printer_state_observed).lower()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
