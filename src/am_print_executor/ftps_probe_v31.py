from __future__ import annotations

import ftplib
import getpass
import hashlib
import io
import json
import os
import secrets
import socket
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REQUEST_ID_DEFAULT = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
FTPS_PORT = 990
FTPS_USERNAME = "bblp"
REMOTE_DIR = "/cache"


class Gate2Error(RuntimeError):
    pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8-sig")
        if existing == text:
            return
        raise Gate2Error(f"Refusing to overwrite a different file: {path}")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate2Error(f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate2Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise Gate2Error(f"JSON root must be an object: {path}")
    return data


def load_gate1_identity(project_root: Path, request_id: str) -> dict[str, Any]:
    task_dir = project_root / "outputs" / "m4" / request_id
    pin_path = task_dir / "m4_gate1_autodiscovered_device_v31.json"
    probe_path = task_dir / "m4_gate1_autodiscovery_probe_v31.json"

    pin = _load_json(pin_path)
    probe = _load_json(probe_path)

    if pin.get("request_id") != request_id or probe.get("request_id") != request_id:
        raise Gate2Error("Gate 1 request_id does not match the requested task.")
    if probe.get("status") != "readonly_probe_passed":
        raise Gate2Error("Gate 1 is not in readonly_probe_passed state.")
    if probe.get("all_attempts_passed") is not True:
        raise Gate2Error("Gate 1 stability result is not fully passed.")
    if int(probe.get("attempt_count", 0)) < 10:
        raise Gate2Error("Gate 1 must pass the 10-attempt stability test before Gate 2.")

    device_id = pin.get("device_id")
    if device_id != probe.get("device_id_autodiscovered"):
        raise Gate2Error("Gate 1 pinned DEVICE_ID and probe DEVICE_ID do not match.")
    if device_id != EXPECTED_DEVICE_ID:
        raise Gate2Error(
            f"Gate 1 DEVICE_ID changed. Expected {EXPECTED_DEVICE_ID}, observed {device_id!r}."
        )

    ip_address = pin.get("ip_address")
    if not isinstance(ip_address, str) or not ip_address:
        raise Gate2Error("Gate 1 pin does not contain a valid printer IP.")

    return {
        "task_dir": task_dir,
        "pin_path": pin_path,
        "probe_path": probe_path,
        "ip_address": ip_address,
        "device_id": device_id,
    }


def get_server_fingerprint(ip_address: str, timeout: float = 6.0) -> str:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip_address, FTPS_PORT), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=ip_address) as tls_sock:
                cert = tls_sock.getpeercert(binary_form=True)
    except OSError as exc:
        raise Gate2Error(f"Cannot open TLS connection to {ip_address}:{FTPS_PORT}: {exc}") from exc
    if not cert:
        raise Gate2Error("FTPS service did not provide a TLS certificate.")
    return hashlib.sha256(cert).hexdigest().upper()


def _format_fp(value: str) -> str:
    compact = value.replace(":", "").upper()
    return ":".join(compact[i:i+2] for i in range(0, len(compact), 2))


def ensure_ftps_pin(
    pin_path: Path,
    *,
    ip_address: str,
    device_id: str,
    fingerprint: str,
    confirmation_reader: Callable[[str], str] = input,
) -> dict[str, Any]:
    compact = fingerprint.replace(":", "").upper()
    if pin_path.exists():
        pin = _load_json(pin_path)
        if pin.get("ip_address") != ip_address:
            raise Gate2Error("Pinned FTPS printer IP changed.")
        if pin.get("device_id") != device_id:
            raise Gate2Error("Pinned FTPS DEVICE_ID changed.")
        if pin.get("certificate_sha256") != compact:
            raise Gate2Error("Pinned FTPS TLS certificate changed. Stop and verify the physical printer.")
        return pin

    suffix = compact[-8:]
    print("\nFIRST-CONNECTION FTPS TLS FINGERPRINT")
    print(_format_fp(compact))
    entered = confirmation_reader(
        f"Type the final 8 characters {suffix} to pin this FTPS certificate: "
    ).strip().replace(":", "").upper()
    if entered != suffix:
        raise Gate2Error("FTPS TLS fingerprint confirmation failed.")

    pin = {
        "schema_version": "0.1.0",
        "module": "M4",
        "stage": "gate2_ftps_tls_pin",
        "request_id": REQUEST_ID_DEFAULT,
        "ip_address": ip_address,
        "device_id": device_id,
        "port": FTPS_PORT,
        "certificate_sha256": compact,
        "access_code_stored": False,
    }
    _write_json_atomic(pin_path, pin)
    return pin


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """ftplib FTP_TLS adapted for implicit TLS on TCP/990."""

    def connect(self, host: str = "", port: int = 0, timeout: float | object = -999, source_address=None):
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address

        self.sock = socket.create_connection(
            (self.host, self.port),
            self.timeout,
            source_address=self.source_address,
        )
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


@dataclass
class AttemptResult:
    attempt: int
    uploaded: bool
    listed: bool
    downloaded: bool
    verified: bool
    deleted: bool
    remote_absent_after_delete: bool
    local_sha256: str
    downloaded_sha256: str | None
    remote_name: str
    error: str | None

    @property
    def success(self) -> bool:
        return all(
            (
                self.uploaded,
                self.listed,
                self.downloaded,
                self.verified,
                self.deleted,
                self.remote_absent_after_delete,
                self.error is None,
            )
        )


