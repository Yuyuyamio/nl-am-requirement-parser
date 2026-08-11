from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

from .ftps_probe_v32 import load_gate1_identity
from .gate4_runtime_v404 import (
    REQUEST_ID,
    EXPECTED_DEVICE_ID,
    _runtime_status_v404,
    _gate3_lock,
)
from .gate4_runtime_v40 import _preflight_checks


def main() -> int:
    project_root = Path(sys.argv[1]).resolve()
    profile = load_gate1_identity(project_root, REQUEST_ID)

    if profile.get("device_id") != EXPECTED_DEVICE_ID:
        print(json.dumps({
            "module": "M4",
            "phase": 4,
            "version": "4.0.5",
            "status": "state_inspect_blocked",
            "error": "Gate1 locked DEVICE_ID mismatch"
        }, ensure_ascii=False, indent=2))
        return 2

    ip_address = profile.get("ip_address")
    access_code = getpass.getpass(
        "X1C Developer Mode Access Code (hidden; not stored): "
    ).strip()

    try:
        lock = _gate3_lock(project_root)
        print_obj, telemetry = _runtime_status_v404(
            ip_address=ip_address,
            access_code=access_code,
        )
        runtime = _preflight_checks(print_obj)

        observed = runtime.get("observed", {})
        result = {
            "module": "M4",
            "phase": 4,
            "version": "4.0.5",
            "stage": "runtime_state_inspection_only",
            "request_id": REQUEST_ID,
            "device_id": EXPECTED_DEVICE_ID,
            "status": "state_inspection_complete",
            "runtime_passed_under_v40_rules": runtime.get("passed"),
            "checks": runtime.get("checks"),
            "observed": observed,
            "telemetry": telemetry,
            "artifact_integrity_lock": {
                "gate3_version_lock": lock.get("gate3_version_lock"),
                "remote_sha256_verified": lock.get("remote_sha256_verified"),
                "remote_retained": lock.get("remote_retained"),
                "ftps_connection_attempted": False,
            },
            "policy": {
                "diagnostic_only": True,
                "ftps_connection_attempted": False,
                "artifact_reuploaded": False,
                "print_start_command_count": 0,
                "heating_command_count": 0,
                "motion_command_count": 0,
                "print_started": False,
                "access_code_stored": False
            }
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({
            "module": "M4",
            "phase": 4,
            "version": "4.0.5",
            "status": "state_inspect_failed",
            "error": f"{type(exc).__name__}: {exc}"
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
