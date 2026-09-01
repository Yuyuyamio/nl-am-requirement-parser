from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Callable

from .audited_artifact_upload_v33 import (
    Gate3V33Error,
    _load_json,
    _resolve_recorded_path,
    _sha256_file,
    _validate_gate2_v32,
    _write_json_atomic,
    expected_authorization_phrase,
    upload_audited_artifact,
)
from .ftps_probe_v32 import (
    EXPECTED_DEVICE_ID,
    REMOTE_DIR,
    REQUEST_ID_DEFAULT,
    SessionReuseImplicitFTP_TLS,
    get_server_fingerprint,
    load_gate1_identity,
    validate_or_create_ftps_pin,
)

REPORT_NAME = "m4_gate3_audited_artifact_upload_v331.json"


class Gate3V331Error(Gate3V33Error):
    pass


def _formal_m3_task_dir(project_root: Path, request_id: str) -> Path:
    return project_root.resolve() / "outputs" / "m3" / request_id


def _validate_formal_final_acceptance(
    project_root: Path, request_id: str
) -> tuple[Path, dict[str, Any]]:
    path = _formal_m3_task_dir(project_root, request_id) / "m3_final_acceptance.json"
    report = _load_json(path)
    expected = {
        "module": "M3",
        "phase": 7,
        "stage": "final_acceptance",
        "request_id": request_id,
        "status": "m3_accepted",
        "m3_complete": True,
        "m4_activation_authorized": False,
        "hard_constraints_passed": True,
        "next_module": "M4",
    }
    mismatches = {
        key: {"expected": expected_value, "actual": report.get(key)}
        for key, expected_value in expected.items()
        if report.get(key) != expected_value
    }
    if mismatches:
        raise Gate3V331Error(
            f"Formal M3 final acceptance is not eligible: {mismatches}"
        )
    return path.resolve(), report