def run_canary_attempt(
    ip_address: str,
    access_code: str,
    request_id: str,
    attempt_number: int,
    *,
    remote_dir: str = REMOTE_DIR,
    timeout: float = 15.0,
    ftp_factory=ImplicitFTP_TLS,
) -> AttemptResult:
    nonce = secrets.token_hex(6)
    remote_name = f"m4_gate2_{request_id}_{nonce}.txt"
    payload = (
        "M4 Gate 2 FTPS transport canary\n"
        f"request_id={request_id}\n"
        f"nonce={nonce}\n"
        "printable=false\n"
        "purpose=upload-download-sha256-delete\n"
    ).encode("utf-8")
    local_sha = _sha256_bytes(payload)

    uploaded = listed = downloaded = verified = deleted = absent = False
    downloaded_sha = None
    error = None
    ftp = None
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        ftp = ftp_factory(context=context, timeout=timeout)
        ftp.connect(ip_address, FTPS_PORT, timeout=timeout)
        ftp.login(FTPS_USERNAME, access_code)
        ftp.prot_p()
        ftp.cwd(remote_dir)

        ftp.storbinary(f"STOR {remote_name}", io.BytesIO(payload))
        uploaded = True

        names = ftp.nlst()
        listed = remote_name in {Path(name).name for name in names}
        if not listed:
            raise Gate2Error("Uploaded canary was not visible in the remote directory listing.")

        sink = io.BytesIO()
        ftp.retrbinary(f"RETR {remote_name}", sink.write)
        downloaded = True
        downloaded_bytes = sink.getvalue()
        downloaded_sha = _sha256_bytes(downloaded_bytes)
        verified = downloaded_sha == local_sha
        if not verified:
            raise Gate2Error("Downloaded canary SHA-256 does not match local canary SHA-256.")

        ftp.delete(remote_name)
        deleted = True

        names_after = ftp.nlst()
        absent = remote_name not in {Path(name).name for name in names_after}
        if not absent:
            raise Gate2Error("Canary still exists after delete.")

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if ftp is not None and uploaded and not deleted:
            try:
                ftp.delete(remote_name)
                deleted = True
                try:
                    names_after = ftp.nlst()
                    absent = remote_name not in {Path(name).name for name in names_after}
                except Exception:
                    pass
            except Exception:
                pass
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    return AttemptResult(
        attempt=attempt_number,
        uploaded=uploaded,
        listed=listed,
        downloaded=downloaded,
        verified=verified,
        deleted=deleted,
        remote_absent_after_delete=absent,
        local_sha256=local_sha,
        downloaded_sha256=downloaded_sha,
        remote_name=remote_name,
        error=error,
    )


def run_gate2(
    project_root: Path,
    request_id: str,
    access_code: str,
    *,
    attempts: int = 1,
    remote_dir: str = REMOTE_DIR,
    confirmation_reader: Callable[[str], str] = input,
) -> dict[str, Any]:
    if not access_code:
        raise Gate2Error("Access Code cannot be empty.")
    if attempts < 1 or attempts > 3:
        raise Gate2Error("Gate 2 attempts must be between 1 and 3.")

    identity = load_gate1_identity(project_root.resolve(), request_id)
    task_dir: Path = identity["task_dir"]
    ftps_pin_path = task_dir / "m4_gate2_ftps_tls_pin_v31.json"
    report_path = task_dir / "m4_gate2_ftps_probe_v31.json"

    fingerprint = get_server_fingerprint(identity["ip_address"])
    ensure_ftps_pin(
        ftps_pin_path,
        ip_address=identity["ip_address"],
        device_id=identity["device_id"],
        fingerprint=fingerprint,
        confirmation_reader=confirmation_reader,
    )

    results = [
        run_canary_attempt(
            identity["ip_address"],
            access_code,
            request_id,
            number,
            remote_dir=remote_dir,
        )
        for number in range(1, attempts + 1)
    ]

    success_count = sum(1 for item in results if item.success)
    all_passed = success_count == attempts

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": 2,
        "stage": "developer_mode_ftps_canary_probe",
        "request_id": request_id,
        "status": "ftps_probe_passed" if all_passed else "ftps_probe_failed",
        "printer_ip": identity["ip_address"],
        "device_id": identity["device_id"],
        "ftps_port": FTPS_PORT,
        "remote_directory": remote_dir,
        "attempt_count": attempts,
        "success_count": success_count,
        "all_attempts_passed": all_passed,
        "policy": {
            "canary_only": True,
            "canary_extension": ".txt",
            "printable": False,
            "print_artifact_uploaded": False,
            "mqtt_publish_count": 0,
            "control_command_count": 0,
            "print_start_command_count": 0,
            "access_code_stored": False,
            "cleanup_required": True,
        },
        "attempts": [item.__dict__ | {"success": item.success} for item in results],
        "next_phase": (
            "m4_gate3_audited_artifact_upload"
            if all_passed and attempts == 3
            else "m4_gate2_stability_test"
            if all_passed
            else None
        ),
    }
    _write_json_atomic(report_path, report)
    report["report_file"] = str(report_path)
    return report
