"""Configure Bambu Studio's native automatic tree support."""
from __future__ import annotations

import math


SUPPORT_MODES = ("detachable",)


def validate_support_mode(mode) -> None:
    if mode not in SUPPORT_MODES:
        raise ValueError("unknown_support_mode: " + str(mode))


def detachable_process(process: dict, nozzle_mm: float) -> tuple[dict, dict]:
    """Force Bambu's automatic tree-hybrid support in a material-safe profile."""
    if not math.isfinite(nozzle_mm) or nozzle_mm <= 0:
        raise ValueError("invalid_nozzle_diameter")
    result = dict(process)
    height = round(min(float(process.get("layer_height", 0.2)), nozzle_mm * 0.3), 3)
    gap = round(height * 2, 3)
    result.update(
        enable_support="1",
        support_type="tree(auto)",
        support_style="tree_hybrid",
        support_threshold_angle="30",
        layer_height=str(height),
        elefant_foot_compensation="0",
        initial_layer_line_width=str(
            process.get("outer_wall_line_width", nozzle_mm * 1.05)
        ),
        support_on_build_plate_only="0",
        support_critical_regions_only="0",
        support_remove_small_overhang="0",
        support_interface_top_layers="2",
        support_interface_bottom_layers="2",
        support_interface_spacing=str(nozzle_mm),
        support_bottom_interface_spacing=str(nozzle_mm),
        support_top_z_distance=str(gap),
        support_bottom_z_distance=str(gap),
        support_object_xy_distance=str(round(nozzle_mm * 0.875, 3)),
        support_base_pattern="rectilinear",
        support_base_pattern_spacing=str(nozzle_mm * 7.5),
        support_interface_pattern="rectilinear",
        support_interface_loop_pattern="0",
        support_filament="0",
        support_interface_filament="0",
        raft_layers="0",
        detect_floating_vertical_shell="1",
        detect_overhang_wall="1",
        bridge_no_support="0",
        sparse_infill_density="100%",
        sparse_infill_pattern="zig-zag",
    )
    return result, {
        "method": "bambu_studio_native_tree_support",
        "support_generator": "bambu_studio_native",
        "support_type": "tree(auto)",
        "support_style": "tree_hybrid",
        "headless": True,
        "model_geometry_changed": False,
        "permanent_buttresses_allowed": False,
        "layer_height_mm": height,
        "top_and_bottom_gap_mm": gap,
        "physical_removal_test_required": True,
    }
