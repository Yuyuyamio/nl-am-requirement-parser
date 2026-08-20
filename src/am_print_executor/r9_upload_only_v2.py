from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import ssl
import time
import zipfile

from pathlib import Path
from typing import Any


class R9UploadError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise R9UploadError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise R9UploadError(f"Invalid JSON: {path}: {exc}") from exc

    if not isinstance(obj, dict):
        raise R9UploadError(f"Expected JSON object: {path}")

    return obj


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def inspect_gcode_3mf(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise R9UploadError(f"Artifact missing: {path}")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise R9UploadError(f"Artifact is not a valid ZIP/3MF: {path}") from exc

    if bad is not None:
        raise R9UploadError(f"Artifact ZIP CRC failure at member: {bad}")

    gcode_entries = sorted(
        name
        for name in names
        if name.startswith("Metadata/plate_")
        and name.endswith(".gcode")
    )

    if gcode_entries != ["Metadata/plate_1.gcode"]:
        raise R9UploadError(
            f"Expected exactly Metadata/plate_1.gcode, got {gcode_entries}"
        )

    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "gcode_entries": gcode_entries,
        "zip_crc_ok": True,
    }


def make_remote_name(local_sha256: str) -> str:
    return f"dog_r9_{local_sha256[:12]}.gcode.3mf"


def upload_verified(
    *,
    ip_address: str,
    access_code: str,
    artifact: Path,
    remote_name: str,
    attempts: int = 3,
) -> dict[str, Any]:
    try:
        from am_print_executor import ftps_probe_v32 as ftps
    except Exception as exc:
        raise R9UploadError(f"Cannot import ftps_probe_v32: {exc}") from exc

    local_sha = sha256_file(artifact)
    local_size = artifact.stat().st_size

    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        ftp = None

        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            ftp = ftps.SessionReuseImplicitFTP_TLS(
                context=context,
                timeout=60,
            )

            ftp.connect(
                ip_address,
                ftps.FTPS_PORT,
                timeout=30,
            )

            ftp.login(
                ftps.FTPS_USERNAME,
                access_code,
            )

            ftp.prot_p()
            ftp.cwd("/")

            exists, remote_size_before = ftps._remote_exists_by_size(
                ftp,
                remote_name,
            )

            reused = False

            if exists:
                sink = bytearray()
                ftp.retrbinary(
                    f"RETR {remote_name}",
                    sink.extend,
                )

                remote_sha = hashlib.sha256(
                    bytes(sink)
                ).hexdigest()

                if remote_sha != local_sha:
                    raise R9UploadError(
                        "Remote file already exists with a different SHA256: "
                        + remote_name
                    )

                reused = True

            if not exists:
                with artifact.open("rb") as src:
                    ftp.storbinary(
                        f"STOR {remote_name}",
                        src,
                    )

            exists_after, remote_size_after = ftps._remote_exists_by_size(
                ftp,
                remote_name,
            )

            if not exists_after:
                raise R9UploadError(
                    "Remote file is missing after upload/reuse check."
                )

            if remote_size_after != local_size:
                raise R9UploadError(
                    "Remote SIZE mismatch: "
                    f"local={local_size}, remote={remote_size_after}"
                )

            sink = bytearray()
            ftp.retrbinary(
                f"RETR {remote_name}",
                sink.extend,
            )

            remote_sha = hashlib.sha256(
                bytes(sink)
            ).hexdigest()

            if remote_sha != local_sha:
                raise R9UploadError(
                    "Remote SHA256 verification failed."
                )

            return {
                "status": "upload_verified",
                "attempt_used": attempt,
                "remote_directory": "/",
                "remote_name": remote_name,
                "remote_path": "/" + remote_name.lstrip("/"),
                "local_sha256": local_sha,
                "remote_sha256": remote_sha,
                "local_size_bytes": local_size,
                "remote_size_bytes": remote_size_after,
                "remote_size_verified": True,
                "remote_sha256_verified": True,
                "uploaded_new": not reused,
                "reused_existing_remote": reused,
            }

        except Exception as exc:
            last_error = exc
            time.sleep(2.0)

        finally:
            if ftp is not None:
                try:
                    ftp.quit()
                except Exception:
                    try:
                        ftp.close()
                    except Exception:
                        pass

    raise R9UploadError(
        "FTPS upload/verification failed after retries: "
        + repr(last_error)
    )