def _validate_formal_gcode_audit(
    project_root: Path, request_id: str
) -> tuple[Path, dict[str, Any], Path, str]:
    report_path = _formal_m3_task_dir(project_root, request_id) / "m3_gcode_audit.json"
    report = _load_json(report_path)
    expected = {
        "module": "M3",
        "phase": 5,
        "stage": "slice_and_gcode_audit",
        "request_id": request_id,
        "status": "audit_passed",
        "hard_constraints_passed": True,
        "hard_failure_count": 0,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": report.get(key)}
        for key, expected_value in expected.items()
        if report.get(key) != expected_value
    }
    if mismatches:
        raise Gate3V331Error(f"Formal Phase 5 G-code audit is not eligible: {mismatches}")

    artifact = report.get("artifact")
    if not isinstance(artifact, dict):
        raise Gate3V331Error("Formal Phase 5 audit artifact record is missing.")
    raw_path = artifact.get("path")
    expected_sha = artifact.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        raise Gate3V331Error("Formal Phase 5 audit artifact path is invalid.")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise Gate3V331Error("Formal Phase 5 audit artifact SHA-256 is invalid.")

    artifact_path = _resolve_recorded_path(raw_path, report_path=report_path)
    if not artifact_path.name.lower().endswith(".gcode.3mf"):
        raise Gate3V331Error("Only the formal Phase 5 audited .gcode.3mf may enter Gate 3.")
    actual_sha = _sha256_file(artifact_path)
    if actual_sha != expected_sha.lower():
        raise Gate3V331Error(
            f"Audited artifact SHA-256 changed. expected={expected_sha}, actual={actual_sha}"
        )
    if not zipfile.is_zipfile(artifact_path):
        raise Gate3V331Error("Audited artifact is no longer a valid 3MF ZIP container.")

    recorded_entries = artifact.get("gcode_entries")
    if not isinstance(recorded_entries, list) or not recorded_entries:
        raise Gate3V331Error("Formal Phase 5 audit has no recorded G-code entries.")
    with zipfile.ZipFile(artifact_path, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise Gate3V331Error(f"Audited 3MF ZIP integrity failed at {bad}.")
        names = set(archive.namelist())
    missing = [entry for entry in recorded_entries if entry not in names]
    if missing:
        raise Gate3V331Error(f"Audited G-code entries are missing from the 3MF: {missing}")

    return report_path.resolve(), report, artifact_path.resolve(), actual_sha


def _crosscheck_acceptance_audit_lock(
    final_acceptance_path: Path,
    final_acceptance: dict[str, Any],
    audit_path: Path,
) -> None:
    recorded_sha = final_acceptance.get("source_gcode_audit_sha256")
    if isinstance(recorded_sha, str) and len(recorded_sha) == 64:
        actual_sha = _sha256_file(audit_path)
        if actual_sha != recorded_sha.lower():
            raise Gate3V331Error(
                "Formal Phase 5 audit no longer matches the SHA locked by M3 final acceptance."
            )
    recorded_path = final_acceptance.get("source_gcode_audit")
    if isinstance(recorded_path, str) and recorded_path:
        resolved = _resolve_recorded_path(recorded_path, report_path=final_acceptance_path)
        if resolved != audit_path.resolve():
            raise Gate3V331Error(
                "M3 final acceptance points to a different Phase 5 audit than the formal task path."
            )


def run_gate3_v331(
    project_root: Path,
    request_id: str,
    access_code: str,
    authorization_phrase: str,
    *,
    remote_dir: str = REMOTE_DIR,
    confirmation_reader: Callable[[str], str] = input,
    ftp_factory=SessionReuseImplicitFTP_TLS,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    if request_id != REQUEST_ID_DEFAULT:
        raise Gate3V331Error(
            f"Gate 3 v3.3.1 is locked to request_id {REQUEST_ID_DEFAULT}; observed {request_id}."
        )
    required_phrase = expected_authorization_phrase(request_id)
    if authorization_phrase.strip() != required_phrase:
        raise Gate3V331Error(
            f"Explicit upload authorization not granted. Required phrase: {required_phrase}"
        )

    identity = load_gate1_identity(project_root, request_id)
    task_dir: Path = identity["task_dir"]
    if identity["device_id"] != EXPECTED_DEVICE_ID:
        raise Gate3V331Error("Locked X1C DEVICE_ID changed before Gate 3.")

    gate2 = _validate_gate2_v32(task_dir, request_id, identity["device_id"])
    final_acceptance_path, final_acceptance = _validate_formal_final_acceptance(
        project_root, request_id
    )
    audit_path, audit, artifact_path, artifact_sha = _validate_formal_gcode_audit(
        project_root, request_id
    )
    _crosscheck_acceptance_audit_lock(
        final_acceptance_path, final_acceptance, audit_path
    )

    report_path = task_dir / REPORT_NAME
    if report_path.exists():
        existing = _load_json(report_path)
        if (
            existing.get("request_id") == request_id
            and existing.get("status") == "audited_artifact_uploaded"
            and existing.get("artifact", {}).get("local_sha256") == artifact_sha
            and existing.get("device_id") == identity["device_id"]
        ):
            return existing | {
                "report_file": str(report_path),
                "reused_existing_report": True,
            }
        raise Gate3V331Error(
            f"Existing Gate 3 v3.3.1 report conflicts with current locked inputs: {report_path}"
        )

    fingerprint = get_server_fingerprint(identity["ip_address"])
    validate_or_create_ftps_pin(
        task_dir,
        ip_address=identity["ip_address"],
        device_id=identity["device_id"],
        fingerprint=fingerprint,
        confirmation_reader=confirmation_reader,
    )

    result = upload_audited_artifact(
        ip_address=identity["ip_address"],
        access_code=access_code,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha,
        request_id=request_id,
        remote_dir=remote_dir,
        ftp_factory=ftp_factory,
    )

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 3,
        "stage": "audited_artifact_upload_v331",
        "request_id": request_id,
        "status": "audited_artifact_uploaded",
        "printer_ip": identity["ip_address"],
        "device_id": identity["device_id"],
        "source_gate2": {
            "report_file": str(task_dir / "m4_gate2_ftps_probe_v32.json"),
            "status": gate2.get("status"),
            "attempt_count": gate2.get("attempt_count"),
            "success_count": gate2.get("success_count"),
        },
        "source_m3": {
            "selection_policy": "deterministic_formal_task_path",
            "formal_task_directory": str(_formal_m3_task_dir(project_root, request_id)),
            "final_acceptance_file": str(final_acceptance_path),
            "gcode_audit_file": str(audit_path),
            "gcode_audit_sha256": _sha256_file(audit_path),
            "gcode_audit_status": audit.get("status"),
        },
        "artifact": {
            "local_path": str(artifact_path),
            "local_sha256": artifact_sha,
            "local_size_bytes": artifact_path.stat().st_size,
            "remote_directory": result.remote_directory,
            "remote_name": result.remote_name,
            "remote_path": f"{result.remote_directory.rstrip('/')}/{result.remote_name}",
            "uploaded_new": result.uploaded_new,
            "reused_existing_remote": result.reused_existing_remote,
            "remote_size_verified": result.remote_size_verified,
            "remote_sha256_verified": result.remote_sha256_verified,
            "remote_sha256": result.remote_sha256,
            "remote_size_bytes": result.remote_size_bytes,
            "remote_retained": result.remote_retained,
        },
        "authorization": {
            "artifact_upload_authorized": True,
            "authorization_phrase_matched": True,
            "print_start_authorized": False,
        },
        "policy": {
            "audited_artifact_only": True,
            "formal_audit_path_only": True,
            "recursive_audit_discovery_disabled": True,
            "phase5_audit_pass_required": True,
            "access_code_stored": False,
            "mqtt_publish_count": 0,
            "control_command_count": 0,
            "print_start_command_count": 0,
            "printer_motion_command_count": 0,
            "heating_command_count": 0,
            "artifact_uploaded": True,
            "print_started": False,
        },
        "next_phase": "m4_gate4_runtime_preflight_and_manual_print_start",
    }
    _write_json_atomic(report_path, report)
    return report | {"report_file": str(report_path), "reused_existing_report": False}
