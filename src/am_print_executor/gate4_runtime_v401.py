from __future__ import annotations

import importlib
import hashlib
import ssl
from pathlib import Path
from typing import Any

REPORT_A = "m4_gate4a_runtime_preflight_v401.json"
REPORT_B = "m4_gate4b_first_print_start_v401.json"


def _load_v40():
    return importlib.import_module("am_print_executor.gate4_runtime_v40")


def _fast_remote_check(v40: Any, *, ip_address: str, access_code: str,
                       remote_name: str, expected_sha256: str,
                       expected_size: int, remote_dir: str = "/cache",
                       timeout: float = 180.0,
                       ftp_factory: Any = None) -> dict[str, Any]:
    """Gate4A fast integrity check.

    Gate3 v3.3.1 already performed full remote SHA-256 verification. Gate4A repeats
    the remote SHA-256 check, but v4.0.1 raises the FTPS read timeout to 180 seconds
    and reports the exact failing network stage.
    """
    if not access_code:
        raise v40.Gate4V40Error("Access Code cannot be empty.")
    factory = ftp_factory or v40.SessionReuseImplicitFTP_TLS
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    ftp = factory(context=context, timeout=timeout)
    stage = "connect"
    try:
        stage = "connect_990"
        ftp.connect(ip_address, v40.FTPS_PORT, timeout=timeout)
        stage = "login"
        ftp.login(v40.FTPS_USERNAME, access_code)
        stage = "protect_data_channel"
        ftp.prot_p()
        stage = "cwd_cache"
        ftp.cwd(remote_dir)
        stage = "size"
        remote_size = ftp.size(remote_name)
        if remote_size is None or int(remote_size) != int(expected_size):
            raise v40.Gate4V40Error(
                f"Gate4A FTPS SIZE mismatch: expected={expected_size}, observed={remote_size}"
            )
        stage = "full_sha256_download"
        digest = hashlib.sha256()
        downloaded_size = 0
        def consume(data: bytes) -> None:
            nonlocal downloaded_size
            digest.update(data)
            downloaded_size += len(data)
        ftp.retrbinary(f"RETR {remote_name}", consume)
        remote_sha256 = digest.hexdigest()
        if downloaded_size != int(expected_size):
            raise v40.Gate4V40Error(
                f"Gate4A FTPS download size mismatch: expected={expected_size}, observed={downloaded_size}"
            )
        if remote_sha256 != expected_sha256:
            raise v40.Gate4V40Error(
                "Gate4A remote SHA-256 changed after Gate3 v3.3.1."
            )
        return {
            "remote_directory": remote_dir,
            "remote_name": remote_name,
            "remote_size_bytes": int(remote_size),
            "remote_size_verified": True,
            "remote_sha256": remote_sha256,
            "remote_sha256_verified": True,
            "remote_sha256_verification_source": "gate4a_v4.0.1_full_redownload",
            "gate4a_full_remote_redownload": True,
            "ftps_read_timeout_seconds": timeout,
        }
    except TimeoutError as exc:
        raise v40.Gate4V40Error(
            f"Gate4A FTPS timeout at stage={stage}. Port 990 is reachable but the read operation did not finish in time."
        ) from exc
    except OSError as exc:
        if "timed out" in str(exc).lower():
            raise v40.Gate4V40Error(
                f"Gate4A FTPS timeout at stage={stage}: {exc}"
            ) from exc
        raise
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def _diagnostic_mqtt_status(v40: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    original = getattr(v40, "_v401_original_passive_status_once")
    try:
        return original(*args, **kwargs)
    except TimeoutError as exc:
        raise v40.Gate4V40Error(
            "Gate4A MQTT timeout on port 8883 while waiting for the passive X1C status report."
        ) from exc
    except OSError as exc:
        if "timed out" in str(exc).lower():
            raise v40.Gate4V40Error(
                f"Gate4A MQTT timeout on port 8883: {exc}"
            ) from exc
        raise


def _patch_writer(v40: Any):
    original = v40._write_json_atomic

    def patched(path: Path, payload: dict[str, Any]) -> None:
        data = dict(payload)
        if path.name == REPORT_A:
            data["stage"] = "runtime_preflight_v401"
            data["network_fix_version"] = "4.0.1"
            data["next_phase"] = "m4_gate4b_manual_first_print_start_v401"
        elif path.name == REPORT_B:
            data["stage"] = "manual_first_print_start_v401"
            data["network_fix_version"] = "4.0.1"
        original(path, data)

    return patched


def _reload_report(path: Path) -> dict[str, Any]:
    import json
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    data["report_file"] = str(path)
    return data


def run_gate4a_v401(project_root: Path, access_code: str) -> dict[str, Any]:
    v40 = _load_v40()
    original_verify = v40.verify_remote_artifact_v32
    original_status = v40._passive_status_once
    original_writer = v40._write_json_atomic
    old_report_a = v40.GATE4A_REPORT
    try:
        v40.GATE4A_REPORT = REPORT_A
        v40.verify_remote_artifact_v32 = lambda **kwargs: _fast_remote_check(v40, **kwargs)
        v40._v401_original_passive_status_once = original_status
        v40._passive_status_once = lambda *args, **kwargs: _diagnostic_mqtt_status(v40, *args, **kwargs)
        v40._write_json_atomic = _patch_writer(v40)
        v40.run_gate4a(project_root, access_code)
        report_path = project_root.resolve() / "outputs" / "m4" / v40.REQUEST_ID / REPORT_A
        return _reload_report(report_path)
    finally:
        v40.verify_remote_artifact_v32 = original_verify
        v40._passive_status_once = original_status
        v40._write_json_atomic = original_writer
        v40.GATE4A_REPORT = old_report_a
        if hasattr(v40, "_v401_original_passive_status_once"):
            delattr(v40, "_v401_original_passive_status_once")


def run_gate4b_v401(project_root: Path, access_code: str, authorization_phrase: str) -> dict[str, Any]:
    v40 = _load_v40()
    original_writer = v40._write_json_atomic
    old_report_a = v40.GATE4A_REPORT
    old_report_b = v40.GATE4B_REPORT
    try:
        v40.GATE4A_REPORT = REPORT_A
        v40.GATE4B_REPORT = REPORT_B
        v40._write_json_atomic = _patch_writer(v40)
        v40.run_gate4b(project_root, access_code, authorization_phrase)
        report_path = project_root.resolve() / "outputs" / "m4" / v40.REQUEST_ID / REPORT_B
        return _reload_report(report_path)
    finally:
        v40._write_json_atomic = original_writer
        v40.GATE4A_REPORT = old_report_a
        v40.GATE4B_REPORT = old_report_b


def expected_start_phrase() -> str:
    return _load_v40().expected_start_phrase()
