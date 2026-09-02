from __future__ import annotations

import html
import hashlib
import json
import os
import re
import tempfile
import zipfile

from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr


_MODEL_SETTINGS_MEMBER = (
    "Metadata/model_settings.config"
)

TEXTURED_PEI_PLATE = "Textured PEI Plate"
TEXTURED_PEI_BED_TYPE = "textured_plate"
TEXTURED_PEI_PLA_BED_TEMPERATURE_C = 55
TEXTURED_PEI_Z_COMPENSATION_MM = -0.04

_PROJECT_SETTINGS_MEMBER = "Metadata/project_settings.config"
_SLICE_INFO_MEMBER = "Metadata/slice_info.config"
_CONTENT_TYPES_MEMBER = "[Content_Types].xml"
_ROOT_MODEL_MEMBER = "3D/3dmodel.model"
_GCODE_MEMBER_RE = re.compile(r"^Metadata/plate_\d+\.gcode$")

_METADATA_LINE_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"<metadata\s+"
    r'key="(?P<key>.*?)"\s+'
    r'value="(?P<value>.*)"\s*/>\s*$'
)


def _normalise_bambu_metadata_line(
    line: str,
) -> str:
    match = _METADATA_LINE_RE.match(line)

    if match is None:
        return line

    indent = match.group("indent")
    key = html.unescape(match.group("key"))
    value = html.unescape(match.group("value"))

    return (
        indent
        + "<metadata key="
        + quoteattr(key)
        + " value="
        + quoteattr(value)
        + "/>"
    )


