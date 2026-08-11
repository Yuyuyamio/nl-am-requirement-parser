from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .device_gate import DeviceInput, EXPECTED_REQUEST_ID, create_device_readiness
from .errors import M4Error


def _yes_no(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt} [Y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please enter Y or N.")


def _choice(prompt: str, choices: tuple[str, ...]) -> str:
    normalized = {item.lower(): item for item in choices}
    while True:
        answer = input(f"{prompt} ({'/'.join(choices)}): ").strip().lower()
        if answer in normalized:
            return normalized[answer]
        print(f"Please enter one of: {', '.join(choices)}")


def _find_final_acceptance(project_root: Path, request_id: str) -> Path:
    m3_root = project_root / "outputs" / "m3"
    candidates: list[Path] = []
    if m3_root.exists():
        for path in m3_root.rglob("m3_final_acceptance.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("request_id") == request_id and data.get("status") == "m3_accepted":
                candidates.append(path.resolve())
    if not candidates:
        raise M4Error("M4_PHASE0_ACCEPTANCE_NOT_FOUND", "No accepted M3 task matches the confirmed request ID.", details={"request_id": request_id})
    if len(candidates) > 1:
        raise M4Error("M4_PHASE0_MULTIPLE_ACCEPTANCE_REPORTS", "Multiple accepted M3 reports match the confirmed request ID.", details={"paths": [str(path) for path in candidates]})
    return candidates[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture the real X1C device identity and M4 execution gate.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--request-id", default=EXPECTED_REQUEST_ID)
    parser.add_argument("--printer-ip")
    parser.add_argument("--serial-number")
    parser.add_argument("--firmware-version")
    parser.add_argument("--material-source", choices=("external_spool", "ams"))
    parser.add_argument("--ams-present", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--network-mode", choices=("cloud", "lan_only"))
    parser.add_argument("--authorize-readonly-probe", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--non-interactive", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        project_root = args.project_root.resolve()
        final_acceptance_path = _find_final_acceptance(project_root, args.request_id)

        if args.non_interactive:
            required = {
                "printer_ip": args.printer_ip,
                "serial_number": args.serial_number,
                "firmware_version": args.firmware_version,
                "material_source": args.material_source,
                "ams_present": args.ams_present,
                "network_mode": args.network_mode,
                "authorize_readonly_probe": args.authorize_readonly_probe,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise M4Error("M4_PHASE0_MISSING_ARGUMENTS", "Non-interactive mode requires all device inputs.", details={"missing": missing})
            physical_device_observed = printer_idle = build_plate_installed = True
            build_plate_clean = chamber_clear = filament_loaded = True
            nozzle_matches_profile = material_matches_profile = True
        else:
            printer_ip = args.printer_ip or input("X1C IPv4 address shown on the printer: ").strip()
            serial_number = args.serial_number or input("X1C serial number shown in Device Info: ").strip()
            firmware_version = args.firmware_version or input("X1C firmware version shown in Device Info: ").strip()
            material_source = args.material_source or _choice("PLA source", ("external_spool", "ams"))
            ams_present = args.ams_present if args.ams_present is not None else _yes_no("Is AMS physically installed?")
            network_mode = args.network_mode or _choice("Current printer network mode", ("cloud", "lan_only"))
            physical_device_observed = _yes_no("Are you physically looking at this exact printer?")
            printer_idle = _yes_no("Is the printer idle with no active or queued print?")
            build_plate_installed = _yes_no("Is the intended build plate installed?")
            build_plate_clean = _yes_no("Is the build plate clean and undamaged?")
            chamber_clear = _yes_no("Is the chamber and toolhead travel area clear?")
            filament_loaded = _yes_no("Is PLA loaded from the selected source?")
            nozzle_matches_profile = _yes_no("Does the installed nozzle match 0.4 mm hardened steel?")
            material_matches_profile = _yes_no("Does the loaded material match Bambu PLA Basic/compatible PLA?")
            authorize_readonly_probe = args.authorize_readonly_probe if args.authorize_readonly_probe is not None else _yes_no(
                "Authorize the next phase to perform a read-only network connection probe?"
            )

        device_input = DeviceInput(
            printer_ip=args.printer_ip if args.non_interactive else printer_ip,
            serial_number=args.serial_number if args.non_interactive else serial_number,
            firmware_version=args.firmware_version if args.non_interactive else firmware_version,
            material_source=args.material_source if args.non_interactive else material_source,
            ams_present=args.ams_present if args.non_interactive else ams_present,
            network_mode=args.network_mode if args.non_interactive else network_mode,
            physical_device_observed=physical_device_observed,
            printer_idle=printer_idle,
            build_plate_installed=build_plate_installed,
            build_plate_clean=build_plate_clean,
            chamber_clear=chamber_clear,
            filament_loaded=filament_loaded,
            nozzle_matches_profile=nozzle_matches_profile,
            material_matches_profile=material_matches_profile,
            authorize_readonly_probe=args.authorize_readonly_probe if args.non_interactive else authorize_readonly_probe,
        )
        result = create_device_readiness(
            project_root=project_root,
            final_acceptance_path=final_acceptance_path,
            device_input=device_input,
            request_id=args.request_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except M4Error as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
