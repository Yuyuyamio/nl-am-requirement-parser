from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Mapping

import paho.mqtt.client as mqtt


class BambuHeadlessNativeSenderError(RuntimeError):
    pass


EXPECTED_STUDIO_VERSION = "02.07.01.62"
BRIDGE_RELATIVE_PATH = Path("src/am_print_executor/_native_bambu/bambu_native_bridge.exe")
NETWORK_DLL_RELATIVE = Path("plugins/bambu_networking.dll")
SOURCE_MODE_ENV = "NL_AM_NATIVE_MATERIAL_SOURCE"
SUPPORTED_SOURCE_MODES = {"studio_auto", "auto", "ams", "external_spool"}
MAPPING_CAPACITY = 5


_HEX6_8 = re.compile(r"^#?([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?$")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _powershell_value(script: str) -> str:
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )
    if proc.returncode != 0:
        raise BambuHeadlessNativeSenderError(
            "PowerShell probe failed: "
            + (proc.stderr.strip() or proc.stdout.strip() or f"rc={proc.returncode}")
        )
    return proc.stdout.strip()


def _discover_studio_exe() -> Path:
    candidates = [
        Path(r"C:\Program Files\Bambu Studio\bambu-studio.exe"),
        Path(r"C:\Program Files\Bambu Studio\BambuStudio.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Bambu Studio" / "bambu-studio.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Bambu Studio" / "BambuStudio.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise BambuHeadlessNativeSenderError("Bambu Studio executable was not found.")


def _studio_version(exe: Path) -> str:
    escaped = str(exe).replace("'", "''")
    return _powershell_value(f"(Get-Item -LiteralPath '{escaped}').VersionInfo.FileVersion")


def _discover_data_dir() -> Path:
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "BambuStudio",
        Path(os.environ.get("APPDATA", "")) / "Bambu Studio",
        Path(os.environ.get("LOCALAPPDATA", "")) / "BambuStudio",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Bambu Studio",
    ]
    for candidate in candidates:
        if (candidate / NETWORK_DLL_RELATIVE).is_file():
            return candidate.resolve()
    raise BambuHeadlessNativeSenderError("Bambu Studio data directory/network plugin was not found.")


def _discover_cert(studio_exe: Path) -> Path:
    candidates = [
        studio_exe.parent / "resources" / "cert" / "slicer_base64.cer",
        studio_exe.parent / "resources" / "cert" / "slicer.cer",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise BambuHeadlessNativeSenderError("Bambu Studio slicer certificate was not found.")


def _assert_no_studio_gui() -> None:
    proc = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq bambu-studio.exe"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    text = (proc.stdout or "").lower()
    if "bambu-studio.exe" in text:
        raise BambuHeadlessNativeSenderError(
            "Bambu Studio GUI is running. Close it before the headless native sender uses the stock network plugin."
        )


def native_sender_preflight() -> dict[str, Any]:
    studio = _discover_studio_exe()
    version = _studio_version(studio)
    if version != EXPECTED_STUDIO_VERSION:
        raise BambuHeadlessNativeSenderError(
            f"Unsupported Bambu Studio version: {version!r}; expected {EXPECTED_STUDIO_VERSION!r}."
        )
    data_dir = _discover_data_dir()
    plugin = (data_dir / NETWORK_DLL_RELATIVE).resolve()
    cert = _discover_cert(studio)
    bridge = (_project_root() / BRIDGE_RELATIVE_PATH).resolve()
    if not bridge.is_file():
        raise BambuHeadlessNativeSenderError(f"Native bridge missing: {bridge}")
    return {
        "studio_exe": str(studio),
        "studio_version": version,
        "data_dir": str(data_dir),
        "plugin": str(plugin),
        "cert": str(cert),
        "bridge": str(bridge),
        "gui_required": False,
    }


def prepare_native_handoff(
    gcode_path: Path,
    *,
    printer_connection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    path = Path(gcode_path).expanduser().resolve()
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise BambuHeadlessNativeSenderError(f"Printable .gcode.3mf is missing or invalid: {path}")
    if printer_connection is None:
        raise BambuHeadlessNativeSenderError(
            "Verified printer connection is required for native headless dispatch."
        )
    ip = str(printer_connection.get("printer_ip") or "").strip()
    device_id = str(printer_connection.get("device_id") or "").strip()
    if not ip or not device_id:
        raise BambuHeadlessNativeSenderError(
            "Verified printer connection does not contain printer_ip/device_id."
        )
    pf = native_sender_preflight()
    return {
        "status": "native_headless_handoff_ready",
        "local_path": str(path),
        "printer_ip": ip,
        "device_id": device_id,
        "sender": "bambu_stock_network_plugin_v020701",
        "studio_version": pf["studio_version"],
        "gui_required": False,
    }


def _rgba(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _HEX6_8.fullmatch(value.strip())
    if not match:
        return None
    return (match.group(1) + (match.group(2) or "FF")).upper()


def _material(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value or None


def _read_project_requirements(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path, "r") as zf:
        name = "Metadata/project_settings.config"
        if name not in zf.namelist():
            raise BambuHeadlessNativeSenderError("project_settings.config missing from printable 3MF.")
        settings = json.loads(zf.read(name).decode("utf-8-sig"))
    ids = settings.get("filament_settings_id")
    filament_ids = settings.get("filament_ids")
    colours = settings.get("filament_colour") or settings.get("filament_color")
    materials = settings.get("filament_type")
    if not all(isinstance(value, list) for value in (ids, colours, materials)):
        raise BambuHeadlessNativeSenderError("Printable 3MF filament metadata is incomplete.")
    count = len(ids)
    if len(colours) != count or len(materials) != count:
        raise BambuHeadlessNativeSenderError("Printable 3MF filament metadata length mismatch.")
    if not isinstance(filament_ids, list) or len(filament_ids) != count:
        filament_ids = ["" for _ in range(count)]
    result = []
    for index in range(count):
        rgba = _rgba(colours[index])
        material = _material(materials[index])
        if rgba is None or material is None:
            raise BambuHeadlessNativeSenderError("Printable 3MF contains invalid filament color/type metadata.")
        result.append(
            {
                "index": index,
                "logical_id": index + 1,
                "settings_id": str(ids[index] or ""),
                "filament_id": str(filament_ids[index] or ""),
                "material": material,
                "rgba": rgba,
            }
        )
    if not result:
        raise BambuHeadlessNativeSenderError("Printable 3MF contains no filament presets.")
    return result


def _extract_filament_state(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    root = payload.get("print", payload)
    if not isinstance(root, Mapping):
        return None
    ams = root.get("ams")
    vt = root.get("vt_tray")
    if not isinstance(ams, Mapping) and not isinstance(vt, Mapping):
        return None

    slots: list[dict[str, Any]] = []
    tray_exist_bits = None
    tray_now = None
    if isinstance(ams, Mapping):
        bits = ams.get("tray_exist_bits")
        if isinstance(bits, str):
            try:
                tray_exist_bits = int(bits, 16)
            except ValueError:
                tray_exist_bits = None
        try:
            tray_now = int(str(ams.get("tray_now")))
        except (TypeError, ValueError):
            tray_now = None
        units = ams.get("ams")
        if isinstance(units, list):
            for unit in units:
                if not isinstance(unit, Mapping):
                    continue
                try:
                    ams_id = int(str(unit.get("id")))
                except (TypeError, ValueError):
                    continue
                trays = unit.get("tray")
                if not isinstance(trays, list):
                    continue
                for tray in trays:
                    if not isinstance(tray, Mapping):
                        continue
                    try:
                        slot_id = int(str(tray.get("id")))
                    except (TypeError, ValueError):
                        continue
                    if not 0 <= slot_id <= 3:
                        continue
                    wire_slot = ams_id * 4 + slot_id
                    exists = (
                        bool(tray_exist_bits & (1 << wire_slot))
                        if tray_exist_bits is not None
                        else bool(tray.get("tray_type") or tray.get("tray_info_idx") or tray.get("setting_id"))
                    )
                    slots.append(
                        {
                            "ams_id": ams_id,
                            "slot_id": slot_id,
                            "wire_slot": wire_slot,
                            "exists": exists,
                            "material": _material(tray.get("tray_type")),
                            "rgba": _rgba(tray.get("tray_color")),
                            "filament_id": str(tray.get("tray_info_idx") or ""),
                            "setting_id": str(tray.get("setting_id") or ""),
                        }
                    )

    external = None
    if isinstance(vt, Mapping):
        try:
            vt_id = int(str(vt.get("id")))
        except (TypeError, ValueError):
            vt_id = 254
        external = {
            "id": vt_id,
            "material": _material(vt.get("tray_type")),
            "rgba": _rgba(vt.get("tray_color")),
            "filament_id": str(vt.get("tray_info_idx") or ""),
            "setting_id": str(vt.get("setting_id") or ""),
        }
    return {"ams_slots": slots, "external": external, "tray_now": tray_now}


def _capture_filament_state(
    *,
    ip_address: str,
    device_id: str,
    access_code: str,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    topic = f"device/{device_id}/report"
    connected = threading.Event()
    observations: list[dict[str, Any]] = []
    connection_error: list[str] = []

    def on_connect(client, userdata, flags, reason_code, properties=None):
        try:
            rc = int(reason_code)
        except Exception:
            rc = int(getattr(reason_code, "value", 1))
        if rc != 0:
            connection_error.append(str(reason_code))
            return
        connected.set()
        client.subscribe(topic, qos=0)

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8", errors="strict"))
        except Exception:
            return
        if not isinstance(payload, Mapping):
            return
        state = _extract_filament_state(payload)
        if state is not None:
            observations.append(state)

    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="nl-am-native-source-" + uuid.uuid4().hex[:10],
            protocol=mqtt.MQTTv311,
        )
    except (AttributeError, TypeError):
        client = mqtt.Client(
            client_id="nl-am-native-source-" + uuid.uuid4().hex[:10],
            protocol=mqtt.MQTTv311,
        )
    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(ip_address, 8883, keepalive=30)
    client.loop_start()
    try:
        if not connected.wait(min(8.0, timeout_seconds)):
            raise BambuHeadlessNativeSenderError(
                "Could not establish passive MQTT telemetry session for filament-source resolution."
            )
        deadline = time.monotonic() + timeout_seconds
        solicit_at = time.monotonic() + min(2.0, max(0.5, timeout_seconds / 4.0))
        solicited = False
        while time.monotonic() < deadline:
            if connection_error:
                raise BambuHeadlessNativeSenderError(
                    "MQTT telemetry connection failed: " + connection_error[-1]
                )
            if observations:
                time.sleep(1.0)
                break
            if not solicited and time.monotonic() >= solicit_at:
                # Same non-actuating full-status request already used by the
                # project's established runtime safety reader. This improves
                # reliability when the printer has not emitted an unsolicited
                # full AMS/vt_tray report immediately after subscription.
                payload = {
                    "pushing": {
                        "sequence_id": str(int(time.time() * 1000)),
                        "command": "pushall",
                        "version": 1,
                        "push_target": 1,
                    }
                }
                info = client.publish(
                    f"device/{device_id}/request",
                    json.dumps(payload, separators=(",", ":")),
                    qos=1,
                    retain=False,
                )
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise BambuHeadlessNativeSenderError(
                        f"Read-only pushall status request failed: rc={info.rc}"
                    )
                solicited = True
            time.sleep(0.2)
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass
    if not observations:
        raise BambuHeadlessNativeSenderError(
            "No printer filament/AMS status was observed; source will not be guessed."
        )
    # Prefer the richest report.
    observations.sort(
        key=lambda s: (
            sum(1 for slot in s["ams_slots"] if slot.get("exists")),
            1 if s.get("external") and s["external"].get("material") else 0,
        ),
        reverse=True,
    )
    return observations[0]


def _srgb_channel_to_linear(value: float) -> float:
    value = value / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _rgba_to_lab(value: str) -> tuple[float, float, float]:
    """Convert RRGGBBAA to CIE Lab (D65).

    Bambu Studio's DevMappingUtil uses a perceptual color-distance term after
    its type / setting-id / filament-id priority checks.  This implements the
    same DeltaE76-style ranking in the headless port.
    """
    rgba = _rgba(value)
    if rgba is None:
        raise BambuHeadlessNativeSenderError(f"Invalid RGBA color: {value!r}")
    r = _srgb_channel_to_linear(int(rgba[0:2], 16))
    g = _srgb_channel_to_linear(int(rgba[2:4], 16))
    b = _srgb_channel_to_linear(int(rgba[4:6], 16))

    # sRGB D65
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750) / 1.00000
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883

    delta = 6.0 / 29.0

    def f(t: float) -> float:
        if t > delta**3:
            return t ** (1.0 / 3.0)
        return t / (3.0 * delta**2) + 4.0 / 29.0

    fx, fy, fz = f(x), f(y), f(z)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def _color_distance(a: str, b: str) -> float:
    la = _rgba_to_lab(a)
    lb = _rgba_to_lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


def _studio_mapping_score(
    requirement: Mapping[str, Any],
    slot: Mapping[str, Any],
) -> float | None:
    """Port the priority used by Bambu Studio DevMappingUtil.

    Official priority:
      filament type (hard requirement)
      > exact setting_id/sub-class
      > exact filament_id/class
      > perceptual color distance.
    """
    if not slot.get("exists", True):
        return None
    if slot.get("material") != requirement.get("material"):
        return None

    color_distance = min(
        1000.0,
        _color_distance(
            str(requirement.get("rgba") or "000000FF"),
            str(slot.get("rgba") or "000000FF"),
        ),
    )

    req_setting = str(requirement.get("settings_id") or "")
    slot_setting = str(slot.get("setting_id") or "")
    req_filament = str(requirement.get("filament_id") or "")
    slot_filament = str(slot.get("filament_id") or "")

    if req_setting and slot_setting and req_setting == slot_setting:
        return color_distance
    if req_filament and slot_filament and req_filament == slot_filament:
        return 10000.0 + color_distance
    return 20000.0 + color_distance


def _studio_ams_assignment(
    requirements: list[dict[str, Any]],
    slots: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Headless port of Studio's AMS mapping priority/greedy selection.

    For normal X1C use-AMS dispatch we keep physical AMS slots distinct.
    Ties are deterministic by wire slot.
    """
    available = [
        dict(slot)
        for slot in slots
        if slot.get("exists", True)
        and slot.get("material")
        and slot.get("rgba")
    ]
    if not available:
        return None

    chosen: list[dict[str, Any] | None] = [None] * len(requirements)
    used_wires: set[int] = set()

    # Studio greedily takes the globally best remaining mapping pair.
    while any(item is None for item in chosen):
        best: tuple[float, int, int, dict[str, Any]] | None = None
        for req_idx, requirement in enumerate(requirements):
            if chosen[req_idx] is not None:
                continue
            for slot in available:
                wire = int(slot["wire_slot"])
                if wire in used_wires:
                    continue
                score = _studio_mapping_score(requirement, slot)
                if score is None:
                    continue
                candidate = (score, wire, req_idx, slot)
                if best is None or candidate[:3] < best[:3]:
                    best = candidate

        if best is None:
            return None

        _, wire, req_idx, slot = best
        chosen[req_idx] = slot
        used_wires.add(wire)

    return [dict(item) for item in chosen if item is not None]


def _external_is_compatible(
    requirements: list[dict[str, Any]],
    external: Mapping[str, Any] | None,
) -> bool:
    if len(requirements) != 1 or not isinstance(external, Mapping):
        return False
    if external.get("material") != requirements[0].get("material"):
        return False
    return bool(external.get("rgba"))


def _resolve_source(
    requirements: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, Any]:
    preference = (
        os.environ.get(SOURCE_MODE_ENV, "studio_auto").strip().lower()
        or "studio_auto"
    )
    # Compatibility with v2-v2.4 callers.
    if preference == "auto":
        preference = "studio_auto"
    if preference not in SUPPORTED_SOURCE_MODES:
        raise BambuHeadlessNativeSenderError(
            f"{SOURCE_MODE_ENV} must be one of {sorted(SUPPORTED_SOURCE_MODES)}."
        )

    ams_solution = _studio_ams_assignment(
        requirements,
        state.get("ams_slots", []),
    )
    external = state.get("external")
    external_match = _external_is_compatible(requirements, external)

    if preference == "ams":
        if ams_solution is None:
            raise BambuHeadlessNativeSenderError(
                "AMS was explicitly requested but Bambu Studio mapping priority "
                "found no compatible AMS assignment."
            )
        return {
            "source": "ams",
            "slots": ams_solution,
            "policy": "bambu_studio_dev_mapping",
        }

    if preference == "external_spool":
        if not external_match:
            raise BambuHeadlessNativeSenderError(
                "External spool was explicitly requested but its material is "
                "not compatible with the printable project."
            )
        return {
            "source": "external_spool",
            "external": dict(external),
            "policy": "explicit_external_spool",
        }

    # Studio-auto for an X1C with AMS:
    # follow the normal "Use AMS" print path.  If a compatible AMS mapping
    # exists, external spool is not allowed to win merely because its color is
    # closer or it is currently active. This is the v2.4 regression seen in
    # the user's audit (action_dispatched use_ams=false).
    if ams_solution is not None:
        return {
            "source": "ams",
            "slots": ams_solution,
            "policy": "bambu_studio_dev_mapping",
        }

    # Only fall back to external when Studio-style AMS mapping cannot produce
    # a compatible assignment at all.
    if external_match:
        return {
            "source": "external_spool",
            "external": dict(external),
            "policy": "studio_auto_no_compatible_ams",
        }

    raise BambuHeadlessNativeSenderError(
        "Bambu Studio mapping priority found neither a compatible AMS "
        "assignment nor a compatible external spool."
    )


def _mapping_payload(
    requirements: list[dict[str, Any]],
    resolved: dict[str, Any],
) -> dict[str, Any]:
    pads = MAPPING_CAPACITY - len(requirements)
    if pads < 0:
        raise BambuHeadlessNativeSenderError(
            f"Project uses {len(requirements)} filaments; X1C mapping capacity is {MAPPING_CAPACITY}."
        )

    if resolved["source"] == "ams":
        slots = resolved["slots"]
        mapping = [int(slot["wire_slot"]) for slot in slots] + [-1] * pads
        mapping2 = [
            {"ams_id": int(slot["ams_id"]), "slot_id": int(slot["slot_id"])}
            for slot in slots
        ] + [{"ams_id": 255, "slot_id": 255} for _ in range(pads)]
        info = []
        for requirement, slot in zip(requirements, slots):
            filament_id = requirement["filament_id"] or slot.get("filament_id") or ""
            info.append(
                {
                    "ams": int(slot["wire_slot"]),
                    "filamentId": filament_id,
                    "filamentType": requirement["material"],
                    "nozzleId": 1,
                    "sourceColor": "#" + requirement["rgba"],
                    "targetColor": str(slot.get("rgba") or requirement["rgba"]),
                }
            )
        use_ams = True
    else:
        if len(requirements) != 1:
            raise BambuHeadlessNativeSenderError("External spool mode supports one logical filament only.")
        external = resolved["external"]
        mapping = [-1] * MAPPING_CAPACITY
        mapping2 = [{"ams_id": 255, "slot_id": 0}] + [
            {"ams_id": 255, "slot_id": 255} for _ in range(MAPPING_CAPACITY - 1)
        ]
        requirement = requirements[0]
        info = [
            {
                "ams": -1,
                "filamentId": requirement["filament_id"] or external.get("filament_id") or "",
                "filamentType": requirement["material"],
                "nozzleId": 1,
                "sourceColor": "#" + requirement["rgba"],
                "targetColor": str(external.get("rgba") or requirement["rgba"]),
            }
        ]
        use_ams = False

    return {
        "use_ams": use_ams,
        "policy": resolved.get("policy"),
        "ams_mapping": json.dumps(mapping, separators=(",", ":")),
        "ams_mapping2": json.dumps(mapping2, separators=(",", ":")),
        "ams_mapping_info": json.dumps(info, separators=(",", ":")),
    }


def _strict_idle(ip_address: str, access_code: str) -> dict[str, Any]:
    from am_print_executor.developer_mode_backend_v1120 import _strict_idle_check
    from am_print_executor.gate4_runtime_v407 import _robust_status_read

    print_obj, telemetry = _robust_status_read(ip_address, access_code)
    strict = _strict_idle_check(print_obj)
    if not strict.get("passed"):
        raise BambuHeadlessNativeSenderError(
            f"Printer is not in strict safe IDLE state: {strict}"
        )
    return {"strict_idle": strict, "telemetry": telemetry}


def _run_bridge(
    *,
    bridge: Path,
    plugin: Path,
    data_dir: Path,
    cert: Path,
    gcode: Path,
    device_id: str,
    ip_address: str,
    access_code: str,
    mapping: dict[str, Any],
) -> dict[str, Any]:
    env = os.environ.copy()
    env["BAMBU_NATIVE_ACCESS_CODE"] = access_code
    command = [
        str(bridge),
        "--plugin", str(plugin),
        "--data-dir", str(data_dir),
        "--cert-file", str(cert),
        "--gcode", str(gcode),
        "--dev-id", device_id,
        "--dev-ip", ip_address,
        "--use-ams", "1" if mapping["use_ams"] else "0",
        "--ams-mapping", mapping["ams_mapping"],
        "--ams-mapping2", mapping["ams_mapping2"],
        "--ams-mapping-info", mapping["ams_mapping_info"],
        "--timeout", "120",
    ]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        timeout=180,
    )
    access_code = ""
    events: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    final = next((item for item in reversed(events) if item.get("event") == "result"), None)
    if proc.returncode != 0 or not isinstance(final, Mapping) or final.get("ok") is not True:
        tail = "\n".join((proc.stdout or "").splitlines()[-30:])
        stderr = (proc.stderr or "").strip()
        raise BambuHeadlessNativeSenderError(
            "Stock Bambu native bridge failed.\n"
            f"returncode={proc.returncode}\n"
            f"stdout_tail={tail}\n"
            f"stderr={stderr[-3000:]}"
        )
    return {
        "returncode": proc.returncode,
        "events": events[-60:],
        "result": dict(final),
    }


def _observe_started(ip_address: str, access_code: str, timeout_seconds: float = 60.0) -> dict[str, Any]:
    from am_print_executor.gate4_runtime_v407 import _robust_status_read

    deadline = time.monotonic() + timeout_seconds
    observations = []
    accepted = {"PREPARE", "RUNNING", "PAUSE"}
    while time.monotonic() < deadline:
        print_obj, telemetry = _robust_status_read(ip_address, access_code)
        state = str(print_obj.get("gcode_state") or "").upper()
        observations.append(
            {
                "gcode_state": state,
                "print_error": print_obj.get("print_error"),
                "hms": print_obj.get("hms"),
            }
        )
        if state in accepted:
            return {"confirmed": True, "state": state, "observations": observations[-12:]}
        time.sleep(1.0)
    return {"confirmed": False, "state": None, "observations": observations[-12:]}


def start_native_headless_print(
    access_code: str,
    handoff: Mapping[str, Any],
) -> dict[str, Any]:
    secret = access_code.strip()
    if not secret:
        raise BambuHeadlessNativeSenderError("Printer access code is empty.")
    # Everything up to _run_bridge is provably pre-dispatch. Convert those
    # failures into a known rejection so the durable workflow does not mark
    # the print-start outcome as unknown when nothing has been sent yet.
    try:
        _assert_no_studio_gui()
        pf = native_sender_preflight()
        gcode = Path(str(handoff.get("local_path") or "")).resolve()
        ip_address = str(handoff.get("printer_ip") or "").strip()
        device_id = str(handoff.get("device_id") or "").strip()
        if not gcode.is_file() or not ip_address or not device_id:
            raise BambuHeadlessNativeSenderError("Native handoff is incomplete.")

        idle = _strict_idle(ip_address, secret)
        requirements = _read_project_requirements(gcode)
        filament_state = _capture_filament_state(
            ip_address=ip_address,
            device_id=device_id,
            access_code=secret,
        )
        resolved = _resolve_source(requirements, filament_state)
        mapping = _mapping_payload(requirements, resolved)
    except BambuHeadlessNativeSenderError as exc:
        secret = ""
        return {
            "status": "direct_print_rejected",
            "sender": "bambu_stock_network_plugin_v020701",
            "gui_required": False,
            "reason": str(exc),
            "dispatch_attempted": False,
        }

    # Crossing this boundary may upload/publish through the stock plugin. Any
    # exception from here must remain outcome-unknown and must not auto-retry.
    bridge_result = _run_bridge(
        bridge=Path(pf["bridge"]),
        plugin=Path(pf["plugin"]),
        data_dir=Path(pf["data_dir"]),
        cert=Path(pf["cert"]),
        gcode=gcode,
        device_id=device_id,
        ip_address=ip_address,
        access_code=secret,
        mapping=mapping,
    )

    observed = _observe_started(ip_address, secret)
    secret = ""
    if not observed["confirmed"]:
        return {
            "status": "direct_print_start_outcome_unknown",
            "sender": "bambu_stock_network_plugin_v020701",
            "gui_required": False,
            "material_source": resolved["source"],
            "mapping": mapping,
            "bridge": bridge_result,
            "printer_observation": observed,
            "strict_idle_before_dispatch": idle,
        }
    return {
        "status": "direct_print_started",
        "sender": "bambu_stock_network_plugin_v020701",
        "gui_required": False,
        "material_source": resolved["source"],
        "mapping": mapping,
        "bridge": bridge_result,
        "printer_observation": observed,
        "strict_idle_before_dispatch": idle,
    }


__all__ = [
    "BambuHeadlessNativeSenderError",
    "native_sender_preflight",
    "prepare_native_handoff",
    "start_native_headless_print",
]