def repair_bambu_model_settings_xml(
    project_path: Path,
) -> dict[str, object]:
    """Repair Bambu's unescaped model-settings attributes atomically.

    Bambu Studio CLI 2.7.x may return success while emitting an invalid
    ``Metadata/model_settings.config``.  Only that member is normalised and
    every other ZIP entry is copied with its original metadata.
    """

    project_path = (
        Path(project_path)
        .expanduser()
        .resolve()
    )

    if not project_path.is_file():
        raise RuntimeError(
            "Project missing before XML validation: "
            + str(project_path)
        )

    if not zipfile.is_zipfile(project_path):
        raise RuntimeError(
            "Project is not a valid ZIP/3MF: "
            + str(project_path)
        )

    with zipfile.ZipFile(
        project_path,
        "r",
    ) as source:
        names = source.namelist()

        if _MODEL_SETTINGS_MEMBER not in names:
            raise RuntimeError(
                "Project is missing "
                + _MODEL_SETTINGS_MEMBER
            )

        original_raw = source.read(
            _MODEL_SETTINGS_MEMBER
        )

    try:
        ET.fromstring(original_raw)

        return {
            "status": "model_settings_xml_valid",
            "repaired": False,
            "member": _MODEL_SETTINGS_MEMBER,
        }
    except ET.ParseError as initial_error:
        initial_error_text = str(initial_error)

    decoded = original_raw.decode(
        "utf-8-sig",
        errors="strict",
    )
    had_final_newline = decoded.endswith(
        ("\n", "\r")
    )
    repaired_text = "\n".join(
        _normalise_bambu_metadata_line(line)
        for line in decoded.splitlines()
    )

    if had_final_newline:
        repaired_text += "\n"

    repaired_raw = repaired_text.encode("utf-8")

    try:
        ET.fromstring(repaired_raw)
    except ET.ParseError as repaired_error:
        raise RuntimeError(
            "model_settings.config remained invalid after "
            "canonical XML repair. "
            f"before={initial_error_text!r}; "
            f"after={str(repaired_error)!r}"
        ) from repaired_error

    # Appending a suffix to a valid 241-character Auto Orient path exceeded
    # Windows MAX_PATH. Keep an independent short, collision-free name beside
    # the project, so atomic replace stays on the same filesystem.
    fd, temporary = tempfile.mkstemp(prefix=".r", suffix=".tmp", dir=project_path.parent)
    os.close(fd)
    tmp_path = Path(temporary)

    try:
        with zipfile.ZipFile(
            project_path,
            "r",
        ) as source, zipfile.ZipFile(
            tmp_path,
            "w",
        ) as target:
            for info in source.infolist():
                data = source.read(info.filename)

                if (
                    info.filename
                    == _MODEL_SETTINGS_MEMBER
                ):
                    data = repaired_raw

                target.writestr(info, data)

        os.replace(tmp_path, project_path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

    with zipfile.ZipFile(
        project_path,
        "r",
    ) as verify_zip:
        verify_raw = verify_zip.read(
            _MODEL_SETTINGS_MEMBER
        )
        ET.fromstring(verify_raw)

    return {
        "status": "model_settings_xml_repaired",
        "repaired": True,
        "member": _MODEL_SETTINGS_MEMBER,
        "initial_parse_error": initial_error_text,
        "original_bytes": len(original_raw),
        "repaired_bytes": len(repaired_raw),
    }


def _rewrite_zip_members(
    project_path: Path,
    replacements: dict[str, bytes],
) -> None:
    """Atomically replace selected members without creating duplicates."""

    fd, temporary = tempfile.mkstemp(
        prefix=".r",
        suffix=".tmp",
        dir=project_path.parent,
    )
    os.close(fd)
    tmp_path = Path(temporary)

    try:
        written: set[str] = set()
        with zipfile.ZipFile(project_path, "r") as source, zipfile.ZipFile(
            tmp_path,
            "w",
        ) as target:
            for info in source.infolist():
                data = replacements.get(info.filename)
                if data is None:
                    data = source.read(info.filename)
                else:
                    written.add(info.filename)
                target.writestr(info, data)

            for name, data in replacements.items():
                if name not in written:
                    target.writestr(name, data)

        os.replace(tmp_path, project_path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def recalculate_bambu_gcode_md5(project_path: Path) -> dict[str, object]:
    """Recalculate and, when needed, repair Bambu's embedded G-code MD5."""

    project_path = Path(project_path).expanduser().resolve()
    if not project_path.is_file() or not zipfile.is_zipfile(project_path):
        raise RuntimeError(f"G-code 3MF is not a valid ZIP: {project_path}")

    with zipfile.ZipFile(project_path, "r") as archive:
        gcode_members = [
            name for name in archive.namelist() if _GCODE_MEMBER_RE.fullmatch(name)
        ]
        if len(gcode_members) != 1:
            raise RuntimeError(
                "G-code 3MF must contain exactly one plate G-code member; "
                f"found={gcode_members!r}"
            )
        gcode_member = gcode_members[0]
        gcode_raw = archive.read(gcode_member)
        md5_member = gcode_member + ".md5"
        stored = (
            archive.read(md5_member).decode("ascii", errors="strict").strip().upper()
            if md5_member in archive.namelist()
            else None
        )

    calculated = hashlib.md5(gcode_raw).hexdigest().upper()
    rewritten = stored != calculated
    if rewritten:
        _rewrite_zip_members(project_path, {md5_member: calculated.encode("ascii")})

    return {
        "status": "gcode_md5_recalculated",
        "gcode_member": gcode_member,
        "md5_member": md5_member,
        "stored_before": stored,
        "calculated": calculated,
        "rewritten": rewritten,
    }


def _as_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _validate_container(
    path: Path,
    *,
    required_members: set[str],
) -> tuple[list[str], dict[str, object]]:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"Bambu 3MF is missing: {path}")
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"Bambu 3MF is not a valid ZIP: {path}")

    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"Bambu 3MF has a corrupt ZIP member: {bad_member}")

        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise RuntimeError(
                "Bambu 3MF contains duplicate ZIP members: " + ", ".join(duplicates)
            )

        missing = sorted(required_members - set(names))
        if missing:
            raise RuntimeError(
                "Bambu 3MF is incomplete; missing: " + ", ".join(missing)
            )

        xml_members = [
            name
            for name in names
            if name.lower().endswith((".xml", ".model", ".rels"))
            or name in {_MODEL_SETTINGS_MEMBER, _SLICE_INFO_MEMBER}
        ]
        json_members = [
            name
            for name in names
            if name.lower().endswith(".json") or name == _PROJECT_SETTINGS_MEMBER
        ]

        for name in xml_members:
            try:
                ET.fromstring(archive.read(name))
            except ET.ParseError as exc:
                raise RuntimeError(
                    f"Bambu 3MF contains invalid XML in {name}: {exc}"
                ) from exc

        decoded_json: dict[str, object] = {}
        for name in json_members:
            try:
                value = json.loads(archive.read(name).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Bambu 3MF contains invalid JSON in {name}: {exc}"
                ) from exc
            decoded_json[name] = value

    return names, {
        "zip_integrity": True,
        "duplicate_members_absent": True,
        "required_members_present": True,
        "xml_valid": True,
        "json_valid": True,
        "member_count": len(names),
        "xml_member_count": len(xml_members),
        "json_member_count": len(json_members),
        "decoded_json": decoded_json,
    }


