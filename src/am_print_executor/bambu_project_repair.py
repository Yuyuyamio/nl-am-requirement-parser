from __future__ import annotations

import html
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
