from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import trimesh


SCHEMA_VERSION = "1.0"

LAYER_HEIGHT_MM = 0.20
LINE_WIDTH_MM = 0.42

GATE_SELF_SUPPORT_XY_MM = max(
    LINE_WIDTH_MM * 0.65,
    LAYER_HEIGHT_MM * 1.10,
)

MOP_ANGLE_DEG = 50.0

MOP_XY_MM_PER_LAYER = (
    math.tan(
        math.radians(MOP_ANGLE_DEG)
    )
    * LAYER_HEIGHT_MM
)

BED_CENTER_X_MM = 128.0
BED_CENTER_Y_MM = 128.0


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def load_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(
        path,
        force="scene",
        process=True,
    )

    if isinstance(
        loaded,
        trimesh.Scene,
    ):
        mesh = loaded.to_mesh()

    if isinstance(
        loaded,
        trimesh.Trimesh,
    ):
        mesh = loaded

    if not isinstance(
        mesh,
        trimesh.Trimesh,
    ):
        raise RuntimeError(
            f"INVALID_MESH:{path}"
        )

    mesh = mesh.copy()

    if hasattr(
        mesh,
        "merge_vertices",
    ):
        mesh.merge_vertices()

    if hasattr(
        mesh,
        "remove_unreferenced_vertices",
    ):
        mesh.remove_unreferenced_vertices()

    return mesh


def component_count(
    mesh: trimesh.Trimesh,
) -> int:
    return len(
        mesh.split(
            only_watertight=False
        )
    )


def bed_center(
    mesh: trimesh.Trimesh,
) -> trimesh.Trimesh:
    out = mesh.copy()

    bounds = np.asarray(
        out.bounds,
        dtype=float,
    )

    cx = float(
        bounds[:, 0].mean()
    )

    cy = float(
        bounds[:, 1].mean()
    )

    min_z = float(
        bounds[0, 2]
    )

    out.apply_translation(
        (
            BED_CENTER_X_MM - cx,
            BED_CENTER_Y_MM - cy,
            -min_z,
        )
    )

    return out


def restore_height(
    mesh: trimesh.Trimesh,
    target_height_mm: float,
) -> tuple[trimesh.Trimesh, float]:

    out = mesh.copy()

    height = float(
        out.extents[2]
    )

    if height <= 0:
        raise RuntimeError(
            "INVALID_HEIGHT"
        )

    factor = (
        float(target_height_mm)
        / height
    )

    if abs(
        factor - 1.0
    ) > 1e-12:
        out.apply_scale(
            factor
        )

    out.apply_translation(
        (
            0.0,
            0.0,
            -float(
                out.bounds[0, 2]
            ),
        )
    )

    return out, factor