def _validate_textured_pla_settings(
    settings: object,
    *,
    expected_bed_type: str,
) -> dict[str, object]:
    if not isinstance(settings, dict):
        raise RuntimeError("project_settings.config must contain a JSON object.")
    actual_bed_type = settings.get("curr_bed_type")
    if actual_bed_type != expected_bed_type:
        raise RuntimeError(
            "Bambu project build plate mismatch: "
            f"expected={expected_bed_type!r}, actual={actual_bed_type!r}"
        )

    filament_types = [value.upper() for value in _as_values(settings.get("filament_type"))]
    pla_indexes = [index for index, value in enumerate(filament_types) if value == "PLA"]
    if not pla_indexes:
        raise RuntimeError("Textured PEI preparation requires a PLA filament setting.")

    expected_temperature = str(TEXTURED_PEI_PLA_BED_TEMPERATURE_C)
    temperatures: dict[str, list[str]] = {}
    for key in ("textured_plate_temp", "textured_plate_temp_initial_layer"):
        values = _as_values(settings.get(key))
        temperatures[key] = values
        if not values or any(
            index >= len(values) or values[index] != expected_temperature
            for index in pla_indexes
        ):
            raise RuntimeError(
                f"PLA {key} must be {expected_temperature} C for Textured PEI Plate; "
                f"actual={values!r}"
            )

    return {
        "curr_bed_type": actual_bed_type,
        "filament_types": filament_types,
        "pla_bed_temperature_c": TEXTURED_PEI_PLA_BED_TEMPERATURE_C,
        "temperature_settings": temperatures,
    }


def validate_bambu_project_3mf(
    path: Path,
    *,
    expected_bed_type: str = TEXTURED_PEI_PLATE,
) -> dict[str, object]:
    """Validate the editable project that is exposed by the download layer."""

    path = Path(path).expanduser().resolve()
    _, container = _validate_container(
        path,
        required_members={
            _CONTENT_TYPES_MEMBER,
            _ROOT_MODEL_MEMBER,
            _MODEL_SETTINGS_MEMBER,
            _PROJECT_SETTINGS_MEMBER,
            _SLICE_INFO_MEMBER,
        },
    )
    decoded_json = container.pop("decoded_json")
    if not isinstance(decoded_json, dict):
        raise RuntimeError("Bambu project JSON validation result is invalid.")
    plate = _validate_textured_pla_settings(
        decoded_json[_PROJECT_SETTINGS_MEMBER],
        expected_bed_type=expected_bed_type,
    )
    return {
        "status": "bambu_project_3mf_validated",
        "path": str(path),
        "container": container,
        "build_plate": plate,
    }


