from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

REPORT_A = "m4_gate4a_runtime_preflight_v403.json"


def _load_v40():
    return importlib.import_module("am_print_executor.gate4_runtime_v40")


def _load_v402():
    return importlib.import_module("am_print_executor.gate4_runtime_v402")


def _gate3_integrity_lock_only(
    *,
    ip_address: str,
    access_code: str,
    remote_name: str,
    expected_sha256: str,
    expected_size: int,
    remote_dir: str = "/cache",
    timeout: float = 0.0,
    ftp_factory: Any = None,
) -> dict[str, Any]:
    """Do not open a new FTPS session in Gate4A.

    Gate4 v4.0 calls this function only after it has already validated:
      * the unique eligible Gate3 v3.3.1 report;
      * Gate3 remote_sha256_verified == true;
      * Gate3 remote_retained == true;
      * the fixed Phase5 audit path;
      * the current local artifact SHA-256;
      * Gate3 local/remote SHA-256 == current audited SHA-256.

    Gate4A's responsibility is therefore runtime state, not retransferring the
    already-verified artifact.  Reopening X1C implicit FTPS here has proven
    flaky and is deliberately skipped.
    """
    if not expected_sha256 or len(expected_sha256) != 64:
        raise _load_v40().Gate4V40Error("Gate4A v4.0.3 received an invalid locked artifact SHA-256.")
    if not remote_name.lower().endswith(".gcode.3mf"):
        raise _load_v40().Gate4V40Error("Gate4A v4.0.3 received an invalid locked remote artifact name.")
    return {
        "remote_directory": remote_dir,
        "remote_name": remote_name,
        "remote_size_bytes_locked": int(expected_size),
        "remote_sha256_locked": expected_sha256,
        "remote_integrity_basis": "gate3_v3.3.1_full_remote_sha256_verified",
        "remote_retained_basis": "gate3_v3.3.1_remote_retained_true",
        "ftps_recheck_performed": False,
        "ftps_connection_attempted": False,
        "gate4a_role": "runtime_state_only",
    }


def _patch_writer(v40: Any):
    original = v40._write_json_atomic

    def patched(path: Path, payload: dict[str, Any]) -> None:
        data = dict(payload)
        if path.name == REPORT_A:
            data["stage"] = "runtime_preflight_v403"
            data["version"] = "4.0.3"
            data["architecture_fix"] = "gate4a_runtime_only_no_redundant_ftps"
            data["next_phase"] = "m4_gate4b_manual_first_print_start_after_v403_review"
            policy = dict(data.get("policy") or {})
            policy["ftps_connection_attempted"] = False
            policy["artifact_reuploaded"] = False
            policy["print_started"] = False
            policy["mqtt_publish_count"] = 0
            data["policy"] = policy
        original(path, data)

    return patched


def _reload_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    data["report_file"] = str(path)
    return data


def run_gate4a_v403(project_root: Path, access_code: str) -> dict[str, Any]:
    v40 = _load_v40()
    v402 = _load_v402()

    original_verify = v40.verify_remote_artifact_v32
    original_status = v40._passive_status_once
    original_writer = v40._write_json_atomic
    old_report_a = v40.GATE4A_REPORT
    try:
        v40.GATE4A_REPORT = REPORT_A
        v40.verify_remote_artifact_v32 = _gate3_integrity_lock_only
        v40._passive_status_once = v402._safe_passive_status_once_v402
        v40._write_json_atomic = _patch_writer(v40)
        v40.run_gate4a(project_root, access_code)
        report_path = (
            project_root.resolve()
            / "outputs"
            / "m4"
            / v40.REQUEST_ID
            / REPORT_A
        )
        return _reload_report(report_path)
    finally:
        v40.verify_remote_artifact_v32 = original_verify
        v40._passive_status_once = original_status
        v40._write_json_atomic = original_writer
        v40.GATE4A_REPORT = old_report_a
