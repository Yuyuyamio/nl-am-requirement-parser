from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import ssl
import zipfile

from pathlib import Path
from typing import Any


class X1CUploadOnlyError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def inspect_artifact(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()

    if not path.is_file():
        raise X1CUploadOnlyError(
            f"Artifact missing: {path}"
        )

    if not zipfile.is_zipfile(path):
        raise X1CUploadOnlyError(
            "Artifact is not a valid 3MF ZIP."
        )

    with zipfile.ZipFile(path, "r") as zf:
        bad = zf.testzip()

        if bad is not None:
            raise X1CUploadOnlyError(
                f"Corrupt 3MF member: {bad}"
            )

        entries = sorted(
            name
            for name in zf.namelist()
            if re.fullmatch(
                r"Metadata/plate_\d+\.gcode",
                name,
            )
        )

    if len(entries) != 1:
        raise X1CUploadOnlyError(
            "Expected exactly one printable plate "
            f"G-code, found {entries}"
        )

    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "gcode_entries": entries,
    }


def verify_preprint_guard(
    *,
    artifact: Path,
    report: Path,
) -> dict[str, Any]:

    artifact_info = inspect_artifact(
        artifact
    )

    report = report.expanduser().resolve()

    if not report.is_file():
        raise X1CUploadOnlyError(
            f"Preprint dry-run report missing: {report}"
        )

    data = json.loads(
        report.read_text(
            encoding="utf-8-sig"
        )
    )

    if data.get("status") != (
        "x1c_preprint_dry_run_pass"
    ):
        raise X1CUploadOnlyError(
            "Preprint dry-run is not PASS."
        )

    expected_sha = (
        data.get("artifact", {})
        .get("sha256")
    )

    if expected_sha != artifact_info["sha256"]:
        raise X1CUploadOnlyError(
            "Artifact SHA256 no longer matches "
            "the PASS dry-run artifact."
        )

    return artifact_info


def safe_remote_name(
    artifact: Path,
    sha256: str,
) -> str:

    stem = artifact.stem

    stem = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        stem,
    )

    return (
        stem[:80]
        + "_"
        + sha256[:10]
        + ".gcode.3mf"
    )


def retrieve_verified_bytes(
    ftp,
    remote_name: str,
    expected_size: int,
) -> bytes:

    sink = bytearray()

    try:
        ftp.retrbinary(
            f"RETR {remote_name}",
            sink.extend,
        )

    except TimeoutError:
        # Some X1C FTPS sessions time out after
        # transferring all bytes. Accept only when
        # the full expected payload arrived.
        if len(sink) != expected_size:
            raise

    if len(sink) != expected_size:
        raise X1CUploadOnlyError(
            "Remote RETR size mismatch: "
            f"received={len(sink)}, "
            f"expected={expected_size}"
        )

    return bytes(sink)