def validate_bambu_gcode_3mf(
    path: Path,
    *,
    expected_bed_type: str = TEXTURED_PEI_PLATE,
    expected_bed_type_code: str = TEXTURED_PEI_BED_TYPE,
) -> dict[str, object]:
    """Validate ZIP/XML/JSON/MD5 and Textured PEI G-code consistency."""

    path = Path(path).expanduser().resolve()
    names, container = _validate_container(
        path,
        required_members={
            _CONTENT_TYPES_MEMBER,
            _ROOT_MODEL_MEMBER,
            _MODEL_SETTINGS_MEMBER,
            _PROJECT_SETTINGS_MEMBER,
            _SLICE_INFO_MEMBER,
        },
    )
    gcode_members = [name for name in names if _GCODE_MEMBER_RE.fullmatch(name)]
    if len(gcode_members) != 1:
        raise RuntimeError(
            "G-code 3MF must contain exactly one plate G-code member; "
            f"found={gcode_members!r}"
        )
    gcode_member = gcode_members[0]
    md5_member = gcode_member + ".md5"
    plate_json_member = gcode_member.removesuffix(".gcode") + ".json"
    for required in (md5_member, plate_json_member):
        if required not in names:
            raise RuntimeError(f"G-code 3MF is missing {required}.")

    decoded_json = container.pop("decoded_json")
    if not isinstance(decoded_json, dict):
        raise RuntimeError("Bambu G-code JSON validation result is invalid.")
    plate = _validate_textured_pla_settings(
        decoded_json[_PROJECT_SETTINGS_MEMBER],
        expected_bed_type=expected_bed_type,
    )
    plate_json = decoded_json.get(plate_json_member)
    if not isinstance(plate_json, dict):
        raise RuntimeError(f"{plate_json_member} must contain a JSON object.")
    actual_bed_type_code = plate_json.get("bed_type")
    if actual_bed_type_code != expected_bed_type_code:
        raise RuntimeError(
            "G-code plate metadata mismatch: "
            f"expected={expected_bed_type_code!r}, actual={actual_bed_type_code!r}"
        )

    with zipfile.ZipFile(path, "r") as archive:
        gcode_raw = archive.read(gcode_member)
        stored_md5 = archive.read(md5_member).decode("ascii", errors="strict").strip().upper()
    calculated_md5 = hashlib.md5(gcode_raw).hexdigest().upper()
    if stored_md5 != calculated_md5:
        raise RuntimeError(
            "Embedded G-code MD5 mismatch: "
            f"stored={stored_md5!r}, calculated={calculated_md5!r}"
        )

    gcode_text = gcode_raw.decode("utf-8-sig", errors="strict")
    temperature = TEXTURED_PEI_PLA_BED_TEMPERATURE_C
    gcode_bed_names = re.findall(
        r"(?m)^;\s*curr_bed_type\s*=\s*(.*?)\s*$",
        gcode_text,
    )
    m140_temperatures = [
        float(value)
        for value in re.findall(r"(?m)^M140\s+S([-+\d.]+)(?:\s|$)", gcode_text)
    ]
    m190_temperatures = [
        float(value)
        for value in re.findall(r"(?m)^M190\s+S([-+\d.]+)(?:\s|$)", gcode_text)
    ]
    active_bed_temperatures = [
        value
        for value in (*m140_temperatures, *m190_temperatures)
        if value > 0
    ]
    z_compensations = [
        float(value)
        for value in re.findall(r"(?m)^G29\.1\s+Z([-+\d.]+)(?:\s|$)", gcode_text)
    ]
    active_z_compensations = [
        value for value in z_compensations if abs(value) > 1e-9
    ]
    checks = {
        "gcode_bed_name": (
            bool(gcode_bed_names)
            and set(gcode_bed_names) == {expected_bed_type}
        ),
        "gcode_bed_temperature": (
            bool(m140_temperatures)
            and bool(m190_temperatures)
            and bool(active_bed_temperatures)
            and all(abs(value - temperature) < 1e-9 for value in active_bed_temperatures)
        ),
        "gcode_z_compensation": (
            bool(active_z_compensations)
            and all(
                abs(value - TEXTURED_PEI_Z_COMPENSATION_MM) < 1e-9
                for value in active_z_compensations
            )
        ),
        "gcode_md5": stored_md5 == calculated_md5,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(
            "G-code does not match the Textured PEI project settings: "
            + ", ".join(failed)
        )

    return {
        "status": "bambu_gcode_3mf_validated",
        "path": str(path),
        "container": container,
        "build_plate": {
            **plate,
            "bed_type": actual_bed_type_code,
            "z_compensation_mm": TEXTURED_PEI_Z_COMPENSATION_MM,
        },
        "gcode": {
            "member": gcode_member,
            "size_bytes": len(gcode_raw),
            "md5_member": md5_member,
            "md5": calculated_md5,
        },
        "checks": checks,
        "observed": {
            "gcode_bed_names": gcode_bed_names,
            "m140_temperatures_c": m140_temperatures,
            "m190_temperatures_c": m190_temperatures,
            "z_compensations_mm": z_compensations,
        },
    }


def finalize_bambu_gcode_3mf(
    path: Path,
    *,
    expected_bed_type: str = TEXTURED_PEI_PLATE,
) -> dict[str, object]:
    """Repair Bambu XML, recalculate MD5, then validate the committed artifact."""

    path = Path(path).expanduser().resolve()
    xml_repair = repair_bambu_model_settings_xml(path)
    md5 = recalculate_bambu_gcode_md5(path)
    validation = validate_bambu_gcode_3mf(
        path,
        expected_bed_type=expected_bed_type,
    )
    return {
        "status": "bambu_gcode_3mf_finalized",
        "xml_repair": xml_repair,
        "gcode_md5": md5,
        "validation": validation,
    }
