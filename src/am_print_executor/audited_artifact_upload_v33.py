from __future__ import annotations

import hashlib
import io
import json
import os
import ssl
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .ftps_probe_v32 import (
    EXPECTED_DEVICE_ID,
    FTPS_PORT,
    FTPS_USERNAME,
    REMOTE_DIR,
    REQUEST_ID_DEFAULT,
    SessionReuseImplicitFTP_TLS,
    get_server_fingerprint,
    load_gate1_identity,
    validate_or_create_ftps_pin,
)

REPORT_NAME = "m4_gate3_audited_artifact_upload_v33.json"
AUTH_PREFIX = "UPLOAD_AUDITED_"


class Gate3V33Error(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate3V33Error(f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate3V33Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Gate3V33Error(f"JSON root must be an object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise Gate3V33Error(f"Required file does not exist: {path}") from exc
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8-sig")
        if existing == text:
            return
        raise Gate3V33Error(f"Refusing to overwrite a different report: {path}")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _resolve_recorded_path(raw_path: str, *, report_path: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (report_path.parent / candidate).resolve()


def _find_unique_json(
    root: Path,
    filename: str,
    *,
    request_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    label: str,
) -> tuple[Path, dict[str, Any]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    if root.exists():
        for path in root.rglob(filename):
            try:
                data = _load_json(path)
            except Gate3V33Error:
                continue
            if data.get("request_id") != request_id:
                continue
            if predicate(data):
                matches.append((path.resolve(), data))
    if len(matches) != 1:
        raise Gate3V33Error(
            f"Expected exactly one eligible {label} for {request_id}; found {len(matches)}."
        )
    return matches[0]


def _validate_gate2_v32(task_dir: Path, request_id: str, device_id: str) -> dict[str, Any]:
    path = task_dir / "m4_gate2_ftps_probe_v32.json"
    report = _load_json(path)
    if report.get("request_id") != request_id:
        raise Gate3V33Error("Gate 2 v3.2 request_id does not match.")
    if report.get("stage") != "developer_mode_ftps_canary_probe_v32":
        raise Gate3V33Error("Gate 2 report is not the locked v3.2 stage.")
    if report.get("status") != "ftps_probe_passed":
        raise Gate3V33Error("Gate 2 v3.2 has not passed.")
    if report.get("all_attempts_passed") is not True:
        raise Gate3V33Error("Gate 2 v3.2 did not pass all recorded attempts.")
    if int(report.get("success_count", 0)) < 1:
        raise Gate3V33Error("Gate 2 v3.2 has no successful real FTPS attempt.")
    if report.get("device_id") != device_id:
        raise Gate3V33Error("Gate 2 v3.2 DEVICE_ID does not match Gate 1.")
    transport = report.get("transport_fix")
    if not isinstance(transport, dict):
        raise Gate3V33Error("Gate 2 v3.2 transport_fix record is missing.")
    if transport.get("implicit_ftps") is not True:
        raise Gate3V33Error("Gate 2 v3.2 implicit FTPS lock is missing.")
    if transport.get("tls_session_reuse_on_data_channel") is not True:
        raise Gate3V33Error("Gate 2 v3.2 TLS session-reuse lock is missing.")
    return report


def _validate_m3_final_acceptance(project_root: Path, request_id: str) -> tuple[Path, dict[str, Any]]:
    return _find_unique_json(
        project_root / "outputs" / "m3",
        "m3_final_acceptance.json",
        request_id=request_id,
        label="M3 final acceptance",
        predicate=lambda data: (
            data.get("module") == "M3"
            and data.get("phase") == 7
            and data.get("stage") == "final_acceptance"
            and data.get("status") == "m3_accepted"
            and data.get("m3_complete") is True
            and data.get("m4_activation_authorized") is False
            and data.get("hard_constraints_passed") is True
            and data.get("next_module") == "M4"
        ),
    )


def _validate_gcode_audit(project_root: Path, request_id: str) -> tuple[Path, dict[str, Any], Path, str]:
    report_path, report = _find_unique_json(
        project_root / "outputs" / "m3",
        "m3_gcode_audit.json",
        request_id=request_id,
        label="Phase 5 G-code audit",
        predicate=lambda data: (
            data.get("module") == "M3"
            and data.get("phase") == 5
            and data.get("stage") == "slice_and_gcode_audit"
            and data.get("status") == "audit_passed"
            and data.get("hard_constraints_passed") is True
            and int(data.get("hard_failure_count", -1)) == 0
        ),
    )
    artifact = report.get("artifact")
    if not isinstance(artifact, dict):
        raise Gate3V33Error("Phase 5 audit artifact record is missing.")
    raw_path = artifact.get("path")
    expected_sha = artifact.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        raise Gate3V33Error("Phase 5 audit artifact path is invalid.")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise Gate3V33Error("Phase 5 audit artifact SHA-256 is invalid.")
    artifact_path = _resolve_recorded_path(raw_path, report_path=report_path)
    if artifact_path.suffix.lower() != ".3mf" or not artifact_path.name.lower().endswith(".gcode.3mf"):
        raise Gate3V33Error("Only the Phase 5 audited .gcode.3mf may enter Gate 3.")
    actual_sha = _sha256_file(artifact_path)
    if actual_sha != expected_sha.lower():
        raise Gate3V33Error(
            f"Audited artifact SHA-256 changed. expected={expected_sha}, actual={actual_sha}"
        )
    if not zipfile.is_zipfile(artifact_path):
        raise Gate3V33Error("Audited artifact is no longer a valid 3MF ZIP container.")
    recorded_entries = artifact.get("gcode_entries")
    if not isinstance(recorded_entries, list) or not recorded_entries:
        raise Gate3V33Error("Phase 5 audit has no recorded G-code entries.")
    with zipfile.ZipFile(artifact_path, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise Gate3V33Error(f"Audited 3MF ZIP integrity failed at {bad}.")
        names = set(archive.namelist())
    missing = [entry for entry in recorded_entries if entry not in names]
    if missing:
        raise Gate3V33Error(f"Audited G-code entries are missing from the 3MF: {missing}")
    return report_path, report, artifact_path, actual_sha


def expected_authorization_phrase(request_id: str) -> str:
    return f"{AUTH_PREFIX}{request_id}"


def _remote_size(ftp: Any, remote_name: str) -> int | None:
    try:
        size = ftp.size(remote_name)
    except Exception as exc:
        text = str(exc)
        if text.startswith("550"):
            return None
        raise
    return None if size is None else int(size)


def _remote_sha256(ftp: Any, remote_name: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0

    def consume(data: bytes) -> None:
        nonlocal count
        digest.update(data)
        count += len(data)

    ftp.retrbinary(f"RETR {remote_name}", consume)
    return digest.hexdigest(), count


@dataclass(frozen=True)
class UploadResult:
    remote_name: str
    remote_directory: str
    uploaded_new: bool
    reused_existing_remote: bool
    remote_size_verified: bool
    remote_sha256_verified: bool
    remote_sha256: str
    remote_size_bytes: int
    remote_retained: bool


def upload_audited_artifact(
    *,
    ip_address: str,
    access_code: str,
    artifact_path: Path,
    artifact_sha256: str,
    request_id: str,
    remote_dir: str = REMOTE_DIR,
    timeout: float = 30.0,
    ftp_factory=SessionReuseImplicitFTP_TLS,
) -> UploadResult:
    if not access_code:
        raise Gate3V33Error("Access Code cannot be empty.")
    size = artifact_path.stat().st_size
    remote_name = f"{request_id}_{artifact_sha256[:12]}.gcode.3mf"
    ftp = None
    uploaded_new = False
    verified = False
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        ftp = ftp_factory(context=context, timeout=timeout)
        ftp.connect(ip_address, FTPS_PORT, timeout=timeout)
        ftp.login(FTPS_USERNAME, access_code)
        ftp.prot_p()
        ftp.cwd(remote_dir)

        existing_size = _remote_size(ftp, remote_name)
        reused = existing_size is not None
        if existing_size is not None and existing_size != size:
            raise Gate3V33Error(
                f"Remote audited-artifact name already exists with a different size: {existing_size} != {size}"
            )

        if existing_size is None:
            with artifact_path.open("rb") as handle:
                ftp.storbinary(f"STOR {remote_name}", handle)
            uploaded_new = True

        observed_size = _remote_size(ftp, remote_name)
        if observed_size != size:
            raise Gate3V33Error(
                f"Remote artifact SIZE mismatch. expected={size}, observed={observed_size}"
            )

        remote_sha, downloaded_size = _remote_sha256(ftp, remote_name)
        if downloaded_size != size:
            raise Gate3V33Error(
                f"Remote artifact download size mismatch. expected={size}, observed={downloaded_size}"
            )
        if remote_sha != artifact_sha256:
            raise Gate3V33Error(
                f"Remote artifact SHA-256 mismatch. expected={artifact_sha256}, observed={remote_sha}"
            )
        verified = True
        return UploadResult(
            remote_name=remote_name,
            remote_directory=remote_dir,
            uploaded_new=uploaded_new,
            reused_existing_remote=reused,
            remote_size_verified=True,
            remote_sha256_verified=True,
            remote_sha256=remote_sha,
            remote_size_bytes=size,
            remote_retained=True,
        )
    except Exception:
        # If this run created a new remote artifact but could not prove byte-for-byte integrity,
        # remove it. A previously verified/reused remote object is never deleted here.
        if ftp is not None and uploaded_new and not verified:
            try:
                ftp.delete(remote_name)
            except Exception:
                pass
        raise
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass


def run_gate3_v33(
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
        raise Gate3V33Error(
            f"Gate 3 is locked to request_id {REQUEST_ID_DEFAULT}; observed {request_id}."
        )
    required_phrase = expected_authorization_phrase(request_id)
    if authorization_phrase.strip() != required_phrase:
        raise Gate3V33Error(
            f"Explicit upload authorization not granted. Required phrase: {required_phrase}"
        )

    identity = load_gate1_identity(project_root, request_id)
    task_dir: Path = identity["task_dir"]
    if identity["device_id"] != EXPECTED_DEVICE_ID:
        raise Gate3V33Error("Locked X1C DEVICE_ID changed before Gate 3.")
    gate2 = _validate_gate2_v32(task_dir, request_id, identity["device_id"])
    final_acceptance_path, _final_acceptance = _validate_m3_final_acceptance(
        project_root, request_id
    )
    audit_path, audit, artifact_path, artifact_sha = _validate_gcode_audit(
        project_root, request_id
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
            return existing | {"report_file": str(report_path), "reused_existing_report": True}
        raise Gate3V33Error(f"Existing Gate 3 report conflicts with the current locked inputs: {report_path}")

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
        "stage": "audited_artifact_upload_v33",
        "request_id": request_id,
        "status": "audited_artifact_uploaded",
        "printer_ip": identity["ip_address"],
        "device_id": identity["device_id"],
        "source_gate1": {
            "device_identity_file": str(task_dir / "m4_gate1_autodiscovered_device_v31.json"),
            "probe_file": str(task_dir / "m4_gate1_autodiscovery_probe_v31.json"),
        },
        "source_gate2": {
            "report_file": str(task_dir / "m4_gate2_ftps_probe_v32.json"),
            "status": gate2.get("status"),
            "attempt_count": gate2.get("attempt_count"),
            "success_count": gate2.get("success_count"),
            "tls_session_reuse_on_data_channel": True,
        },
        "source_m3": {
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
            "bambu_studio_generated_artifact_required": True,
            "phase5_audit_pass_required": True,
            "content_addressed_remote_name": True,
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