def upload_only(
    *,
    artifact: Path,
    preprint_report: Path,
    ip_address: str,
    device_id: str,
    evidence_dir: Path,
    access_code: str,
) -> dict[str, Any]:

    from am_print_executor import (
        ftps_probe_v32 as ftps,
    )

    if not access_code:
        raise X1CUploadOnlyError(
            "Access Code cannot be empty."
        )

    artifact_info = verify_preprint_guard(
        artifact=artifact,
        report=preprint_report,
    )

    evidence_dir = (
        evidence_dir
        .expanduser()
        .resolve()
    )

    evidence_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    fingerprint = (
        ftps.get_server_fingerprint(
            ip_address
        )
    )

    ftps.validate_or_create_ftps_pin(
        evidence_dir,
        ip_address=ip_address,
        device_id=device_id,
        fingerprint=fingerprint,
    )

    local_path = Path(
        artifact_info["path"]
    )

    remote_name = safe_remote_name(
        local_path,
        artifact_info["sha256"],
    )

    context = ssl.SSLContext(
        ssl.PROTOCOL_TLS_CLIENT
    )

    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    ftp = ftps.SessionReuseImplicitFTP_TLS(
        context=context,
        timeout=120,
    )

    reused = False

    try:
        ftp.connect(
            ip_address,
            ftps.FTPS_PORT,
            timeout=120,
        )

        ftp.login(
            ftps.FTPS_USERNAME,
            access_code,
        )

        ftp.prot_p()

        ftp.cwd("/")

        exists, remote_size = (
            ftps._remote_exists_by_size(
                ftp,
                remote_name,
            )
        )

        if exists:
            remote_bytes = (
                retrieve_verified_bytes(
                    ftp,
                    remote_name,
                    int(remote_size),
                )
            )

            remote_sha = hashlib.sha256(
                remote_bytes
            ).hexdigest()

            if (
                remote_sha
                != artifact_info["sha256"]
            ):
                raise X1CUploadOnlyError(
                    "Existing remote file has "
                    "different SHA256."
                )

            reused = True

        else:
            with local_path.open("rb") as handle:
                ftp.storbinary(
                    f"STOR {remote_name}",
                    handle,
                )

            exists, remote_size = (
                ftps._remote_exists_by_size(
                    ftp,
                    remote_name,
                )
            )

            if not exists:
                raise X1CUploadOnlyError(
                    "Remote artifact was not found "
                    "after upload."
                )

            if int(remote_size) != int(
                artifact_info["size_bytes"]
            ):
                raise X1CUploadOnlyError(
                    "Remote SIZE verification failed."
                )

            remote_bytes = (
                retrieve_verified_bytes(
                    ftp,
                    remote_name,
                    int(remote_size),
                )
            )

            remote_sha = hashlib.sha256(
                remote_bytes
            ).hexdigest()

            if (
                remote_sha
                != artifact_info["sha256"]
            ):
                raise X1CUploadOnlyError(
                    "Remote SHA256 verification failed."
                )

        return {
            "schema_version": "1.0.0",
            "module": "M4",
            "stage": "x1c_upload_only",

            "status":
                "x1c_upload_only_verified",

            "device": {
                "device_id": device_id,
                "ip_address": ip_address,
            },

            "artifact": artifact_info,

            "remote": {
                "directory": "/",
                "name": remote_name,
                "path": "/" + remote_name,
                "size_bytes": int(
                    remote_size
                ),
                "sha256": remote_sha,
                "reused_existing":
                    reused,
            },

            "verification": {
                "size_match":
                    int(remote_size)
                    == int(
                        artifact_info[
                            "size_bytes"
                        ]
                    ),

                "sha256_match":
                    remote_sha
                    == artifact_info[
                        "sha256"
                    ],
            },

            "policy": {
                "ftps_upload_performed":
                    not reused,

                "mqtt_publish_count": 0,
                "printer_command_sent": False,
                "print_started": False,
                "access_code_stored": False,
            },
        }

    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def cli_main(
    argv: list[str] | None = None,
) -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Upload an audited X1C gcode.3mf "
            "by FTPS and verify remote SHA256. "
            "This command cannot start a print."
        )
    )

    parser.add_argument(
        "--artifact",
        required=True,
    )

    parser.add_argument(
        "--preprint-report",
        required=True,
    )

    parser.add_argument(
        "--ip",
        required=True,
    )

    parser.add_argument(
        "--device-id",
        required=True,
    )

    parser.add_argument(
        "--evidence-dir",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args(argv)

    access_code = getpass.getpass(
        "X1C Access Code "
        "(hidden, never stored): "
    ).strip()

    report_path = (
        Path(args.report)
        .expanduser()
        .resolve()
    )

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        result = upload_only(
            artifact=Path(args.artifact),
            preprint_report=Path(
                args.preprint_report
            ),
            ip_address=args.ip,
            device_id=args.device_id,
            evidence_dir=Path(
                args.evidence_dir
            ),
            access_code=access_code,
        )

        report_path.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )

        print()
        print(
            "M4_X1C_UPLOAD_ONLY=PASS"
        )

        return 0

    except Exception as exc:
        failure = {
            "schema_version": "1.0.0",
            "module": "M4",
            "stage": "x1c_upload_only",
            "status": "x1c_upload_only_error",
            "error_type":
                type(exc).__name__,
            "error":
                str(exc),

            "policy": {
                "mqtt_publish_count": 0,
                "printer_command_sent": False,
                "print_started": False,
                "access_code_stored": False,
            },
        }

        report_path.write_text(
            json.dumps(
                failure,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        print(
            json.dumps(
                failure,
                ensure_ascii=False,
                indent=2,
            )
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
