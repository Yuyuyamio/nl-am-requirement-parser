from __future__ import annotations

import argparse
import getpass
import hashlib
import ipaddress
import json
import os
import re
import shutil
import ssl
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping
from am_print_executor.bambu_auto_orient import (
    BambuAutoOrientError,
    auto_orient_with_bambu_cli,
)
from am_print_executor.bambu_headless_cli import (
    run_bambu_cli,
)
from am_print_executor.bambu_project_repair import (
    repair_bambu_model_settings_xml,
)
from am_print_executor.bambu_profile_resolver import materialize_bambu_cli_profiles
from am_print_executor.ams_mapping import AmsMappingError, external_spool_wire_mapping, to_x1c_wire_mapping

def _resolve_project_root() -> Path:
    """Resolve repository root without a machine-specific hard-coded path."""
    override = os.environ.get("NL_AM_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    # developer_mode_backend_v1120.py
    #   repo/src/am_print_executor/<this file>
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _resolve_project_root()
REQUEST_ID = "M2-1E4B2301FADD"
EXPECTED_DEVICE_ID = "00M09A3A1700722"
TASK_DIR = PROJECT_ROOT / "outputs" / "m4" / REQUEST_ID

PREFLIGHT_REPORT = TASK_DIR / "m4_developer_backend_preflight_v1000.json"
DEFAULT_REMOTE_DIR = "/"


class DeveloperBackendError(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise DeveloperBackendError(f"Required JSON missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DeveloperBackendError(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise DeveloperBackendError(f"Expected JSON object: {path}")
    return obj


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        old = path.read_text(encoding="utf-8-sig")
        if old == text:
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = path.with_name(path.name + f".pre_{stamp}.bak")
        backup.write_text(old, encoding="utf-8")
    path.write_text(text, encoding="utf-8")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _safe_remote_name(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    if not base.lower().endswith(".gcode.3mf"):
        stem = re.sub(r"\.3mf$", "", base, flags=re.I)
        base = stem + ".gcode.3mf"
    return base[:120]


def discover_bambu_studio() -> Path | None:
    env = os.environ.get("BAMBU_STUDIO_EXE")
    candidates = []
    if env:
        candidates.append(Path(env))

    which = shutil.which("bambu-studio") or shutil.which("bambu-studio.exe")
    if which:
        candidates.append(Path(which))

    candidates.extend(
        [
            Path(r"C:\Program Files\Bambu Studio\bambu-studio.exe"),
            Path(r"C:\Program Files\Bambu Studio\BambuStudio.exe"),
            Path(r"C:\Users\PC\AppData\Local\Programs\Bambu Studio\bambu-studio.exe"),
            Path(r"C:\Users\PC\AppData\Local\Programs\Bambu Studio\BambuStudio.exe"),
        ]
    )

    for c in candidates:
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            pass
    return None


def inspect_gcode_3mf(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise DeveloperBackendError(f"Artifact missing: {path}")
    if not zipfile.is_zipfile(path):
        raise DeveloperBackendError(f"Not a valid 3MF ZIP: {path}")

    with zipfile.ZipFile(path, "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            raise DeveloperBackendError(f"Corrupt 3MF member: {bad}")
        names = zf.namelist()

    entries = sorted(
        n for n in names
        if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)
    )
    if not entries:
        raise DeveloperBackendError(
            "3MF contains no Metadata/plate_<n>.gcode; it is not a sliced printable .gcode.3mf."
        )

    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "gcode_entries": entries,
        "plate_count": len(entries),
    }


def discover_bambu_profiles(studio_exe: Path) -> dict[str, Path]:
    """Discover official X1C 0.4 mm / 0.20 Standard / PLA profiles.

    Profile selection is derived from the installed Bambu Studio resources,
    not from a user-specific absolute profile path.
    """
    studio_exe = Path(studio_exe).expanduser().resolve()

    profile_root = (
        studio_exe.parent
        / "resources"
        / "profiles"
        / "BBL"
    )

    machine = (
        profile_root
        / "machine"
        / "Bambu Lab X1 Carbon 0.4 nozzle.json"
    )

    process = (
        profile_root
        / "process"
        / "0.20mm Standard @BBL X1C.json"
    )

    missing = []

    if not machine.is_file():
        missing.append(str(machine))

    if not process.is_file():
        missing.append(str(process))

    if missing:
        raise DeveloperBackendError(
            "Required Bambu X1C profiles are missing: "
            + "; ".join(missing)
        )

    filament_root = profile_root / "filament"

    candidates = []

    if filament_root.is_dir():
        for path in filament_root.rglob("*.json"):
            name = path.name.lower()

            if "bambu pla basic" in name:
                candidates.append(path)
                continue

            if name in {
                "generic pla @base.json",
                "generic pla.json",
            }:
                candidates.append(path)

    def filament_rank(path: Path) -> tuple[int, int, str]:
        name = path.name.lower()

        if name == "bambu pla basic @bbl x1c.json":
            return (0, len(name), name)

        if (
            "bambu pla basic" in name
            and "x1c" in name
            and "0.2 nozzle" not in name
        ):
            return (1, len(name), name)

        if (
            "bambu pla basic" in name
            and "@base" in name
        ):
            return (2, len(name), name)

        if name == "generic pla @base.json":
            return (3, len(name), name)

        if name == "generic pla.json":
            return (4, len(name), name)

        return (50, len(name), name)

    candidates = sorted(
        {
            path.resolve()
            for path in candidates
            if path.is_file()
        },
        key=filament_rank,
    )

    if not candidates:
        raise DeveloperBackendError(
            f"No usable PLA profile found under: {filament_root}"
        )

    filament = candidates[0]

    return {
        "profile_root": profile_root.resolve(),
        "machine": machine.resolve(),
        "process": process.resolve(),
        "filament": filament,
    }


def slice_with_bambu_cli(
    input_path: Path,
    output_path: Path,
    *,
    studio_exe: Path | None = None,
    machine_json: Path | None = None,
    process_json: Path | None = None,
    filament_jsons: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """
    Prepare and slice a model through validated headless Bambu CLI stages.

    Production contract:
      * bundled BBL profiles are resolved to complete CLI-ready configs;
      * raw model Auto Orient is isolated from slicing and may be retried;
      * the oriented project is structurally validated before slicing;
      * every Bambu process uses the central hidden, serialised runner;
      * no stale-output glob fallback is allowed;
      * the exact requested output must be created and pass gcode.3mf inspection.
    """

    input_path = Path(input_path).resolve()
    output_path = Path(output_path).resolve()

    if not input_path.is_file():
        raise DeveloperBackendError(
            f"Slice input missing: {input_path}"
        )

    studio = (
        Path(studio_exe).resolve()
        if studio_exe is not None
        else discover_bambu_studio()
    )

    if studio is None or not Path(studio).is_file():
        raise DeveloperBackendError(
            "Bambu Studio CLI executable not found. "
            "Set BAMBU_STUDIO_EXE or pass --studio."
        )

    studio = Path(studio).resolve()

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fils = [
        Path(x)
        for x in (filament_jsons or [])
    ]

    # Resolve omitted profiles from the installed Bambu Studio bundle.
    if (
        machine_json is None
        or process_json is None
        or not fils
    ):
        auto_profiles = discover_bambu_profiles(
            studio
        )

        if machine_json is None:
            machine_json = auto_profiles["machine"]

        if process_json is None:
            process_json = auto_profiles["process"]

        if not fils:
            fils = [
                auto_profiles["filament"]
            ]

    # BBL bundled presets commonly use inheritance/include chains.
    # Materialize complete CLI-ready configs before invoking the slicer.
    resolved = materialize_bambu_cli_profiles(
        studio_exe=studio,
        machine_json=Path(machine_json),
        process_json=Path(process_json),
        filament_jsons=[
            Path(x)
            for x in fils
        ],
        cache_dir=(
            output_path.parent
            / ".nl_am_bambu_cli_profiles"
        ),
    )

    machine_json = Path(
        resolved["machine"]
    ).resolve()

    process_json = Path(
        resolved["process"]
    ).resolve()

    fils = [
        Path(x).resolve()
        for x in resolved["filaments"]
    ]

    if not fils:
        raise DeveloperBackendError(
            "No resolved filament profile is available."
        )

    settings_arg = ";".join(
        (
            str(machine_json),
            str(process_json),
        )
    )

    filaments_arg = ";".join(
        str(x)
        for x in fils
    )

    started = time.time()
    auto_orient: dict[str, Any] | None = None
    slice_input = input_path

    if input_path.suffix.lower() != ".3mf":
        output_name = output_path.name
        suffix = ".gcode.3mf"
        base_name = (
            output_name[:-len(suffix)]
            if output_name.lower().endswith(suffix)
            else output_path.stem
        )
        oriented_project = output_path.with_name(
            base_name
            + ".auto_orient.project.3mf"
        )

        try:
            auto_orient = auto_orient_with_bambu_cli(
                studio_exe=studio,
                source_model=input_path,
                output_path=oriented_project,
                machine_json=machine_json,
                process_json=process_json,
                filament_jsons=fils,
                max_attempts=3,
                timeout=600,
            )
        except BambuAutoOrientError as exc:
            raise DeveloperBackendError(
                "Bambu Studio Auto Orient stage failed.\n"
                + str(exc)
            ) from exc

        slice_input = oriented_project

    cmd: list[str] = [
        str(studio),
        "--load-settings",
        settings_arg,
        "--load-filaments",
        filaments_arg,
        "--slice",
        "0",
        "--debug",
        "5",
        "--export-3mf",
        str(output_path),
        str(slice_input),
    ]

    slice_attempts: list[dict[str, Any]] = []
    slice_result = None
    artifact = None
    artifact_xml_repair = None

    for attempt in range(1, 3):
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass

        result = run_bambu_cli(
            cmd,
            expected_outputs=[output_path],
            cwd=output_path.parent,
            timeout=1800,
        )
        validation_error = None

        if result.success:
            try:
                artifact_xml_repair = (
                    repair_bambu_model_settings_xml(
                        output_path
                    )
                )
                artifact = inspect_gcode_3mf(
                    output_path
                )
            except Exception as exc:
                validation_error = (
                    f"{type(exc).__name__}: {exc}"
                )
        else:
            validation_error = (
                "Bambu CLI returned failure or did not create "
                "the exact requested output."
            )

        slice_attempts.append(
            {
                "attempt": attempt,
                "success": (
                    result.success
                    and validation_error is None
                ),
                "returncode_raw": result.raw_exit,
                "returncode_signed": result.signed_exit,
                "outputs_exist": result.outputs_exist,
                "lock_wait_seconds": (
                    result.lock_wait_seconds
                ),
                "process_settle_seconds": (
                    result.process_settle_seconds
                ),
                "validation_error": validation_error,
                "stdout_tail": result.stdout[-3000:],
                "stderr_tail": result.stderr[-3000:],
            }
        )

        if (
            result.success
            and validation_error is None
            and artifact is not None
        ):
            slice_result = result
            break

        if attempt < 2:
            time.sleep(0.25)

    if slice_result is None or artifact is None:
        raise DeveloperBackendError(
            "Bambu Studio headless slicing failed after retries.\n"
            f"command={cmd!r}\n"
            f"attempts={slice_attempts!r}"
        )

    return {
        "status": "slice_complete",
        "pipeline": "bambu_auto_orient_then_slice_v2",
        "studio_exe": str(studio),
        "command": cmd,
        "elapsed_seconds": round(
            time.time() - started,
            3,
        ),
        "returncode_raw": slice_result.raw_exit,
        "returncode_signed": slice_result.signed_exit,
        "artifact": artifact,
        "artifact_xml_repair": (
            artifact_xml_repair
        ),
        "auto_orient": auto_orient,
        "auto_orient_applied": (
            auto_orient is not None
        ),
        "slice_input": str(slice_input),
        "slice_attempts": slice_attempts,
        "cli_invocation_count": (
            len(slice_attempts)
            + (
                int(auto_orient["attempt_count"])
                if auto_orient is not None
                else 0
            )
        ),
        "resolved_machine_json": str(
            machine_json
        ),
        "resolved_process_json": str(
            process_json
        ),
        "resolved_filament_jsons": [
            str(x)
            for x in fils
        ],
        "stdout_tail": slice_result.stdout[-2000:],
        "stderr_tail": slice_result.stderr[-2000:],
    }



def _retr_remote_bytes(
    ftp,
    remote_name: str,
    expected_size: int | None,
) -> bytes:
    # FTPS_RETR_SIZE_FALLBACK_V1200
    sink = bytearray()
    try:
        ftp.retrbinary(f"RETR {remote_name}", sink.extend)
    except TimeoutError:
        if expected_size is None or len(sink) != expected_size:
            raise

    if expected_size is not None and len(sink) != expected_size:
        raise DeveloperBackendError(
            "Remote RETR size verification failed: "
            f"received={len(sink)} expected={expected_size}"
        )
    return bytes(sink)


def _live_identity_from_connection(
    connection: Mapping[str, Any],
    *,
    project_root: Path,
    expected_device_id: str | None,
) -> dict[str, Any]:
    """Validate the safe, in-memory result of an authenticated MQTT session."""

    required_facts = (
        "connected",
        "authenticated",
        "printer_state_observed",
        "credential_available",
    )
    if any(connection.get(name) is not True for name in required_facts):
        raise DeveloperBackendError(
            "A current authenticated Printer Connection is required before upload."
        )

    printer_ip = str(connection.get("printer_ip") or "").strip()
    try:
        parsed_ip = ipaddress.ip_address(printer_ip)
    except ValueError as exc:
        raise DeveloperBackendError(
            "Printer Connection returned an invalid printer IP."
        ) from exc
    if parsed_ip.version != 4 or not parsed_ip.is_private:
        raise DeveloperBackendError(
            "Printer upload is restricted to a private IPv4 address."
        )

    device_id = str(connection.get("device_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{5,63}", device_id):
        raise DeveloperBackendError(
            "Printer Connection returned an invalid DEVICE_ID."
        )
    if expected_device_id is not None and device_id != expected_device_id:
        raise DeveloperBackendError(
            "Locked printer DEVICE_ID mismatch: "
            f"expected={expected_device_id!r} observed={device_id!r}."
        )

    certificate = str(
        connection.get("tls_certificate_sha256") or ""
    ).replace(":", "").upper()
    if not re.fullmatch(r"[0-9A-F]{64}", certificate):
        raise DeveloperBackendError(
            "Printer Connection has no valid MQTT TLS certificate fingerprint."
        )

    return {
        "task_dir": (
            project_root / "outputs" / "printer_connections" / device_id
        ),
        "ip_address": str(parsed_ip),
        "device_id": device_id,
        "tls_certificate_sha256": certificate,
        "identity_source": "authenticated_printer_connection",
    }


def _ftps_upload_verified(
    artifact_path: Path,
    access_code: str,
    *,
    remote_name: str | None = None,
    project_root: Path | None = None,
    identity_request_id: str | None = None,
    expected_device_id: str | None = EXPECTED_DEVICE_ID,
    printer_connection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from am_print_executor import ftps_probe_v32 as ftps

    if not access_code:
        raise DeveloperBackendError("Access Code cannot be empty.")

    artifact = inspect_gcode_3mf(artifact_path)
    effective_project_root = Path(
        project_root if project_root is not None else PROJECT_ROOT
    ).expanduser().resolve()

    effective_identity_request_id = str(
        identity_request_id if identity_request_id is not None else REQUEST_ID
    ).strip()

    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}",
        effective_identity_request_id,
    ):
        raise DeveloperBackendError(
            "Invalid device identity request ID."
        )

    if printer_connection is None:
        identity = ftps.load_gate1_identity(
            effective_project_root,
            effective_identity_request_id,
        )
        identity["identity_source"] = "legacy_gate1_evidence"
    else:
        identity = _live_identity_from_connection(
            printer_connection,
            project_root=effective_project_root,
            expected_device_id=expected_device_id,
        )
    if (
        expected_device_id is not None
        and identity["device_id"] != expected_device_id
    ):
        raise DeveloperBackendError(
            "Locked printer DEVICE_ID mismatch: "
            f"expected={expected_device_id!r} "
            f"observed={identity['device_id']!r}."
        )

    ip_address = identity["ip_address"]
    task_dir = identity["task_dir"]

    fp = ftps.get_server_fingerprint(ip_address)
    ftps.validate_or_create_ftps_pin(
        task_dir,
        ip_address=ip_address,
        device_id=identity["device_id"],
        fingerprint=fp,
        trusted_fingerprint=identity.get("tls_certificate_sha256"),
    )

    local_path = Path(artifact["path"])
    if remote_name is None:
        remote_name = _safe_remote_name(
            f"{local_path.stem}_{artifact['sha256'][:10]}.gcode.3mf"
        )
    else:
        remote_name = _safe_remote_name(remote_name)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    ftp = ftps.SessionReuseImplicitFTP_TLS(context=context, timeout=120)
    try:
        ftp.connect(ip_address, ftps.FTPS_PORT, timeout=120)
        ftp.login(ftps.FTPS_USERNAME, access_code)
        ftp.prot_p()
        ftp.cwd(DEFAULT_REMOTE_DIR)

        exists, remote_size = ftps._remote_exists_by_size(ftp, remote_name)
        reused = False

        if exists:
            remote_bytes = _retr_remote_bytes(
                ftp,
                remote_name,
                remote_size,
            )
            remote_sha = hashlib.sha256(remote_bytes).hexdigest()
            if remote_sha != artifact["sha256"]:
                raise DeveloperBackendError(
                    f"Remote file already exists with different SHA-256: {remote_name}"
                )
            reused = True
        else:
            with local_path.open("rb") as f:
                ftp.storbinary(f"STOR {remote_name}", f)

            exists, remote_size = ftps._remote_exists_by_size(ftp, remote_name)
            if not exists or remote_size != artifact["size_bytes"]:
                raise DeveloperBackendError(
                    f"Remote SIZE verification failed: observed={remote_size}"
                )

            remote_bytes = _retr_remote_bytes(
                ftp,
                remote_name,
                remote_size,
            )
            remote_sha = hashlib.sha256(remote_bytes).hexdigest()
            if remote_sha != artifact["sha256"]:
                raise DeveloperBackendError("Remote SHA-256 verification failed.")

        return {
            "status": "upload_verified",
            "printer_ip": ip_address,
            "device_id": identity["device_id"],
            "remote_dir": DEFAULT_REMOTE_DIR,
            "remote_name": remote_name,
            "remote_path": f"{DEFAULT_REMOTE_DIR}/{remote_name}",
            "local_sha256": artifact["sha256"],
            "remote_sha256": remote_sha,
            "remote_size_bytes": remote_size,
            "reused_existing_remote": reused,
            "gcode_entries": artifact["gcode_entries"],
            "device_identity_source": identity["identity_source"],
            "device_evidence_project_root": str(effective_project_root),
            "device_evidence_request_id": effective_identity_request_id,
        }
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def _strict_idle_check(print_obj: dict[str, Any]) -> dict[str, Any]:
    state = str(print_obj.get("gcode_state", "")).strip().upper()
    hms = print_obj.get("hms")

    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    nozzle_target = _num(print_obj.get("nozzle_target_temper"))
    bed_target = _num(print_obj.get("bed_target_temper"))

    # Match Bambu Studio's official state classification:
    # active printing: PAUSE / RUNNING / SLICING / PREPARE
    # finished: FINISH / FAILED
    active_states = {
        "PAUSE",
        "RUNNING",
        "SLICING",
        "PREPARE",
    }

    finished_states = {
        "FINISH",
        "FAILED",
    }

    state_is_active = state in active_states

    safe_terminal_state = (
        state == "IDLE"
        or state in finished_states
    )

    print_error_zero = (
        print_obj.get("print_error") == 0
    )

    sdcard_available = (
        print_obj.get("sdcard") is True
    )

    hms_empty = (
        isinstance(hms, list)
        and len(hms) == 0
    )

    heater_targets_safe = (
        nozzle_target is not None
        and nozzle_target <= 40
        and bed_target is not None
        and bed_target <= 40
    )

    checks = {
        "official_safe_terminal_state": safe_terminal_state,
        "official_state_not_active": not state_is_active,
        "print_error_zero": print_error_zero,
        "sdcard_available": sdcard_available,
        "hms_empty": hms_empty,
        "heater_targets_safe": heater_targets_safe,
    }

    passed = (
        safe_terminal_state
        and not state_is_active
        and print_error_zero
        and sdcard_available
        and hms_empty
        and heater_targets_safe
    )

    return {
        "passed": passed,
        "checks": checks,
        "observed": {
            "gcode_state": print_obj.get("gcode_state"),
            "print_error": print_obj.get("print_error"),
            "sdcard": print_obj.get("sdcard"),
            "hms": hms,
            "mc_percent": print_obj.get("mc_percent"),
            "mc_remaining_time": print_obj.get("mc_remaining_time"),
            "print_real_action": print_obj.get("print_real_action"),
            "print_gcode_action": print_obj.get("print_gcode_action"),
            "nozzle_target_temper": print_obj.get("nozzle_target_temper"),
            "bed_target_temper": print_obj.get("bed_target_temper"),
        },
        "state_interpretation": (
            "active"
            if state_is_active
            else "finished"
            if state in finished_states
            else "idle"
            if state == "IDLE"
            else "unknown"
        ),
    }


def build_project_file_payload(
    *,
    remote_path: str,
    gcode_entry: str,
    sequence_id: str,
    use_ams: bool,
    ams_mapping: list[int] | None,
) -> dict[str, Any]:
    if not remote_path.startswith("/"):
        raise DeveloperBackendError("Remote print path must be under /cache.")
    if not re.fullmatch(r"Metadata/plate_\d+\.gcode", gcode_entry):
        raise DeveloperBackendError("Invalid plate gcode entry.")

    if use_ams:
        if ams_mapping is None:
            raise DeveloperBackendError(
                "AMS printing requires an explicit "
                "--ams-mapping. Slot mapping is never "
                "auto-guessed."
            )

        try:
            # The caller supplies logical project-filament
            # order only. This device layer is the sole place
            # that generates X1/P1/A1 -1 wire padding.
            mapping_value: Any = to_x1c_wire_mapping(
                ams_mapping
            )
        except AmsMappingError as exc:
            raise DeveloperBackendError(
                str(exc)
            ) from exc

    else:
        # External spool is a device-wire concern and is
        # intentionally represented as the legacy virtual tray.
        mapping_value = external_spool_wire_mapping()

    return {
        "print": {
            "sequence_id": sequence_id,
            "command": "project_file",
            "param": gcode_entry,
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "subtask_id": "0",
            "subtask_name": Path(remote_path).name,
            "file": "",
            "url": f"ftp://{remote_path}",
            "md5": "",
            "timelapse": False,
            "bed_type": "auto",
            "bed_levelling": True,
            "flow_cali": True,
            "vibration_cali": True,
            "layer_inspect": True,
            "ams_mapping": mapping_value,
            "use_ams": bool(use_ams),
        }
    }


def _mqtt_start_once(
    access_code: str,
    upload: dict[str, Any],
    *,
    use_ams: bool,
    ams_mapping: list[int] | None,
    observe_seconds: float = 60.0,
) -> dict[str, Any]:
    import paho.mqtt.client as mqtt
    from am_print_executor.gate4_runtime_v407 import _robust_status_read

    ip_address = upload["printer_ip"]
    device_id = upload["device_id"]

    print_obj, telemetry = _robust_status_read(ip_address, access_code)
    strict = _strict_idle_check(print_obj)
    if not strict["passed"]:
        raise DeveloperBackendError(f"Printer is not in strict safe IDLE state: {strict}")

    gcode_entries = upload["gcode_entries"]
    if len(gcode_entries) != 1:
        raise DeveloperBackendError(
            "V1.0.0 direct backend requires exactly one sliced plate for automatic start."
        )

    sequence_id = str(int(time.time() * 1000))
    payload = build_project_file_payload(
        remote_path=upload["remote_path"],
        gcode_entry=gcode_entries[0],
        sequence_id=sequence_id,
        use_ams=use_ams,
        ams_mapping=ams_mapping,
    )

    connected = threading.Event()
    subscribed = threading.Event()
    active = threading.Event()
    callback_errors: list[str] = []
    ack: dict[str, Any] | None = None
    observed_states: list[str] = []
    events: list[dict[str, Any]] = []

    def reason_failed(reason_code: Any) -> bool:
        marker = getattr(reason_code, "is_failure", None)
        if marker is not None:
            return bool(marker)
        try:
            return int(reason_code) != 0
        except Exception:
            return str(reason_code).lower() not in {"0", "success"}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_failed(reason_code):
            callback_errors.append(f"MQTT connect rejected: {reason_code}")
            connected.set()
            return
        connected.set()
        client.subscribe(f"device/{device_id}/report", qos=0)

    def on_subscribe(client, userdata, mid, reason_codes, properties=None):
        if any(getattr(x, "is_failure", False) for x in (reason_codes or [])):
            callback_errors.append("MQTT subscription refused.")
        subscribed.set()

    def on_message(client, userdata, msg):
        nonlocal ack
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="strict"))
        except Exception:
            return
        pobj = data.get("print") if isinstance(data, dict) else None
        if not isinstance(pobj, dict):
            return

        state = pobj.get("gcode_state")
        if state is not None:
            s = str(state).upper()
            if not observed_states or observed_states[-1] != s:
                observed_states.append(s)
            if s in {"PREPARE", "RUNNING", "SLICING", "PAUSE", "PAUSED"}:
                active.set()

        if (
            str(pobj.get("command", "")) == "project_file"
            and str(pobj.get("sequence_id", "")) == sequence_id
        ):
            ack = dict(pobj)

        events.append(
            {
                "command": pobj.get("command"),
                "sequence_id": pobj.get("sequence_id"),
                "result": pobj.get("result"),
                "reason": pobj.get("reason"),
                "err_code": pobj.get("err_code"),
                "gcode_state": pobj.get("gcode_state"),
                "mc_percent": pobj.get("mc_percent"),
                "mc_remaining_time": pobj.get("mc_remaining_time"),
                "print_error": pobj.get("print_error"),
                "hms": pobj.get("hms"),
                "tray_now": pobj.get("tray_now"),
                "ams_status": pobj.get("ams_status"),
            }
        )

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"nl-am-dev-backend-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    try:
        try:
            client.connect_timeout = 6.0
        except Exception:
            pass
        try:
            client.reconnect_delay_set(min_delay=1, max_delay=3)
        except Exception:
            pass

        client.connect_async(ip_address, 8883, keepalive=60)
        client.loop_start()

        if not connected.wait(45):
            raise DeveloperBackendError("MQTT/TLS connection timeout after automatic retries.")
        if callback_errors:
            raise DeveloperBackendError(callback_errors[-1])
        if not subscribed.wait(12):
            raise DeveloperBackendError("MQTT subscribe timeout.")
        if callback_errors:
            raise DeveloperBackendError(callback_errors[-1])

        info = client.publish(
            f"device/{device_id}/request",
            payload=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            qos=0,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise DeveloperBackendError(f"Local MQTT publish failed rc={info.rc}")
        info.wait_for_publish(timeout=8)
        if not info.is_published():
            raise DeveloperBackendError("MQTT print-start publish did not complete locally.")

        active.wait(observe_seconds)

    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

    result_text = str((ack or {}).get("result", "")).strip().lower()
    rejected = bool(result_text and result_text != "success")
    started = active.is_set() and not rejected

    return {
        "status": (
            "direct_print_started"
            if started
            else "direct_print_rejected"
            if rejected
            else "direct_print_start_outcome_unknown"
        ),
        "published": True,
        "mqtt_publish_count": 1,
        "sequence_id": sequence_id,
        "ack": ack,
        "observed_states": observed_states,
        "events_tail": events[-50:],
        "strict_prestart": strict,
        "status_read_telemetry": telemetry,
        "use_ams": use_ams,
        "ams_mapping": ams_mapping if use_ams else None,
        "payload": payload,
        "automatic_retry_publish_count": 0,
    }


def upload_print_artifact(
    artifact_path: Path,
    access_code: str,
    *,
    remote_name: str | None = None,
    project_root: Path | None = None,
    device_evidence_request_id: str | None = None,
    expected_device_id: str | None = EXPECTED_DEVICE_ID,
    printer_connection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Public, verified upload API for application-level orchestration.

    The LAN access code is consumed in memory and is never included in the
    returned evidence.  Upload is idempotent by remote content hash.
    """

    return _ftps_upload_verified(
        artifact_path,
        access_code,
        remote_name=remote_name,
        project_root=project_root,
        identity_request_id=device_evidence_request_id,
        expected_device_id=expected_device_id,
        printer_connection=printer_connection,
    )


def start_uploaded_print(
    access_code: str,
    upload: dict[str, Any],
    *,
    use_ams: bool,
    ams_mapping: list[int] | None,
    observe_seconds: float = 60.0,
) -> dict[str, Any]:
    """Public single-dispatch API for an already verified upload.

    This function performs the live strict-IDLE gate immediately before the
    one MQTT publish.  Retrying a result with an unknown outcome is the
    responsibility of a durable caller and must never happen automatically.
    """

    return _mqtt_start_once(
        access_code,
        upload,
        use_ams=use_ams,
        ams_mapping=ams_mapping,
        observe_seconds=observe_seconds,
    )


def offline_preflight() -> dict[str, Any]:
    required = [
        PROJECT_ROOT / "src" / "am_print_executor" / "ftps_probe_v32.py",
        PROJECT_ROOT / "src" / "am_print_executor" / "gate4_runtime_v407.py",
        PROJECT_ROOT / "src" / "am_print_executor" / "gate4b_runtime_v411.py",
        PROJECT_ROOT / "src" / "am_print_executor" / "gate8fb_x1c_native_ai_live_monitor_v860.py",
        PROJECT_ROOT / "src" / "am_print_executor" / "gate8fc_native_ai_correlation_v880.py",
    ]
    missing = [str(p) for p in required if not p.is_file()]

    gate4a_path = TASK_DIR / "m4_gate4a_runtime_preflight_v407.json"
    gate4b_path = TASK_DIR / "m4_gate4b_developer_mode_first_print_v411.json"
    native_closeout_path = TASK_DIR / "m4_final_native_ai_closeout_v900.json"

    gate4a = _load_json(gate4a_path)
    gate4b = _load_json(gate4b_path)
    closeout = _load_json(native_closeout_path)

    p4 = gate4a.get("policy", {})
    developer_mode_evidence = (
        p4.get("developer_mode_manually_confirmed") is True
        and p4.get("lan_only_mode_manually_confirmed") is True
    )
    direct_start_evidence = (
        gate4b.get("status") == "first_print_started"
        and gate4b.get("dispatch", {}).get("published") is True
        and int(gate4b.get("dispatch", {}).get("mqtt_publish_count", 0)) == 1
    )
    native_ai_evidence = (
        closeout.get("status") == "native_ai_live_integration_validated"
    )

    studio = discover_bambu_studio()
    warnings = []
    if studio is None:
        warnings.append(
            "Bambu Studio CLI executable was not auto-discovered; set BAMBU_STUDIO_EXE before headless slicing."
        )

    passed = (
        not missing
        and developer_mode_evidence
        and direct_start_evidence
        and native_ai_evidence
    )

    report = {
        "schema_version": "0.1.0",
        "module": "M4",
        "phase": "DEVELOPER-BACKEND",
        "version": "10.0.0",
        "created_unix": time.time(),
        "project_root": str(PROJECT_ROOT),
        "request_id": REQUEST_ID,
        "device_id": EXPECTED_DEVICE_ID,
        "status": (
            "developer_backend_offline_ready"
            if passed
            else "developer_backend_offline_preflight_failed"
        ),
        "passed": passed,
        "validated_existing_evidence": {
            "developer_mode_and_lan_only_confirmed": developer_mode_evidence,
            "previous_direct_print_start_confirmed": direct_start_evidence,
            "native_ai_live_integration_confirmed": native_ai_evidence,
        },
        "required_module_files": {
            "missing": missing,
            "all_present": not missing,
        },
        "bambu_studio_cli": {
            "auto_discovered": studio is not None,
            "path": str(studio) if studio else None,
        },
        "backend_policy": {
            "bambu_studio_gui_required": False,
            "headless_cli_slicing_supported": True,
            "ftps_upload_supported": True,
            "mqtt_project_file_start_supported": True,
            "native_ai_monitoring_retained": True,
            "ams_supported_only_with_explicit_mapping": True,
            "ams_slot_auto_guessing": False,
            "access_code_stored": False,
            "automatic_retry_print_publish": False,
            "git_action": False,
        },
        "warnings": warnings,
        "next_live_acceptance": (
            "one direct Developer Mode print from this backend; preferably one small AMS multi-material job"
        ),
    }

    _write_json(PREFLIGHT_REPORT, report)
    return report


def _parse_mapping(text: str | None) -> list[int] | None:
    if text is None:
        return None
    vals = [x.strip() for x in text.split(",") if x.strip()]
    if not vals:
        return None
    try:
        return [int(x) for x in vals]
    except ValueError as exc:
        raise DeveloperBackendError("--ams-mapping must be comma-separated integers.") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M4 Developer Mode backend: headless slice -> FTPS -> MQTT direct print."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight")

    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("artifact")

    p_slice = sub.add_parser("slice")
    p_slice.add_argument("input")
    p_slice.add_argument("output")
    p_slice.add_argument("--studio")
    p_slice.add_argument("--machine")
    p_slice.add_argument("--process")
    p_slice.add_argument("--filament", action="append", default=[])

    p_print = sub.add_parser("print")
    p_print.add_argument("artifact")
    p_print.add_argument("--remote-name")
    p_print.add_argument("--use-ams", action="store_true")
    p_print.add_argument("--ams-mapping")
    p_print.add_argument("--confirm-start", action="store_true")
    p_print.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
    )
    p_print.add_argument(
        "--job-request-id",
        required=True,
    )
    p_print.add_argument(
        "--device-evidence-request-id",
        default=REQUEST_ID,
    )

    p_full = sub.add_parser("full")
    p_full.add_argument("input")
    p_full.add_argument("output")
    p_full.add_argument("--studio")
    p_full.add_argument("--machine")
    p_full.add_argument("--process")
    p_full.add_argument("--filament", action="append", default=[])
    p_full.add_argument("--remote-name")
    p_full.add_argument("--use-ams", action="store_true")
    p_full.add_argument("--ams-mapping")
    p_full.add_argument("--confirm-start", action="store_true")
    p_full.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
    )
    p_full.add_argument(
        "--job-request-id",
        required=True,
    )
    p_full.add_argument(
        "--device-evidence-request-id",
        default=REQUEST_ID,
    )

    args = parser.parse_args()

    if args.command == "preflight":
        report = offline_preflight()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 2

    if args.command == "inspect":
        print(json.dumps(inspect_gcode_3mf(Path(args.artifact)), ensure_ascii=False, indent=2))
        return 0

    if args.command == "slice":
        result = slice_with_bambu_cli(
            Path(args.input),
            Path(args.output),
            studio_exe=Path(args.studio) if args.studio else None,
            machine_json=Path(args.machine) if args.machine else None,
            process_json=Path(args.process) if args.process else None,
            filament_jsons=[Path(x) for x in args.filament],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command in {"print", "full"}:
        project_root = Path(
            args.project_root
        ).expanduser().resolve()

        job_request_id = str(
            args.job_request_id
        ).strip()

        device_evidence_request_id = str(
            args.device_evidence_request_id
        ).strip()

        request_id_pattern = (
            r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}"
        )

        if not re.fullmatch(
            request_id_pattern,
            job_request_id,
        ):
            raise DeveloperBackendError(
                "Invalid --job-request-id."
            )

        if not re.fullmatch(
            request_id_pattern,
            device_evidence_request_id,
        ):
            raise DeveloperBackendError(
                "Invalid --device-evidence-request-id."
            )

        if not args.confirm_start:
            raise DeveloperBackendError(
                "Real print start is blocked. The caller must explicitly set --confirm-start."
            )

        access_code = getpass.getpass("X1C Access Code (hidden, never stored): ").strip()
        mapping = _parse_mapping(args.ams_mapping)

        if args.command == "full":
            slice_result = slice_with_bambu_cli(
                Path(args.input),
                Path(args.output),
                studio_exe=Path(args.studio) if args.studio else None,
                machine_json=Path(args.machine) if args.machine else None,
                process_json=Path(args.process) if args.process else None,
                filament_jsons=[Path(x) for x in args.filament],
            )
            artifact_path = Path(slice_result["artifact"]["path"])
        else:
            slice_result = None
            artifact_path = Path(args.artifact)

        upload = _ftps_upload_verified(
            artifact_path,
            access_code,
            remote_name=args.remote_name,
            project_root=project_root,
            identity_request_id=device_evidence_request_id,
        )

        start = _mqtt_start_once(
            access_code,
            upload,
            use_ams=bool(args.use_ams),
            ams_mapping=mapping,
        )

        result = {
            "schema_version": "0.1.0",
            "module": "M4",
            "version": "10.0.0",
            "stage": "developer_mode_backend_direct_print",
            "created_unix": time.time(),
            "runtime_context": {
                "project_root": str(project_root),
                "job_request_id": job_request_id,
                "device_evidence_request_id": device_evidence_request_id,
            },
            "slice": slice_result,
            "upload": upload,
            "start": start,
            "access_code_stored": False,
            "bambu_studio_gui_used": False,
        }

        stamp = time.strftime("%Y%m%d_%H%M%S")
        report_task_dir = (
            project_root
            / "outputs"
            / "m4"
            / job_request_id
        )
        report_path = (
            report_task_dir
            / f"m4_developer_backend_live_{stamp}.json"
        )
        _write_json(report_path, result)

        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\nEvidence: {report_path}")
        return 0 if start["status"] == "direct_print_started" else 3

    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeveloperBackendError as exc:
        print(f"\n[DEVELOPER BACKEND FAIL] {exc}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
