"""Enable Bambu Studio's native automatic tree support."""
from __future__ import annotations

SUPPORT_MODES = ("detachable",)


def validate_support_mode(mode) -> None:
    if mode not in SUPPORT_MODES:
        raise ValueError("unknown_support_mode: " + str(mode))


def detachable_process(
    process: dict,
    nozzle_mm: float | None = None,
) -> tuple[dict, dict]:
    """Enable automatic tree support without overriding Bambu's decisions."""
    del nozzle_mm  # retained only for compatibility with older callers
    result = dict(process)
    result.update(
        enable_support="1",
        support_type="tree(auto)",
        support_style="tree_hybrid",
    )
    return result, {
        "method": "bambu_studio_native_tree_support",
        "support_generator": "bambu_studio_native",
        "support_type": "tree(auto)",
        "support_style": "tree_hybrid",
        "headless": True,
        "model_geometry_changed": False,
        "permanent_buttresses_allowed": False,
        "bambu_profile_settings_preserved": True,
    }
