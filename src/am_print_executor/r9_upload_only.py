from __future__ import annotations

import ftplib
import getpass
import hashlib
import json
import ssl

from pathlib import Path
from typing import Any


class R9UploadOnlyError(RuntimeError):
    pass


def _sha256(path: Path) -> str:

    h = hashlib.sha256()

    with path.open("rb") as f:

        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:

    obj = json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(obj, dict):

        raise R9UploadOnlyError(
            f"Expected JSON object: {path}"
        )

    return obj


def _retrieve_remote(
    ftp: Any,
    remote_name: str,
    expected_size: int | None,
) -> bytes:

    sink = bytearray()

    try:

        ftp.retrbinary(
            f"RETR {remote_name}",
            sink.extend,
        )

    except TimeoutError:

        # X1C FTPS may time out after transferring
        # the full announced SIZE.
        if (
            expected_size is None
            or len(sink)
            != expected_size
        ):

            raise

    if (
        expected_size is not None
        and len(sink)
        != expected_size
    ):

        raise R9UploadOnlyError(
            "Remote RETR size mismatch: "
            f"received={len(sink)} "
            f"expected={expected_size}"
        )

    return bytes(sink)


def upload_only_verified(
    *,
    artifact_path: Path,
    r7b_report: Path,
    r8_report: Path,
    expected_device_id: str,
    remote_directory: str = "/",
) -> dict[str, Any]:

    from am_print_executor import (
        ftps_probe_v32 as ftps,
    )

    from am_print_executor import (
        developer_mode_backend_v1120
        as backend,
    )

    artifact_path = (
        Path(artifact_path)
        .expanduser()
        .resolve()
    )

    r7b_report = (
        Path(r7b_report)
        .expanduser()
        .resolve()
    )

    r8_report = (
        Path(r8_report)
        .expanduser()
        .resolve()
    )

    if not artifact_path.is_file():

        raise R9UploadOnlyError(
            f"Artifact missing: {artifact_path}"
        )

    r7b = _load_json(
        r7b_report
    )

    r8 = _load_json(
        r8_report
    )

    # ----------------------------------------
    # Formal artifact lock
    # ----------------------------------------

    local_sha = _sha256(
        artifact_path
    )

    local_size = (
        artifact_path.stat().st_size
    )

    approved_sha = (
        r8.get("gcode", {})
        .get("sha256")
    )

    if (
        r8.get("status")
        != "preprint_dry_run_pass"
        or r8.get(
            "upload_eligible_offline"
        ) is not True
        or local_sha
        != approved_sha
    ):

        raise R9UploadOnlyError(
            "R8 artifact lock failed."
        )

    # ----------------------------------------
    # Formal device identity lock
    # ----------------------------------------

    if (
        r7b.get("status")
        != "ams_snapshot_observed"
        or r7b.get(
            "device_match"
        ) is not True
    ):

        raise R9UploadOnlyError(
            "R7B device identity gate failed."
        )

    ip_address = str(
        r7b.get("printer_ip")
        or ""
    ).strip()

    device_id = str(
        r7b.get("device_id")
        or ""
    ).strip()

    if not ip_address:

        raise R9UploadOnlyError(
            "R7B printer IP is empty."
        )

    if device_id != expected_device_id:

        raise R9UploadOnlyError(
            "R7B DEVICE_ID mismatch."
        )

    # ----------------------------------------
    # Use existing project artifact parser.
    # No slicing occurs.
    # ----------------------------------------

    inspected = (
        backend.inspect_gcode_3mf(
            artifact_path
        )
    )

    inspected_sha = str(
        inspected.get("sha256")
        or ""
    )

    if (
        inspected_sha
        and inspected_sha != local_sha
    ):

        raise R9UploadOnlyError(
            "Backend artifact SHA differs "
            "from R8 artifact SHA."
        )

    # ----------------------------------------
    # Build deterministic remote filename.
    # ----------------------------------------

    stem = artifact_path.name

    if stem.lower().endswith(
        ".gcode.3mf"
    ):

        stem = stem[
            :-len(".gcode.3mf")
        ]

    safe_name = (
        backend._safe_remote_name(
            f"{stem}_{local_sha[:10]}"
            ".gcode.3mf"
        )
    )

    # ----------------------------------------
    # TLS server identity pin.
    # Stored with CURRENT R7/R8 evidence,
    # not a legacy task ID.
    # ----------------------------------------

    fingerprint = (
        ftps.get_server_fingerprint(
            ip_address
        )
    )

    ftps.validate_or_create_ftps_pin(
        r7b_report.parent,
        ip_address=ip_address,
        device_id=device_id,
        fingerprint=fingerprint,
        confirmation_reader=getpass.getpass,
    )

    # ----------------------------------------
    # FTPS only.
    # This module contains NO MQTT client.
    # ----------------------------------------

    context = ssl.SSLContext(
        ssl.PROTOCOL_TLS_CLIENT
    )

    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    ftp = (
        ftps.SessionReuseImplicitFTP_TLS(
            context=context,
            timeout=30,
        )
    )

    uploaded_new = False
    reused_existing = False

    try:

        ftp.connect(
            ip_address,
            ftps.FTPS_PORT,
            timeout=30,
        )

        ftp.login(
            ftps.FTPS_USERNAME,
            # caller temporarily injects this
            # runtime-only value below
            _RUNTIME_ACCESS_CODE,
        )

        ftp.prot_p()

        ftp.cwd(
            remote_directory
        )

        try:

            remote_size_before = (
                ftp.size(
                    safe_name
                )
            )

        except ftplib.error_perm:

            remote_size_before = None

        if (
            remote_size_before
            == local_size
        ):

            remote_bytes = (
                _retrieve_remote(
                    ftp,
                    safe_name,
                    remote_size_before,
                )
            )

            remote_sha_before = (
                hashlib.sha256(
                    remote_bytes
                ).hexdigest()
            )

            if (
                remote_sha_before
                == local_sha
            ):

                reused_existing = True

        if not reused_existing:

            with artifact_path.open(
                "rb"
            ) as source:

                ftp.storbinary(
                    f"STOR {safe_name}",
                    source,
                )

            uploaded_new = True

        # ------------------------------------
        # Mandatory remote verification
        # ------------------------------------

        remote_size = (
            ftp.size(
                safe_name
            )
        )

        if remote_size != local_size:

            raise R9UploadOnlyError(
                "Remote SIZE verification failed: "
                f"{remote_size} != {local_size}"
            )

        remote_bytes = (
            _retrieve_remote(
                ftp,
                safe_name,
                remote_size,
            )
        )

        remote_sha = (
            hashlib.sha256(
                remote_bytes
            ).hexdigest()
        )

        if remote_sha != local_sha:

            raise R9UploadOnlyError(
                "Remote SHA256 verification failed."
            )

    finally:

        try:
            ftp.quit()

        except Exception:

            try:
                ftp.close()

            except Exception:
                pass

    remote_path = (
        "/"
        + safe_name
        if remote_directory == "/"
        else (
            remote_directory.rstrip("/")
            + "/"
            + safe_name
        )
    )

    return {
        "status":
            "upload_only_verified",

        "printer_ip":
            ip_address,

        "device_id":
            device_id,

        "remote_directory":
            remote_directory,

        "remote_name":
            safe_name,

        "remote_path":
            remote_path,

        "local_size_bytes":
            local_size,

        "remote_size_bytes":
            remote_size,

        "local_sha256":
            local_sha,

        "remote_sha256":
            remote_sha,

        "remote_size_verified":
            remote_size == local_size,

        "remote_sha256_verified":
            remote_sha == local_sha,

        "uploaded_new":
            uploaded_new,

        "reused_existing_remote":
            reused_existing,

        "network_used":
            True,

        "ftps_upload_attempted":
            True,

        "mqtt_publish_count":
            0,

        "printer_command_sent":
            False,

        "print_started":
            False,

        "heating_command_sent":
            False,

        "motion_command_sent":
            False,
    }


# Runtime secret slot.
# Assigned only for the duration of upload call.
_RUNTIME_ACCESS_CODE = None
