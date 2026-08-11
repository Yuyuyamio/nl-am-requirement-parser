from __future__ import annotations

import ftplib
import hashlib
import io
import json
import os
import ssl
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REQUEST_ID_DEFAULT = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
FTPS_PORT = 990
FTPS_USERNAME = "bblp"
REMOTE_DIR = "/cache"


class Gate3Error(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadResult:
    uploaded: bool
    reused_existing_remote: bool
    remote_size_verified: bool
    downloaded: bool
    sha256_verified: bool
    remote_retained: bool
    remote_path: str
    local_size_bytes: int
    remote_size_bytes: int | None
    local_sha256: str
    downloaded_sha256: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate3Error(f"{label} does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate3Error(f"{label} is invalid JSON: {path}: line {exc.lineno}") from exc
    if not isinstance(value, dict):
        raise Gate3Error(f"{label} root must be a JSON object: {path}")
    return value


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8-sig")
        if existing == text:
            return
        raise Gate3Error(f"Refusing to overwrite a different Gate 3 report: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _resolve_reference(raw: str, base: Path) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def validate_gate2_v32(project_root: Path, request_id: str) -> dict[str, Any]:
    task = project_root / "outputs" / "m4" / request_id
    path = task / "m4_gate2_ftps_probe_v32.json"
    report = _load_json(path, "Gate 2 v3.2 report")
    expected = {
        "module": "M4",
        "phase": 2,
        "stage": "developer_mode_ftps_canary_probe_v32",
        "request_id": request_id,
        "status": "ftps_probe_passed",
        "device_id": EXPECTED_DEVICE_ID,
        "ftps_port": FTPS_PORT,
        "remote_directory": REMOTE_DIR,
        "attempt_count": 3,
        "success_count": 3,
        "all_attempts_passed": True,
        "next_phase": "m4_gate3_audited_artifact_upload",
    }
    mismatches = {
        key: {"expected": value, "actual": report.get(key)}
        for key, value in expected.items()
        if report.get(key) != value
    }
    if mismatches:
        raise Gate3Error(f"Gate 2 v3.2 is not eligible for Gate 3: {mismatches}")
    transport = report.get("transport_fix")
    if not isinstance(transport, dict):
        raise Gate3Error("Gate 2 v3.2 transport_fix is missing.")
    if transport.get("implicit_ftps") is not True or transport.get("tls_session_reuse_on_data_channel") is not True:
        raise Gate3Error("Gate 2 v3.2 transport fix is not the validated implicit-FTPS configuration.")
    if transport.get("remote_presence_check") != "SIZE":
        raise Gate3Error("Gate 2 v3.2 must have validated SIZE-based remote presence checks.")
    policy = report.get("policy")
    if not isinstance(policy, dict):
        raise Gate3Error("Gate 2 v3.2 policy is missing.")
    required_policy = {
        "canary_only": True,
        "printable": False,
        "print_artifact_uploaded": False,
        "mqtt_publish_count": 0,
        "control_command_count": 0,
        "print_start_command_count": 0,
        "access_code_stored": False,
        "cleanup_required": True,
    }
    bad = {key: {"expected": value, "actual": policy.get(key)} for key, value in required_policy.items() if policy.get(key) != value}
    if bad:
        raise Gate3Error(f"Gate 2 v3.2 safety policy failed: {bad}")
    attempts = report.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 3:
        raise Gate3Error("Gate 2 v3.2 must contain exactly three attempts.")
    for index, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict) or attempt.get("success") is not True:
            raise Gate3Error(f"Gate 2 attempt {index} was not successful.")
        for key in ("uploaded", "remote_size_verified", "downloaded", "sha256_verified", "deleted", "remote_absent_after_delete"):
            if attempt.get(key) is not True:
                raise Gate3Error(f"Gate 2 attempt {index} did not pass {key}.")
        if attempt.get("error") is not None:
            raise Gate3Error(f"Gate 2 attempt {index} contains an error.")
    return report


def validate_m3_chain(project_root: Path, request_id: str) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    task = project_root / "outputs" / "m3" / request_id
    audit_path = task / "m3_gcode_audit.json"
    acceptance_path = task / "m3_final_acceptance.json"
    audit = _load_json(audit_path, "M3 G-code audit")
    acceptance = _load_json(acceptance_path, "M3 final acceptance")

    audit_expected = {
        "module": "M3",
        "phase": 5,
        "stage": "slice_and_gcode_audit",
        "request_id": request_id,
        "status": "audit_passed",
        "hard_constraints_passed": True,
        "hard_failure_count": 0,
    }
    bad_audit = {key: {"expected": value, "actual": audit.get(key)} for key, value in audit_expected.items() if audit.get(key) != value}
    if bad_audit:
        raise Gate3Error(f"M3 G-code audit is not eligible: {bad_audit}")

    acceptance_expected = {
        "module": "M3",
        "phase": 7,
        "stage": "final_acceptance",
        "request_id": request_id,
        "status": "m3_accepted",
        "m3_complete": True,
        "m4_activation_authorized": False,
        "printer_connection_attempted": False,
        "artifact_uploaded": False,
        "print_started": False,
        "next_module": "M4",
    }
    bad_acceptance = {key: {"expected": value, "actual": acceptance.get(key)} for key, value in acceptance_expected.items() if acceptance.get(key) != value}
    if bad_acceptance:
        raise Gate3Error(f"M3 final acceptance is not eligible: {bad_acceptance}")
    return audit_path, audit, acceptance_path, acceptance


def resolve_audited_artifact(audit_path: Path, audit: dict[str, Any]) -> tuple[Path, str, int, list[str]]:
    artifact = audit.get("artifact")
    if not isinstance(artifact, dict):
        raise Gate3Error("M3 audit artifact block is missing.")
    raw_path = artifact.get("path")
    expected_sha = artifact.get("sha256")
    expected_size = artifact.get("size_bytes")
    gcode_entries = artifact.get("gcode_entries")
    if not isinstance(raw_path, str) or not raw_path:
        raise Gate3Error("M3 audit artifact path is missing.")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise Gate3Error("M3 audit artifact SHA-256 is invalid.")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise Gate3Error("M3 audit artifact size is invalid.")
    if not isinstance(gcode_entries, list) or len(gcode_entries) != 1 or not isinstance(gcode_entries[0], str):
        raise Gate3Error("M3 audit must lock exactly one G-code entry.")

    artifact_path = _resolve_reference(raw_path, audit_path.parent)
    if artifact_path.name.lower().endswith(".gcode.3mf") is False:
        raise Gate3Error(f"Audited artifact is not a .gcode.3mf file: {artifact_path}")
    if not artifact_path.is_file():
        raise Gate3Error(f"Audited artifact does not exist: {artifact_path}")
    actual_size = artifact_path.stat().st_size
    actual_sha = sha256_file(artifact_path)
    if actual_size != expected_size or actual_sha != expected_sha:
        raise Gate3Error(
            f"Audited artifact changed after M3 audit: expected size/hash {expected_size}/{expected_sha}, actual {actual_size}/{actual_sha}"
        )
    if not zipfile.is_zipfile(artifact_path):
        raise Gate3Error("Audited .gcode.3mf is not a valid ZIP/3MF container.")
    with zipfile.ZipFile(artifact_path, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise Gate3Error(f"Audited .gcode.3mf ZIP entry is corrupt: {bad}")
        names = archive.namelist()
    if gcode_entries[0] not in names:
        raise Gate3Error(f"M3-audited G-code entry is missing from the artifact: {gcode_entries[0]}")
    return artifact_path, expected_sha, expected_size, gcode_entries


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """Implicit FTPS client with TLS session reuse for X1C data channels."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        context = kwargs.pop("context", None)
        if context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        super().__init__(*args, context=context, **kwargs)
        self._sock: Any = None

    @property
    def sock(self) -> Any:
        return self._sock

    @sock.setter
    def sock(self, value: Any) -> None:
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=getattr(self, "host", None))
        self._sock = value

    def ntransfercmd(self, cmd: str, rest: str | None = None) -> tuple[Any, int | None]:
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            session = self.sock.session if isinstance(self.sock, ssl.SSLSocket) else None
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=session,
            )
        return conn, size


def connect_ftps(ip_address: str, access_code: str, timeout: float) -> ImplicitFTP_TLS:
    if not access_code:
        raise Gate3Error("Access Code cannot be empty.")
    client = ImplicitFTP_TLS(timeout=timeout)
    try:
        client.connect(host=ip_address, port=FTPS_PORT, timeout=timeout)
        client.login(user=FTPS_USERNAME, passwd=access_code)
        client.prot_p()
        client.set_pasv(True)
        return client
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        raise


def remote_size(client: ImplicitFTP_TLS, filename: str) -> int | None:
    try:
        value = client.size(filename)
    except ftplib.error_perm as exc:
        text = str(exc)
        if text.startswith("550") or text.startswith("450"):
            return None
        raise
    return int(value) if value is not None else None


def download_remote(client: ImplicitFTP_TLS, filename: str) -> bytes:
    buffer = bytearray()
    client.retrbinary(f"RETR {filename}", buffer.extend)
    return bytes(buffer)


def upload_and_verify(
    ip_address: str,
    access_code: str,
    artifact_path: Path,
    expected_sha: str,
    expected_size: int,
    *,
    timeout: float = 60.0,
) -> UploadResult:
    remote_name = f"m4_{REQUEST_ID_DEFAULT}_{expected_sha[:12]}.gcode.3mf"
    remote_path = str(PurePosixPath(REMOTE_DIR) / remote_name)
    client: ImplicitFTP_TLS | None = None
    uploaded = False
    reused = False
    try:
        client = connect_ftps(ip_address, access_code, timeout)
        client.cwd(REMOTE_DIR)
        existing_size = remote_size(client, remote_name)
        if existing_size is not None:
            existing_data = download_remote(client, remote_name)
            existing_sha = sha256_bytes(existing_data)
            if existing_size != expected_size or existing_sha != expected_sha:
                raise Gate3Error(
                    "A conflicting printable artifact already exists at the deterministic remote path. It was not overwritten or deleted."
                )
            reused = True
            return UploadResult(
                uploaded=False,
                reused_existing_remote=True,
                remote_size_verified=True,
                downloaded=True,
                sha256_verified=True,
                remote_retained=True,
                remote_path=remote_path,
                local_size_bytes=expected_size,
                remote_size_bytes=existing_size,
                local_sha256=expected_sha,
                downloaded_sha256=existing_sha,
            )

        with artifact_path.open("rb") as handle:
            client.storbinary(f"STOR {remote_name}", handle, blocksize=1024 * 1024)
        uploaded = True

        observed_size = remote_size(client, remote_name)
        if observed_size != expected_size:
            raise Gate3Error(f"Remote SIZE mismatch after upload: expected {expected_size}, got {observed_size}")
        downloaded_data = download_remote(client, remote_name)
        downloaded_sha = sha256_bytes(downloaded_data)
        if len(downloaded_data) != expected_size or downloaded_sha != expected_sha:
            raise Gate3Error(
                f"Downloaded artifact does not match M3-audited bytes: size={len(downloaded_data)}, sha256={downloaded_sha}"
            )
        return UploadResult(
            uploaded=True,
            reused_existing_remote=False,
            remote_size_verified=True,
            downloaded=True,
            sha256_verified=True,
            remote_retained=True,
            remote_path=remote_path,
            local_size_bytes=expected_size,
            remote_size_bytes=observed_size,
            local_sha256=expected_sha,
            downloaded_sha256=downloaded_sha,
        )
    except Exception:
        # If the new upload failed verification, remove only the file created by this invocation.
        if client is not None and uploaded:
            try:
                client.delete(remote_name)
            except Exception:
                pass
        raise
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass


def run_gate3(project_root: Path, request_id: str, access_code: str, *, timeout: float = 60.0) -> dict[str, Any]:
    if request_id != REQUEST_ID_DEFAULT:
        raise Gate3Error(f"Gate 3 is locked to confirmed request ID {REQUEST_ID_DEFAULT}.")
    project_root = project_root.resolve()
    gate2 = validate_gate2_v32(project_root, request_id)
    audit_path, audit, acceptance_path, acceptance = validate_m3_chain(project_root, request_id)
    artifact_path, artifact_sha, artifact_size, gcode_entries = resolve_audited_artifact(audit_path, audit)

    ip_address = gate2.get("printer_ip")
    device_id = gate2.get("device_id")
    if not isinstance(ip_address, str) or not ip_address:
        raise Gate3Error("Gate 2 printer_ip is missing.")
    if device_id != EXPECTED_DEVICE_ID:
        raise Gate3Error(f"Gate 2 device_id changed: {device_id}")

    task = project_root / "outputs" / "m4" / request_id
    report_path = task / "m4_gate3_audited_artifact_upload_v1.json"

    transfer = upload_and_verify(
        ip_address,
        access_code,
        artifact_path,
        artifact_sha,
        artifact_size,
        timeout=timeout,
    )

    report: dict[str, Any] = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 3,
        "stage": "audited_print_artifact_ftps_upload",
        "request_id": request_id,
        "status": "audited_artifact_uploaded",
        "created_at": utc_now(),
        "printer_ip": ip_address,
        "device_id": device_id,
        "ftps_port": FTPS_PORT,
        "remote_directory": REMOTE_DIR,
        "source_gate2_report": str((task / "m4_gate2_ftps_probe_v32.json").resolve()),
        "source_gate2_report_sha256": sha256_file(task / "m4_gate2_ftps_probe_v32.json"),
        "source_m3_audit": str(audit_path),
        "source_m3_audit_sha256": sha256_file(audit_path),
        "source_m3_final_acceptance": str(acceptance_path),
        "source_m3_final_acceptance_sha256": sha256_file(acceptance_path),
        "artifact": {
            "local_path": str(artifact_path),
            "local_size_bytes": artifact_size,
            "local_sha256": artifact_sha,
            "gcode_entries": gcode_entries,
            "remote_path": transfer.remote_path,
            "remote_size_bytes": transfer.remote_size_bytes,
            "downloaded_sha256": transfer.downloaded_sha256,
            "uploaded_this_run": transfer.uploaded,
            "reused_existing_remote": transfer.reused_existing_remote,
            "remote_size_verified": transfer.remote_size_verified,
            "downloaded_for_verification": transfer.downloaded,
            "sha256_verified": transfer.sha256_verified,
            "remote_retained": transfer.remote_retained,
        },
        "policy": {
            "m3_audit_passed_required": True,
            "m3_final_acceptance_required": True,
            "audited_artifact_only": True,
            "artifact_uploaded": True,
            "remote_artifact_retained_for_gate4": True,
            "mqtt_publish_count": 0,
            "control_command_count": 0,
            "print_start_command_count": 0,
            "access_code_stored": False,
            "print_started": False,
        },
        "next_phase": "m4_gate4_first_print_start_authorization",
    }
    _write_json_atomic(report_path, report)
    result = dict(report)
    result["report_file"] = str(report_path)
    return result
