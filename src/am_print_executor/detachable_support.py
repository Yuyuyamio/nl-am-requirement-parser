"""Conservative breakaway-support design checks, not a physical removal test.

The contact audit uses the actual per-path widths/heights in the sliced file.
Support continuity remains the responsibility of gcode_printability_gate.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import re
import zipfile

from am_print_executor.gcode_support_continuity import parse_extrusion_segments
from am_print_executor.gcode_printability_gate import _is_model, _is_support

SUPPORT_MODES = ("detachable", "permanent")


def validate_support_mode(mode):
    if mode not in SUPPORT_MODES:
        raise ValueError("unknown_support_mode: " + str(mode))


def detachable_process(process: dict, nozzle_mm: float) -> tuple[dict, dict]:
    """A reproducible starting profile; calibration is still required per material."""
    if not math.isfinite(nozzle_mm) or nozzle_mm <= 0:
        raise ValueError("invalid_nozzle_diameter")
    result = dict(process)
    height = round(min(float(process.get("layer_height", .2)), nozzle_mm * .3), 3)
    gap = round(height * 2, 3)
    result.update(enable_support="1", support_type="normal(auto)", support_style="snug",
        support_threshold_angle="30", layer_height=str(height), elefant_foot_compensation="0",
        initial_layer_line_width=str(process.get("outer_wall_line_width", nozzle_mm * 1.05)),
        support_on_build_plate_only="0", support_critical_regions_only="0", support_remove_small_overhang="0",
        support_interface_top_layers="2", support_interface_bottom_layers="2",
        support_interface_spacing=str(nozzle_mm), support_bottom_interface_spacing=str(nozzle_mm),
        support_top_z_distance=str(gap), support_bottom_z_distance=str(gap),
        support_object_xy_distance=str(round(nozzle_mm * .875, 3)),
        support_base_pattern="rectilinear", support_base_pattern_spacing=str(nozzle_mm * 7.5),
        support_interface_pattern="rectilinear", support_interface_loop_pattern="0",
        support_filament="0", support_interface_filament="0", raft_layers="0",
        detect_floating_vertical_shell="1", detect_overhang_wall="1", bridge_no_support="0",
        sparse_infill_density="100%", sparse_infill_pattern="zig-zag")
    return result, {"method": "detachable_slicer_support", "permanent_buttresses_allowed": False,
        "layer_height_mm": height, "top_and_bottom_gap_mm": gap,
        "interface_layers": 2, "interface_spacing_mm": nozzle_mm,
        "base_pattern_spacing_mm": nozzle_mm * 7.5, "physical_removal_test_required": True}


def _number(settings, key):
    value = settings[key]
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError("expected_single_value: " + key)
        value = value[0]
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("invalid_setting: " + key)
    return result


def _beads(segments):
    from shapely.geometry import MultiLineString
    from shapely.ops import unary_union
    groups = defaultdict(list)
    for s in segments:
        groups[(round(s.z, 4), round(s.layer_height_mm, 4), round(s.line_width_mm, 3))].append(
            [(s.x1, s.y1), (s.x2, s.y2)])
    layers = defaultdict(list)
    for (z, height, width), lines in groups.items():
        layers[(z, height)].append(MultiLineString(lines).buffer(width / 2, quad_segs=4))
    return [(z, h, unary_union(polygons)) for (z, h), polygons in sorted(layers.items())]


def _screen_enclosed_support(support_beads, artifact, geometry_path):
    """Reject support in cross-section holes. This is deliberately conservative.

    It does not simulate bending/breaking, tool access or removal through a side
    opening at another height; those require physical/user review.
    """
    import numpy as np
    import trimesh
    import shapely
    from shapely.geometry import MultiLineString, Polygon
    from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame, verify_source_matches_slice
    mesh = trimesh.load(geometry_path, force="mesh", process=True)
    verify_source_matches_slice(mesh, artifact)
    mapping = gate_report_in_geometry_frame({}, artifact)["coordinate_mapping"]
    offset = np.asarray(mapping["translation_xy_mm"], dtype=float)
    mesh.apply_translation([-offset[0], -offset[1], 0])
    enclosed = []
    for z, height, footprint in support_beads:
        section = mesh.section(plane_origin=(0, 0, z - height / 2), plane_normal=(0, 0, 1))
        if section is None:
            continue
        paths = [p[:, :2] for p in section.discrete if len(p) > 2]
        area = shapely.build_area(MultiLineString(paths)) if paths else None
        polygons = list(area.geoms) if area is not None and hasattr(area, "geoms") else [area]
        for polygon in polygons:
            if polygon is None or polygon.geom_type != "Polygon":
                continue
            for ring in polygon.interiors:
                intersection = Polygon(ring).intersection(footprint)
                if intersection.area > 1e-5:
                    enclosed.append({"z_mm": z, "area_mm2": float(intersection.area)})
    return enclosed


def inspect_detachable_support(artifact: Path, *, geometry_path: Path | None = None) -> dict:
    """Fail closed on fused contacts, missing bead metadata or enclosed support."""
    report = {"status": "blocked", "audit": "detachable_support_design_v1", "blockers": [],
        "physical_removal_test_required": True, "physical_removal_verified": False,
        "limits": "Nominal bead geometry and enclosed-section screening; no peel-force or full tool-access simulation."}
    try:
        with zipfile.ZipFile(artifact) as archive:
            names = [n for n in archive.namelist() if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)]
            if len(names) != 1:
                raise ValueError("expected_one_plate")
            settings = json.loads(archive.read("Metadata/project_settings.config").decode("utf-8-sig"))
            segments = parse_extrusion_segments(archive.read(names[0]).decode("utf-8"))
        model = [s for s in segments if _is_model(s.feature)]
        support = [s for s in segments if _is_support(s.feature)]
        if not model:
            raise ValueError("no_model_extrusion")
        report["support_segment_count"] = len(support)
        if not support:
            report.update(status="pass", support_needed=False, physical_removal_test_required=False)
            return report
        report["support_needed"] = True
        nozzle = _number(settings, "nozzle_diameter")
        top = _number(settings, "support_top_z_distance")
        bottom = _number(settings, "support_bottom_z_distance")
        xy = _number(settings, "support_object_xy_distance")
        spacing = _number(settings, "support_interface_spacing")
        top_layers = _number(settings, "support_interface_top_layers")
        base_spacing = _number(settings, "support_base_pattern_spacing")
        report["settings"] = dict(nozzle_mm=nozzle, top_gap_mm=top, bottom_gap_mm=bottom,
            xy_gap_mm=xy, interface_spacing_mm=spacing, top_interface_layers=top_layers,
            base_pattern_spacing_mm=base_spacing)
        if not (.25 * nozzle <= top <= .8 * nozzle and .25 * nozzle <= bottom <= .8 * nozzle):
            report["blockers"].append("contact_gap_outside_breakaway_design_range")
        if xy < .5 * nozzle or spacing < .5 * nozzle or not 1 <= top_layers <= 3 or base_spacing < 3 * nozzle:
            report["blockers"].append("support_too_dense_for_breakaway_default")
        if any(s.line_width_mm is None or s.layer_height_mm is None or
               not 0 < s.layer_height_mm <= nozzle or not 0 < s.line_width_mm <= nozzle * 3 for s in model + support):
            raise ValueError("missing_or_invalid_actual_bead_dimensions")
        model_beads, support_beads = _beads(model), _beads(support)
        contacts, clashes = [], []
        # The epsilon only handles floating-point geometry. No area exemptions
        # from the independent unsupported-extrusion gate are introduced here.
        for sz, sh, sp in support_beads:
            for mz, mh, mp in model_beads:
                gap = max(mz - mh - sz, sz - sh - mz)
                if gap > max(top, bottom) + nozzle:
                    continue
                if mz + 1e-5 < sz - sh or sz + 1e-5 < mz - mh:
                    direction = "top" if mz > sz else "bottom"
                else:
                    direction = "overlap"
                area = sp.intersection(mp).area
                if area <= 1e-5:
                    continue
                contact = {"support_z_mm": sz, "model_z_mm": mz,
                    "clearance_mm": round(gap, 5), "overlap_xy_area_mm2": float(area), "side": direction}
                contacts.append(contact)
                if gap < nozzle * .2 - 1e-4:
                    clashes.append(contact)
        report["minimum_measured_contact_gap_mm"] = min((c["clearance_mm"] for c in contacts), default=None)
        report["contact_pair_count"] = len(contacts)
        report["contact_clashes"] = clashes[:30]
        report["contact_clash_count"] = len(clashes)
        if clashes:
            report["blockers"].append("actual_support_contacts_are_fused_or_too_close")
        if not contacts:
            report["blockers"].append("support_has_no_measurable_model_contacts")
        if geometry_path is None:
            raise ValueError("geometry_path_required_for_removal_screening")
        enclosed = _screen_enclosed_support(support_beads, artifact, geometry_path)
        report["enclosed_support_sections"] = enclosed[:30]
        report["enclosed_support_section_count"] = len(enclosed)
        if enclosed:
            report["blockers"].append("support_inside_model_hole_requires_redesign_or_manual_review")
        report["status"] = "blocked" if report["blockers"] else "pass"
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
        report["blockers"].append(str(exc))
    return report
