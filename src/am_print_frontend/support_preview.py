"""Build a browser preview from the receipt-verified support toolpaths."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import zipfile

from am_print_executor.gcode_support_continuity import parse_extrusion_segments
from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame


class SupportPreviewError(ValueError):
    pass


def build_support_preview(
    files: Mapping[str, tuple[Path, str]],
) -> dict[str, Any]:
    """Return only real support extrusions, mapped into the STL world frame."""
    try:
        gcode_path, gcode_hash = files["gcode"]
        _, stl_hash = files["stl"]
        mapping = gate_report_in_geometry_frame({}, gcode_path)["coordinate_mapping"]
        dx, dy = (float(value) for value in mapping["translation_xy_mm"])
        with zipfile.ZipFile(gcode_path) as archive:
            entries = [name for name in archive.namelist() if name.lower().endswith(".gcode")]
            preferred = [name for name in entries if name.lower().endswith("metadata/plate_1.gcode")]
            if len(preferred) != 1:
                raise SupportPreviewError("最终切片缺少唯一的打印刀路。")
            text = archive.read(preferred[0]).decode("utf-8", errors="strict")
    except SupportPreviewError:
        raise
    except (KeyError, OSError, ValueError, UnicodeError, zipfile.BadZipFile) as exc:
        raise SupportPreviewError("无法从最终切片读取真实支撑。") from exc

    paths: list[list[float | int]] = []
    interface_count = 0
    for segment in parse_extrusion_segments(text):
        feature = " ".join(segment.feature.lower().split())
        if "support" not in feature or "unsupported" in feature:
            continue
        interface = int("interface" in feature)
        interface_count += interface
        width = float(segment.line_width_mm or 0.42)
        paths.append([
            round(segment.x1 + dx, 4),
            round(segment.y1 + dy, 4),
            round(segment.z, 4),
            round(segment.x2 + dx, 4),
            round(segment.y2 + dy, 4),
            round(segment.z, 4),
            round(width, 4),
            interface,
        ])
    if not paths:
        raise SupportPreviewError("最终切片没有可显示的支撑刀路。")
    return {
        "schema": "actual_support_toolpaths_v1",
        "support_mode": "detachable",
        "source": {"gcode_sha256": gcode_hash, "stl_sha256": stl_hash},
        "coordinate_mapping": mapping,
        "segment_count": len(paths),
        "interface_segment_count": interface_count,
        "paths": paths,
    }
