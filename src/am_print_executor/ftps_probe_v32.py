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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REQUEST_ID_DEFAULT = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
FTPS_PORT = 990
FTPS_USERNAME = "bblp"
REMOTE_DIR = "/cache"


class Gate2V32Error(RuntimeError):
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
        raise Gate2V32Error(f"Refusing to overwrite a different file: {path}")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(min(0.01 * (2**attempt), 0.2))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Gate2V32Error(f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Gate2V32Error(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise Gate2V32Error(f"JSON root must be an object: {path}")
    return data


def load_gate1_identity(project_root: Path, request_id: str) -> dict[str, Any]:
    task_dir = project_root / "outputs" / "m4" / request_id
    pin_path = task_dir / "m4_gate1_autodiscovered_device_v31.json"
    probe_path = task_dir / "m4_gate1_autodiscovery_probe_v31.json"

    pin = _load_json(pin_path)
    probe = _load_json(probe_path)

    if pin.get("request_id") != request_id or probe.get("request_id") != request_id:
        raise Gate2V32Error("Gate 1 request_id does not match.")
    if probe.get("status") != "readonly_probe_passed":
        raise Gate2V32Error("Gate 1 is not passed.")
    if probe.get("all_attempts_passed") is not True or int(probe.get("attempt_count", 0)) < 10:
        raise Gate2V32Error("Gate 1 must pass 10/10 stability before Gate 2.")

    device_id = pin.get("device_id")
    if device_id != probe.get("device_id_autodiscovered"):
        raise Gate2V32Error("Gate 1 DEVICE_ID records do not match.")
    if device_id != EXPECTED_DEVICE_ID:
        raise Gate2V32Error(
            f"DEVICE_ID changed. Expected {EXPECTED_DEVICE_ID}, observed {device_id!r}."
        )

    ip_address = pin.get("ip_address")
    if not isinstance(ip_address, str) or not ip_address:
        raise Gate2V32Error("Gate 1 pin has no valid printer IP.")

    return {
        "task_dir": task_dir,
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
        raise Gate2V32Error(f"Cannot connect TLS to {ip_address}:{FTPS_PORT}: {exc}") from exc
    if not cert:
        raise Gate2V32Error("FTPS server did not provide a TLS certificate.")
    return hashlib.sha256(cert).hexdigest().upper()


def _format_fp(value: str) -> str:
    compact = value.replace(":", "").upper()
    return ":".join(compact[i:i+2] for i in range(0, len(compact), 2))


def validate_or_create_ftps_pin(
    task_dir: Path,
    *,
    ip_address: str,
    device_id: str,
    fingerprint: str,
    confirmation_reader: Callable[[str], str] = input,
    trusted_fingerprint: str | None = None,
) -> dict[str, Any]:
    # Reuse the successful certificate pin from v3.1 if it exists.
    old_pin = task_dir / "m4_gate2_ftps_tls_pin_v31.json"
    new_pin = task_dir / "m4_gate2_ftps_tls_pin_v32.json"
    compact = fingerprint.replace(":", "").upper()

    for candidate in (old_pin, new_pin):
        if candidate.exists():
            pin = _load_json(candidate)
            if pin.get("ip_address") != ip_address:
                raise Gate2V32Error("Pinned FTPS printer IP changed.")
            if pin.get("device_id") != device_id:
                raise Gate2V32Error("Pinned FTPS DEVICE_ID changed.")
            if str(pin.get("certificate_sha256", "")).replace(":", "").upper() != compact:
                raise Gate2V32Error(
                    "FTPS TLS certificate fingerprint changed. Stop and verify the physical printer."
                )
            return pin

    if trusted_fingerprint is not None:
        trusted = trusted_fingerprint.replace(":", "").upper()
        if trusted != compact:
            raise Gate2V32Error(
                "FTPS TLS certificate does not match the authenticated "
                "MQTT printer connection."
            )
    else:
        suffix = compact[-8:]
        print("\nFIRST-CONNECTION FTPS TLS FINGERPRINT")
        print(_format_fp(compact))
        entered = confirmation_reader(
            f"Type the final 8 characters {suffix} to pin this FTPS certificate: "
        ).strip().replace(":", "").upper()
        if entered != suffix:
            raise Gate2V32Error("FTPS TLS fingerprint confirmation failed.")

    pin = {
        "schema_version": "0.1.0",
        "module": "M4",
        "stage": "gate2_ftps_tls_pin_v32",
        "request_id": REQUEST_ID_DEFAULT,
        "ip_address": ip_address,
        "device_id": device_id,
        "port": FTPS_PORT,
        "certificate_sha256": compact,
        "access_code_stored": False,
    }
    _write_json_atomic(new_pin, pin)
    return pin


class SessionReuseImplicitFTP_TLS(ftplib.FTP_TLS):
    """Implicit FTPS on 990, reusing the control TLS session on every data connection."""

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

    def ntransfercmd(self, cmd, rest=None):
        # Deliberately bypass FTP_TLS.ntransfercmd, because that method creates
        # a fresh TLS session on the data socket. X1C requires session reuse.
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            if not isinstance(self.sock, ssl.SSLSocket):
                conn.close()
                raise Gate2V32Error("FTPS control socket is not an SSL socket.")
            session = self.sock.session
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=session,
            )
        return conn, size

    @staticmethod
    def _safe_unwrap(conn) -> None:
        if isinstance(conn, ssl.SSLSocket):
            try:
                conn.unwrap()
            except OSError:
                # Some FTPS servers close the data socket without a TLS close_notify.
                # Transfer integrity is checked separately with SHA-256.
                pass

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as conn:
            while True:
                buf = fp.read(blocksize)
                if not buf:
                    break
                conn.sendall(buf)
                if callback:
                    callback(buf)
            self._safe_unwrap(conn)
        return self.voidresp()

    def retrbinary(self, cmd, callback, blocksize=8192, rest=None):
        # X1C_RETR_RESILIENCE_V1200
        # Stop once the server-announced RETR size is received, and retry a
        # bounded number of transient data-socket timeouts.
        self.voidcmd("TYPE I")
        conn, expected_size = self.ntransfercmd(cmd, rest)
        received = 0
        consecutive_timeouts = 0

        with conn:
            while expected_size is None or received < expected_size:
                try:
                    data = conn.recv(blocksize)
                except TimeoutError:
                    consecutive_timeouts += 1
                    if consecutive_timeouts > 2:
                        raise
                    continue

                if not data:
                    break

                consecutive_timeouts = 0
                received += len(data)
                callback(data)

            if expected_size is not None and received < expected_size:
                raise Gate2V32Error(
                    "FTPS RETR ended early: "
                    f"received={received} expected={expected_size}"
                )

            self._safe_unwrap(conn)

        return self.voidresp()


@dataclass
class Attempt:
    attempt: int
    uploaded: bool
    remote_size_verified: bool
    downloaded: bool
    sha256_verified: bool
    deleted: bool
    remote_absent_after_delete: bool
    local_sha256: str
    downloaded_sha256: str | None
    remote_name: str
    error: str | None

    @property
    def success(self) -> bool:
        return (
            self.uploaded
            and self.remote_size_verified
            and self.downloaded
            and self.sha256_verified
            and self.deleted
            and self.remote_absent_after_delete
            and self.error is None
        )


def _remote_exists_by_size(ftp: ftplib.FTP, remote_name: str) -> tuple[bool, int | None]:
    try:
        size = ftp.size(remote_name)
        return True, size
    except ftplib.error_perm as exc:
        text = str(exc)
        if text.startswith("550"):
            return False, None
        raise


def run_canary_attempt(
    ip_address: str,
    access_code: str,
    request_id: str,
    attempt_number: int,
    *,
    remote_dir: str = REMOTE_DIR,
    timeout: float = 15.0,
    ftp_factory=SessionReuseImplicitFTP_TLS,
) -> Attempt:
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

    uploaded = size_ok = downloaded = hash_ok = deleted = absent = False
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

        exists, remote_size = _remote_exists_by_size(ftp, remote_name)
        size_ok = exists and remote_size == len(payload)
        if not size_ok:
            raise Gate2V32Error(
                f"Remote SIZE mismatch. expected={len(payload)}, observed={remote_size}"
            )

        sink = io.BytesIO()
        ftp.retrbinary(f"RETR {remote_name}", sink.write)
        downloaded = True
        downloaded_sha = _sha256_bytes(sink.getvalue())
        hash_ok = downloaded_sha == local_sha
        if not hash_ok:
            raise Gate2V32Error("Downloaded canary SHA-256 does not match local SHA-256.")

        ftp.delete(remote_name)
        deleted = True

        exists_after, _ = _remote_exists_by_size(ftp, remote_name)
        absent = not exists_after
        if not absent:
            raise Gate2V32Error("Remote canary still exists after delete.")

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if ftp is not None and uploaded and not deleted:
            try:
                ftp.delete(remote_name)
                deleted = True
                try:
                    exists_after, _ = _remote_exists_by_size(ftp, remote_name)
                    absent = not exists_after
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

    return Attempt(
        attempt=attempt_number,
        uploaded=uploaded,
        remote_size_verified=size_ok,
        downloaded=downloaded,
        sha256_verified=hash_ok,
        deleted=deleted,
        remote_absent_after_delete=absent,
        local_sha256=local_sha,
        downloaded_sha256=downloaded_sha,
        remote_name=remote_name,
        error=error,
    )


def run_gate2_v32(
    project_root: Path,
    request_id: str,
    access_code: str,
    *,
    attempts: int = 1,
    remote_dir: str = REMOTE_DIR,
    confirmation_reader: Callable[[str], str] = input,
) -> dict[str, Any]:
    if not access_code:
        raise Gate2V32Error("Access Code cannot be empty.")
    if attempts < 1 or attempts > 3:
        raise Gate2V32Error("Attempts must be between 1 and 3.")

    identity = load_gate1_identity(project_root.resolve(), request_id)
    task_dir: Path = identity["task_dir"]
    report_path = task_dir / "m4_gate2_ftps_probe_v32.json"

    fingerprint = get_server_fingerprint(identity["ip_address"])
    validate_or_create_ftps_pin(
        task_dir,
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
        "stage": "developer_mode_ftps_canary_probe_v32",
        "request_id": request_id,
        "status": "ftps_probe_passed" if all_passed else "ftps_probe_failed",
        "printer_ip": identity["ip_address"],
        "device_id": identity["device_id"],
        "ftps_port": FTPS_PORT,
        "remote_directory": remote_dir,
        "transport_fix": {
            "implicit_ftps": True,
            "tls_session_reuse_on_data_channel": True,
            "remote_presence_check": "SIZE",
        },
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