def run_r9(
    *,
    artifact_path: Path,
    r7_report_path: Path,
    r8_report_path: Path,
    expected_sha: str,
    ip_address: str,
    device_id: str,
    report_path: Path,
) -> dict[str, Any]:
    r7 = load_json(r7_report_path)
    r8 = load_json(r8_report_path)

    if r7.get("status") != "r7_ams_mapping_pass":
        raise R9UploadError("Upstream R7 is not PASS.")

    if r8.get("status") != "r8_material_capacity_preflight_pass":
        raise R9UploadError("Upstream R8 is not PASS.")

    if (
        r7.get("device", {}).get("device_id")
        != device_id
    ):
        raise R9UploadError("R7 device_id does not match locked X1C.")

    if (
        r7.get("device", {}).get("ip_address")
        != ip_address
    ):
        raise R9UploadError("R7 printer IP does not match current R9 target.")

    mapping = r7.get("ams_mapping_two_logical_filaments")
    wire = r7.get("x1c_wire_mapping")

    if mapping != [0, 3]:
        raise R9UploadError(
            f"Expected current live logical AMS mapping [0, 3], got {mapping!r}"
        )

    if wire != [0, 3, -1, -1, -1]:
        raise R9UploadError(
            f"Expected current X1C wire mapping [0,3,-1,-1,-1], got {wire!r}"
        )

    artifact = inspect_gcode_3mf(
        Path(artifact_path).resolve()
    )

    if artifact["sha256"].lower() != expected_sha.lower():
        raise R9UploadError(
            "Artifact SHA256 changed since R4/R5/R6 lock."
        )

    print("Enter current X1C LAN Access Code.")
    print("Input is hidden and is NOT written to disk.")
    access_code = getpass.getpass("X1C Access Code: ").strip()

    if not access_code:
        raise R9UploadError("Access Code is empty.")

    remote_name = make_remote_name(
        artifact["sha256"]
    )

    upload = upload_verified(
        ip_address=ip_address,
        access_code=access_code,
        artifact=Path(artifact["path"]),
        remote_name=remote_name,
        attempts=3,
    )

    checks = {
        "r7_pass":
            r7.get("status") == "r7_ams_mapping_pass",

        "r8_pass":
            r8.get("status") == "r8_material_capacity_preflight_pass",

        "artifact_sha_locked":
            artifact["sha256"].lower() == expected_sha.lower(),

        "artifact_zip_crc_ok":
            artifact["zip_crc_ok"],

        "exactly_one_plate_gcode":
            artifact["gcode_entries"] == ["Metadata/plate_1.gcode"],

        "live_mapping_locked":
            mapping == [0, 3],

        "wire_mapping_locked":
            wire == [0, 3, -1, -1, -1],

        "remote_size_verified":
            upload["remote_size_verified"],

        "remote_sha256_verified":
            upload["remote_sha256_verified"],

        "local_remote_sha_identical":
            upload["local_sha256"] == upload["remote_sha256"],
    }

    failed = [
        key
        for key, value in checks.items()
        if not value
    ]

    status = (
        "r9_upload_only_pass"
        if not failed
        else "r9_upload_only_fail"
    )

    report = {
        "schema_version": "r9-upload-only-v2",
        "module": "M4",
        "stage": "R9_UPLOAD_ONLY",
        "created_unix": time.time(),
        "status": status,
        "device": {
            "device_id": device_id,
            "ip_address": ip_address,
            "ftps_port": 990,
        },
        "artifact": artifact,
        "ams": {
            "logical_mapping": mapping,
            "x1c_wire_mapping": wire,
        },
        "upload": upload,
        "checks": checks,
        "failed_checks": failed,
        "policy": {
            "access_code_stored": False,
            "remote_directory": "/",
            "remote_name_is_sha_bound": True,
            "old_r9_remote_artifact_reused_by_name": False,
        },
        "safety": {
            "network_used": True,
            "printer_contacted": True,
            "artifact_uploaded_or_verified_existing": True,
            "mqtt_used": False,
            "project_file_command_sent": False,
            "print_command_sent": False,
            "print_started": False,
            "heating_command_sent": False,
            "motion_command_sent": False,
            "parameter_adjustment_count": 0,
        },
        "next_gate": (
            "R10_FINAL_PREFLIGHT"
            if status == "r9_upload_only_pass"
            else None
        ),
    }

    write_json(report_path, report)
    report["report_path"] = str(Path(report_path).resolve())

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "R9 FTPS upload-only gate. Uploads/verifies the locked X1C "
            "gcode.3mf and never publishes an MQTT project_file command."
        )
    )

    parser.add_argument("--artifact", required=True)
    parser.add_argument("--r7-report", required=True)
    parser.add_argument("--r8-report", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--report", required=True)

    args = parser.parse_args()

    result = run_r9(
        artifact_path=Path(args.artifact),
        r7_report_path=Path(args.r7_report),
        r8_report_path=Path(args.r8_report),
        expected_sha=args.expected_sha,
        ip_address=args.ip,
        device_id=args.device_id,
        report_path=Path(args.report),
    )

    upload = result["upload"]

    print()
    print("=== R9 X1C UPLOAD-ONLY V2 ===")
    print("LOCAL_SHA256=", upload["local_sha256"])
    print("REMOTE_SHA256=", upload["remote_sha256"])
    print("LOCAL_SIZE_BYTES=", upload["local_size_bytes"])
    print("REMOTE_SIZE_BYTES=", upload["remote_size_bytes"])
    print("REMOTE_SIZE_VERIFIED=", upload["remote_size_verified"])
    print("REMOTE_SHA256_VERIFIED=", upload["remote_sha256_verified"])
    print("REMOTE_PATH=", upload["remote_path"])
    print("UPLOADED_NEW=", upload["uploaded_new"])
    print("REUSED_EXISTING_REMOTE=", upload["reused_existing_remote"])
    print("FTPS_ATTEMPT_USED=", upload["attempt_used"])
    print()
    print("FAILED_CHECKS=", result["failed_checks"])
    print("ACCESS_CODE_STORED=False")
    print("MQTT_USED=False")
    print("PROJECT_FILE_COMMAND_SENT=False")
    print("PRINT_COMMAND_SENT=False")
    print("PRINT_STARTED=False")
    print("HEATING_COMMAND_SENT=False")
    print("MOTION_COMMAND_SENT=False")
    print("STATUS=", result["status"])
    print("NEXT_GATE=", result["next_gate"])
    print("REPORT=", result["report_path"])

    return 0 if result["status"] == "r9_upload_only_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