def locate_first(
    candidates: list[Path],
) -> Path | None:

    for path in candidates:
        if path.is_file():
            return path.resolve()

    return None


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--preflight",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.preflight:
        raise SystemExit(
            "ONLY --preflight IS ENABLED IN V1"
        )

    root = Path(
        os.environ["NL_ROOT"]
    ).resolve()

    for name in (
        "AGENTS.md",
        "agent.md",
    ):
        p = root / name

        if p.is_file():
            p.read_text(
                encoding="utf-8-sig",
                errors="replace",
            )

    manifest_path = (
        root
        / "tests"
        / "fixtures"
        / "printability_strategy_benchmark_v1"
        / "benchmark_manifest.json"
    )

    if not manifest_path.is_file():
        raise SystemExit(
            "BENCHMARK_MANIFEST_MISSING"
        )

    manifest = json.loads(
        manifest_path.read_text(
            encoding="utf-8-sig"
        )
    )

    if (
        int(
            manifest.get(
                "sample_count",
                -1,
            )
        )
        != 9
    ):
        raise SystemExit(
            "BENCHMARK_SAMPLE_COUNT_NOT_9"
        )

    # --------------------------------------------------------
    # Toolchain
    # --------------------------------------------------------

    bambu = locate_first(
        [
            Path(
                r"C:\Program Files\Bambu Studio\bambu-studio.exe"
            ),

            Path(
                r"C:\Program Files\BambuStudio\bambu-studio.exe"
            ),
        ]
    )

    orca = locate_first(
        [
            Path(
                r"C:\Program Files\OrcaSlicer\orca-slicer.exe"
            ),
        ]
    )

    tool_blockers = []

    if bambu is None:
        tool_blockers.append(
            "bambu_studio_missing"
        )

    if orca is None:
        tool_blockers.append(
            "orca_slicer_missing"
        )

    bambu_machine = None
    bambu_process = None
    bambu_filament = None

    if bambu is not None:
        resources = (
            bambu.parent
            / "resources"
        )

        bbl = (
            resources
            / "profiles"
            / "BBL"
        )

        bambu_machine = (
            bbl
            / "machine"
            / "Bambu Lab X1 Carbon 0.4 nozzle.json"
        )

        bambu_process = (
            bbl
            / "process"
            / "0.20mm Standard @BBL X1C.json"
        )

        bambu_filament = (
            bbl
            / "filament"
            / "Bambu PLA Basic @BBL X1C.json"
        )

        for label, path in (
            (
                "bambu_machine_profile",
                bambu_machine,
            ),
            (
                "bambu_process_profile",
                bambu_process,
            ),
            (
                "bambu_filament_profile",
                bambu_filament,
            ),
        ):
            if not path.is_file():
                tool_blockers.append(
                    label + "_missing"
                )

    orca_machine = None
    orca_process = None
    orca_filament = None

    if orca is not None:
        resources = (
            orca.parent
            / "resources"
        )

        bbl = (
            resources
            / "profiles"
            / "BBL"
        )

        orca_machine = (
            bbl
            / "machine"
            / "Bambu Lab X1 Carbon 0.4 nozzle.json"
        )

        orca_process = (
            bbl
            / "process"
            / "0.20mm Standard @BBL X1C.json"
        )

        orca_filament = (
            bbl
            / "filament"
            / "Bambu PLA Basic @BBL X1C.json"
        )

        for label, path in (
            (
                "orca_machine_profile",
                orca_machine,
            ),
            (
                "orca_process_profile",
                orca_process,
            ),
            (
                "orca_filament_profile",
                orca_filament,
            ),
        ):
            if not path.is_file():
                tool_blockers.append(
                    label + "_missing"
                )

    # --------------------------------------------------------
    # Project gates
    # --------------------------------------------------------

    gate_path = (
        root
        / "src"
        / "am_print_executor"
        / "gcode_printability_gate.py"
    )

    flat_gate_path = (
        root
        / "src"
        / "am_print_executor"
        / "flat_base_gate.py"
    )

    if not gate_path.is_file():
        tool_blockers.append(
            "gcode_printability_gate_missing"
        )

    if not flat_gate_path.is_file():
        tool_blockers.append(
            "flat_base_gate_missing"
        )

    try:
        import manifold3d

        manifold_ready = True

    except Exception:
        manifold_ready = False

        tool_blockers.append(
            "manifold3d_missing"
        )

    from am_print_executor.flat_base_gate import (
        FlatBaseGateError,
        ensure_flat_printing_base,
        inspect_flat_printing_base,
    )

    from am_print_executor.gcode_printability_gate import (
        inspect_final_gcode_printability,
    )

    # Import proves Gate can be loaded.
    assert callable(
        inspect_final_gcode_printability
    )

    # --------------------------------------------------------
    # Frozen strategy definitions
    # --------------------------------------------------------

    strategies = {
        "bambu_normal_auto_support": {
            "slicer":
                "bambu",

            "geometry_change":
                False,

            "enable_support":
                "1",

            "support_type":
                "normal(auto)",

            "support_on_build_plate_only":
                "0",

            "support_threshold_angle":
                "30",
        },

        "bambu_tree_auto_hybrid": {
            "slicer":
                "bambu",

            "geometry_change":
                False,

            "enable_support":
                "1",

            "support_type":
                "tree(auto)",

            "support_style":
                "tree_hybrid",

            "support_on_build_plate_only":
                "0",

            "support_threshold_angle":
                "30",
        },

        "orca_mop_without_support": {
            "slicer":
                "orca",

            "geometry_change":
                True,

            "make_overhang_printable":
                "1",

            "make_overhang_printable_angle":
                MOP_ANGLE_DEG,

            # Orca default retained for V1.
            # Geometry-preservation benchmark is responsible
            # for detecting unacceptable cavity filling.
            "make_overhang_printable_hole_size":
                0.0,

            "enable_support":
                "0",
        },

        "orca_mop_plus_support_fallback": {
            "slicer":
                "orca",

            "conditional":
                True,

            "geometry_change":
                True,

            "make_overhang_printable":
                "1",

            "make_overhang_printable_angle":
                MOP_ANGLE_DEG,

            "make_overhang_printable_hole_size":
                0.0,

            "enable_support":
                "1",
        },
    }

    # --------------------------------------------------------
    # Output run
    # --------------------------------------------------------

    run_dir = (
        root
        / "outputs"
        / "printability_strategy_benchmark_v1"
        / (
            "preflight_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
        )
    )

    canonical_dir = (
        run_dir
        / "canonical_geometry"
    )

    canonical_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    sample_reports = []

    unexpected_blocks = []

    eligible_count = 0

    # --------------------------------------------------------
    # Canonicalize every sample ONCE.
    #
    # No orientation optimization here.
    # The fixture orientation is frozen so slicer strategy,
    # not orientation search, is the independent variable.
    # --------------------------------------------------------

    for sample in manifest["samples"]:
        sample_id = str(
            sample["id"]
        )

        source = (
            root
            / sample["path"]
        ).resolve()

        responsibility = (
            manifest.get(
                "responsibility",
                {},
            ).get(
                sample_id,
                {},
            )
        )

        if not source.is_file():
            unexpected_blocks.append(
                sample_id
                + ":source_missing"
            )

            sample_reports.append(
                {
                    "id":
                        sample_id,

                    "status":
                        "unexpected_block",

                    "blockers":
                        ["source_missing"],
                }
            )

            continue

        mesh = load_mesh(
            source
        )

        source_components = (
            component_count(
                mesh
            )
        )

        source_height = float(
            mesh.extents[2]
        )

        source_volume = float(
            mesh.volume
        )

        before_flat = (
            inspect_flat_printing_base(
                mesh
            )
        )

        flat_action = None

        prepared = mesh.copy()

        flat_error = None

        try:
            prepared, flat_action = (
                ensure_flat_printing_base(
                    prepared,
                    layer_height_mm=(
                        LAYER_HEIGHT_MM
                    ),
                )
            )

        except FlatBaseGateError as exc:
            flat_error = exc.report

        scale_factor = 1.0

        if flat_error is None:
            prepared, scale_factor = (
                restore_height(
                    prepared,
                    source_height,
                )
            )

            prepared = bed_center(
                prepared
            )

        after_flat = None

        prepared_components = None

        prepared_watertight = None

        prepared_volume = None

        if flat_error is None:
            after_flat = (
                inspect_flat_printing_base(
                    prepared
                )
            )

            prepared_components = (
                component_count(
                    prepared
                )
            )

            prepared_watertight = bool(
                prepared.is_watertight
            )

            prepared_volume = float(
                prepared.volume
            )

        blockers = []

        if flat_error is not None:
            blockers.append(
                "flat_base_unrepairable"
            )

        if (
            prepared_components is not None
            and prepared_components != 1
        ):
            blockers.append(
                "disconnected_components"
            )

        if (
            prepared_watertight is not None
            and not prepared_watertight
        ):
            blockers.append(
                "not_watertight"
            )

        if (
            after_flat is not None
            and after_flat.get(
                "status"
            ) != "pass"
        ):
            blockers.append(
                "flat_base_not_pass"
            )

        expected_route = str(
            responsibility.get(
                "expected_route",
                "",
            )
        )

        intentional_block = (
            sample_id
            == "true_floating_island"
            and
            "disconnected_components"
            in blockers
        )

        status = (
            "eligible"
            if not blockers
            else "pre_strategy_block"
        )

        if intentional_block:
            status = (
                "expected_pre_strategy_block"
            )

        if (
            blockers
            and not intentional_block
        ):
            unexpected_blocks.append(
                sample_id
                + ":"
                + ",".join(
                    blockers
                )
            )

        canonical_path = None

        if flat_error is None:
            canonical_path = (
                canonical_dir
                / (
                    sample_id
                    + ".stl"
                )
            )

            canonical_path.write_bytes(
                trimesh.exchange.stl.export_stl(
                    prepared
                )
            )

        if status == "eligible":
            eligible_count += 1

        volume_ratio = None

        if (
            prepared_volume is not None
            and
            abs(source_volume) > 1e-9
        ):
            volume_ratio = (
                prepared_volume
                / source_volume
            )

        report = {
            "id":
                sample_id,

            "kind":
                sample.get("kind"),

            "source":
                str(source),

            "source_sha256":
                sha256(source),

            "expected_route":
                expected_route,

            "identity_change_allowed":
                responsibility.get(
                    "identity_change_allowed"
                ),

            "source_geometry": {
                "height_mm":
                    source_height,

                "watertight":
                    bool(
                        mesh.is_watertight
                    ),

                "component_count":
                    source_components,

                "volume_mm3":
                    source_volume,

                "flat_base_status":
                    before_flat.get(
                        "status"
                    ),
            },

            "canonicalization": {
                "flat_base_action":
                    (
                        flat_action.get(
                            "status"
                        )
                        if flat_action
                        else None
                    ),

                "flat_base_clip_mm":
                    (
                        flat_action.get(
                            "clip_depth_mm"
                        )
                        if flat_action
                        else None
                    ),

                "height_restore_scale":
                    scale_factor,

                "orientation_changed":
                    False,

                "bed_centered":
                    (
                        flat_error
                        is None
                    ),
            },

            "canonical_geometry": {
                "path":
                    (
                        str(
                            canonical_path
                        )
                        if canonical_path
                        else None
                    ),

                "sha256":
                    (
                        sha256(
                            canonical_path
                        )
                        if canonical_path
                        else None
                    ),

                "height_mm":
                    (
                        float(
                            prepared.extents[2]
                        )
                        if flat_error
                        is None
                        else None
                    ),

                "watertight":
                    prepared_watertight,

                "component_count":
                    prepared_components,

                "volume_mm3":
                    prepared_volume,

                "volume_ratio_to_source":
                    volume_ratio,

                "flat_base_status":
                    (
                        after_flat.get(
                            "status"
                        )
                        if after_flat
                        else None
                    ),
            },

            "status":
                status,

            "blockers":
                blockers,
        }

        sample_reports.append(
            report
        )

    # --------------------------------------------------------
    # Run-count planning
    #
    # Primary:
    #   Bambu Normal
    #   Bambu Tree
    #   Orca MOP
    #
    # Fallback:
    #   MOP + support only when MOP-alone blocks.
    # --------------------------------------------------------

    primary_slices = (
        eligible_count * 3
    )

    maximum_fallback_slices = (
        eligible_count
    )

    maximum_total_slices = (
        primary_slices
        + maximum_fallback_slices
    )

    preflight_status = (
        "pass"
        if (
            not tool_blockers
            and
            not unexpected_blocks
        )
        else "block"
    )

    report = {
        "schema_version":
            SCHEMA_VERSION,

        "status":
            preflight_status,

        "manifest":
            str(manifest_path),

        "manifest_sha256":
            sha256(
                manifest_path
            ),

        "toolchain": {
            "bambu_studio":
                (
                    str(bambu)
                    if bambu
                    else None
                ),

            "orca_slicer":
                (
                    str(orca)
                    if orca
                    else None
                ),

            "manifold3d":
                manifold_ready,

            "gate_sha256":
                (
                    sha256(
                        gate_path
                    )
                    if gate_path.is_file()
                    else None
                ),

            "tool_blockers":
                tool_blockers,
        },

        "policy_alignment": {
            "layer_height_mm":
                LAYER_HEIGHT_MM,

            "line_width_mm":
                LINE_WIDTH_MM,

            "gate_self_support_xy_mm_per_layer":
                GATE_SELF_SUPPORT_XY_MM,

            "mop_angle_deg":
                MOP_ANGLE_DEG,

            "mop_xy_mm_per_layer":
                MOP_XY_MM_PER_LAYER,

            "mop_within_gate":
                bool(
                    MOP_XY_MM_PER_LAYER
                    <=
                    GATE_SELF_SUPPORT_XY_MM
                    + 1e-12
                ),

            "mop_hole_size_mm2":
                0.0,

            "mop_hole_policy":
                (
                    "orca_default; "
                    "must independently pass "
                    "geometry-preservation test"
                ),
        },

        "strategies":
            strategies,

        "samples":
            sample_reports,

        "eligible_sample_count":
            eligible_count,

        "expected_pre_strategy_blocks":
            [
                "true_floating_island"
            ],

        "unexpected_blocks":
            unexpected_blocks,

        "slice_plan": {
            "primary_slices":
                primary_slices,

            "maximum_fallback_slices":
                maximum_fallback_slices,

            "maximum_total_slices":
                maximum_total_slices,

            "auto_orient_runs":
                0,
        },
    }

    report_path = (
        run_dir
        / "preflight_report.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # Concise output
    # --------------------------------------------------------

    print(
        "=== STRATEGY BENCHMARK PREFLIGHT V1 ==="
    )

    print(
        "MANIFEST_SHA256 =",
        report[
            "manifest_sha256"
        ],
    )

    print(
        "BAMBU_READY =",
        bambu is not None,
    )

    print(
        "ORCA_READY =",
        orca is not None,
    )

    print(
        "MANIFOLD_READY =",
        manifold_ready,
    )

    print("")
    print(
        "=== POLICY ALIGNMENT ==="
    )

    print(
        "GATE_XY_MM_PER_LAYER =",
        GATE_SELF_SUPPORT_XY_MM,
    )

    print(
        "MOP_ANGLE_DEG =",
        MOP_ANGLE_DEG,
    )

    print(
        "MOP_XY_MM_PER_LAYER =",
        MOP_XY_MM_PER_LAYER,
    )

    print(
        "MOP_WITHIN_GATE =",
        report[
            "policy_alignment"
        ][
            "mop_within_gate"
        ],
    )

    print(
        "MOP_HOLE_SIZE_MM2 = 0.0"
    )

    print("")
    print(
        "=== SAMPLE CANONICALIZATION ==="
    )

    for item in sample_reports:
        src = item.get(
            "source_geometry",
            {},
        )

        canon = item.get(
            "canonical_geometry",
            {},
        )

        action = item.get(
            "canonicalization",
            {},
        )

        print(
            item["id"],
            "| status =",
            item["status"],
            "| components =",
            src.get(
                "component_count"
            ),
            "->",
            canon.get(
                "component_count"
            ),
            "| flat =",
            src.get(
                "flat_base_status"
            ),
            "->",
            canon.get(
                "flat_base_status"
            ),
            "| base_action =",
            action.get(
                "flat_base_action"
            ),
        )

    print("")
    print(
        "=== RUN PLAN ==="
    )

    print(
        "ELIGIBLE_SAMPLE_COUNT =",
        eligible_count,
    )

    print(
        "EXPECTED_PRE_STRATEGY_BLOCKS = 1"
    )

    print(
        "PRIMARY_SLICES =",
        primary_slices,
    )

    print(
        "MAXIMUM_FALLBACK_SLICES =",
        maximum_fallback_slices,
    )

    print(
        "MAXIMUM_TOTAL_SLICES =",
        maximum_total_slices,
    )

    print(
        "AUTO_ORIENT_RUNS = 0"
    )

    print(
        "UNEXPECTED_BLOCKS =",
        unexpected_blocks,
    )

    print("")
    print(
        "PREFLIGHT =",
        preflight_status.upper(),
    )

    print(
        "REPORT =",
        report_path,
    )

    print(
        "SLICING_RUNS = 0"
    )

    print(
        "PIPELINE_SOURCE_FILES_MODIFIED = 0"
    )

    print(
        "NEXT_GATE =",
        (
            "RUN_STRATEGY_BENCHMARK"
            if preflight_status
            == "pass"
            else
            "STOP_AT_FIRST_PREFLIGHT_FAILURE"
        ),
    )

    return (
        0
        if preflight_status
        == "pass"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
