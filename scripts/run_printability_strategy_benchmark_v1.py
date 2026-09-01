from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import time
import zipfile

from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(
    os.environ["NL_ROOT"]
).resolve()

for name in ("AGENTS.md", "agent.md"):
    path = ROOT / name
    if path.is_file():
        path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )

# ------------------------------------------------------------
# Frozen benchmark inputs
# ------------------------------------------------------------

BENCH_ROOT = (
    ROOT
    / "outputs"
    / "printability_strategy_benchmark_v1"
)

preflights = sorted(
    BENCH_ROOT.glob(
        "preflight_*/preflight_report.json"
    ),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)

if not preflights:
    raise SystemExit(
        "RESULT = PREFLIGHT_REPORT_NOT_FOUND"
    )

PREFLIGHT_PATH = preflights[0]

preflight = json.loads(
    PREFLIGHT_PATH.read_text(
        encoding="utf-8-sig"
    )
)

if preflight.get("status") != "pass":
    raise SystemExit(
        "RESULT = LATEST_PREFLIGHT_NOT_PASS"
    )

eligible = [
    item
    for item in preflight["samples"]
    if item.get("status") == "eligible"
]

if len(eligible) != 8:
    raise SystemExit(
        f"RESULT = ELIGIBLE_SAMPLE_COUNT_NOT_8:{len(eligible)}"
    )

toolchain = preflight["toolchain"]

BAMBU = Path(
    toolchain["bambu_studio"]
).resolve()

ORCA = Path(
    toolchain["orca_slicer"]
).resolve()

if not BAMBU.is_file():
    raise SystemExit(
        "RESULT = BAMBU_STUDIO_MISSING"
    )

if not ORCA.is_file():
    raise SystemExit(
        "RESULT = ORCA_SLICER_MISSING"
    )

# ------------------------------------------------------------
# Profiles
# ------------------------------------------------------------

def profile_paths(
    exe: Path,
):
    bbl = (
        exe.parent
        / "resources"
        / "profiles"
        / "BBL"
    )

    return {
        "machine":
            (
                bbl
                / "machine"
                / "Bambu Lab X1 Carbon 0.4 nozzle.json"
            ),

        "process":
            (
                bbl
                / "process"
                / "0.20mm Standard @BBL X1C.json"
            ),

        "filament":
            (
                bbl
                / "filament"
                / "Bambu PLA Basic @BBL X1C.json"
            ),
    }


BBL = profile_paths(
    BAMBU
)

ORC = profile_paths(
    ORCA
)

for family, profiles in (
    ("BAMBU", BBL),
    ("ORCA", ORC),
):
    for name, path in profiles.items():
        if not path.is_file():
            raise SystemExit(
                f"RESULT = {family}_{name.upper()}_PROFILE_MISSING:{path}"
            )

# ------------------------------------------------------------
# Existing project helpers
# ------------------------------------------------------------

from am_print_executor.bambu_headless_cli import (
    run_bambu_cli,
)

from am_print_executor.multimaterial_project import (
    repair_bambu_model_settings_xml,
)

import am_print_executor.gcode_printability_gate as gate_module

from am_print_executor.gcode_support_continuity import (
    parse_extrusion_segments,
)

# ------------------------------------------------------------
# Orca semantic adapter
#
# Orca's own ExtrusionEntity::is_bridge() includes
# erOverhangPerimeter, exposed in G-code as "Overhang wall".
#
# Benchmark-only runtime translation.
# Formal project Gate remains untouched.
# ------------------------------------------------------------

ORIGINAL_IS_BRIDGE = (
    gate_module._is_bridge
)

def orca_aware_is_bridge(
    feature: str,
) -> bool:

    normalized = " ".join(
        str(feature)
        .strip()
        .lower()
        .split()
    )

    if normalized == "overhang wall":
        return True

    return ORIGINAL_IS_BRIDGE(
        feature
    )


def set_gate_semantics(
    slicer: str,
):

    if slicer == "orca":
        gate_module._is_bridge = (
            orca_aware_is_bridge
        )

    if slicer == "bambu":
        gate_module._is_bridge = (
            ORIGINAL_IS_BRIDGE
        )


# ------------------------------------------------------------
# Process profiles
# ------------------------------------------------------------

def load_json(
    path: Path,
) -> dict:

    return json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )


def write_json(
    path: Path,
    data: dict,
):

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def make_bambu_process(
    destination: Path,
    *,
    tree: bool,
):

    obj = load_json(
        BBL["process"]
    )

    obj.update(
        {
            "enable_support":
                "1",

            "support_on_build_plate_only":
                "0",

            "support_critical_regions_only":
                "0",

            "support_remove_small_overhang":
                "0",

            "support_threshold_angle":
                "30",

            "detect_floating_vertical_shell":
                "1",

            "detect_overhang_wall":
                "1",

            "bridge_no_support":
                "0",
        }
    )

    if tree:
        obj["name"] = (
            "Benchmark Tree Hybrid"
        )

        obj["support_type"] = (
            "tree(auto)"
        )

        obj["support_style"] = (
            "tree_hybrid"
        )

    if not tree:
        obj["name"] = (
            "Benchmark Normal Auto"
        )

        obj["support_type"] = (
            "normal(auto)"
        )

    write_json(
        destination,
        obj,
    )

    return obj


def make_orca_process(
    destination: Path,
    *,
    support: bool,
):

    obj = load_json(
        ORC["process"]
    )

    obj.update(
        {
            "make_overhang_printable":
                "1",

            "make_overhang_printable_angle":
                "50",

            "make_overhang_printable_hole_size":
                "0",

            "enable_support":
                (
                    "1"
                    if support
                    else "0"
                ),

            "support_on_build_plate_only":
                "0",

            "support_critical_regions_only":
                "0",

            "support_remove_small_overhang":
                "0",

            "support_threshold_angle":
                "30",

            "detect_floating_vertical_shell":
                "1",

            "detect_overhang_wall":
                "1",

            "bridge_no_support":
                "0",
        }
    )

    if support:
        obj["name"] = (
            "Benchmark Orca MOP50 Plus Support"
        )

        obj["support_type"] = (
            "normal(auto)"
        )

    if not support:
        obj["name"] = (
            "Benchmark Orca MOP50 No Support"
        )

    write_json(
        destination,
        obj,
    )

    return obj


# ------------------------------------------------------------
# Generic helpers
# ------------------------------------------------------------

RUN_ROOT = (
    BENCH_ROOT
    / (
        "run_"
        + datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
    )
)

RUN_ROOT.mkdir(
    parents=True,
    exist_ok=False,
)


def clean_dir(
    path: Path,
):

    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    for child in path.iterdir():
        if child.is_file():
            child.unlink()


def read_zip_gcode(
    artifact: Path,
) -> str:

    with zipfile.ZipFile(
        artifact,
        "r",
    ) as archive:

        names = [
            name
            for name in archive.namelist()
            if (
                name.startswith(
                    "Metadata/plate_"
                )
                and
                name.endswith(
                    ".gcode"
                )
            )
        ]

        if len(names) != 1:
            raise RuntimeError(
                f"EXPECTED_ONE_GCODE:{artifact}:{names}"
            )

        return archive.read(
            names[0]
        ).decode(
            "utf-8",
            errors="replace",
        )


def wrap_orca_gcode(
    gcode: Path,
    wrapper: Path,
    process: dict,
):

    raw = gcode.read_bytes()

    with zipfile.ZipFile(
        wrapper,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:

        archive.writestr(
            "Metadata/plate_1.gcode",
            raw,
        )

        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                process,
                ensure_ascii=False,
            ).encode(
                "utf-8"
            ),
        )

    with zipfile.ZipFile(
        wrapper,
        "r",
    ) as archive:

        verify = archive.read(
            "Metadata/plate_1.gcode"
        )

    if verify != raw:
        raise RuntimeError(
            "ORCA_WRAPPER_CHANGED_GCODE_BYTES"
        )


def blocker_kinds(
    result: dict,
) -> dict:

    return dict(
        Counter(
            str(item).split(
                ":",
                1,
            )[0]
            for item
            in (
                result.get(
                    "blockers"
                )
                or []
            )
        )
    )


def gate_metrics(
    result: dict,
) -> dict:

    return {
        "status":
            result.get(
                "status"
            ),

        "dangerous_layer_count":
            result.get(
                "dangerous_layer_count"
            ),

        "total_bad_area_mm2":
            result.get(
                "total_bad_area_mm2"
            ),

        "worst_bad_area_mm2":
            result.get(
                "worst_bad_area_mm2"
            ),

        "longest_bad_bridge_mm":
            result.get(
                "longest_bad_bridge_mm"
            ),

        "support_xy_length_mm":
            result.get(
                "support_xy_length_mm"
            ),

        "blocker_count":
            len(
                result.get(
                    "blockers"
                )
                or []
            ),

        "blocker_kinds":
            blocker_kinds(
                result
            ),
    }


# ------------------------------------------------------------
# Critical-feature preservation
#
# Known benchmark:
# base_recessed_hole has a base-open cylindrical recess,
# radius=4 mm, extending from Z=0 to Z=5 mm.
#
# A model extrusion crossing radius<=2.0 mm below Z=4.5
# means the cavity has been materially filled.
#
# Support extrusion does NOT count as geometry destruction.
# ------------------------------------------------------------

def point_segment_distance(
    px: float,
    py: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> float:

    dx = x2 - x1
    dy = y2 - y1

    denom = (
        dx * dx
        + dy * dy
    )

    if denom <= 1e-12:
        return math.hypot(
            px - x1,
            py - y1,
        )

    t = (
        (
            (px - x1) * dx
            + (py - y1) * dy
        )
        / denom
    )

    t = max(
        0.0,
        min(
            1.0,
            t,
        ),
    )

    qx = x1 + t * dx
    qy = y1 + t * dy

    return math.hypot(
        px - qx,
        py - qy,
    )


def inspect_recess_preservation(
    gcode_text: str,
) -> dict:

    segments = (
        parse_extrusion_segments(
            gcode_text
        )
    )

    offending = []

    for segment in segments:

        if segment.z > 4.5:
            continue

        if segment.z < 0.05:
            continue

        feature = " ".join(
            str(
                segment.feature
            )
            .strip()
            .lower()
            .split()
        )

        if gate_module._is_support(
            feature
        ):
            continue

        if not gate_module._is_model(
            feature
        ):
            continue

        distance = (
            point_segment_distance(
                128.0,
                128.0,
                segment.x1,
                segment.y1,
                segment.x2,
                segment.y2,
            )
        )

        if distance <= 2.0:
            offending.append(
                {
                    "z":
                        segment.z,

                    "feature":
                        feature,

                    "distance_mm":
                        distance,
                }
            )

    return {
        "preserved":
            len(offending) == 0,

        "model_intrusion_segment_count":
            len(offending),

        "first_intrusions":
            offending[:20],
    }


# ------------------------------------------------------------
# Bambu slice
# ------------------------------------------------------------

def run_bambu(
    sample_id: str,
    geometry: Path,
    *,
    tree: bool,
) -> dict:

    strategy = (
        "bambu_tree_auto_hybrid"
        if tree
        else
        "bambu_normal_auto_support"
    )

    out = (
        RUN_ROOT
        / sample_id
        / strategy
    )

    clean_dir(
        out
    )

    process_path = (
        out
        / "process.json"
    )

    process = (
        make_bambu_process(
            process_path,
            tree=tree,
        )
    )

    artifact = (
        out
        / "output.gcode.3mf"
    )

    command = [
        str(BAMBU),

        "--arrange",
        "0",

        "--ensure-on-bed",

        "--slice",
        "0",

        "--debug",
        "2",

        "--outputdir",
        str(out),

        "--export-3mf",
        artifact.name,

        "--load-settings",
        ";".join(
            [
                str(
                    BBL[
                        "machine"
                    ]
                ),
                str(
                    process_path
                ),
            ]
        ),

        "--load-filaments",
        str(
            BBL[
                "filament"
            ]
        ),

        str(
            geometry
        ),
    ]

    started = time.perf_counter()

    execution = (
        run_bambu_cli(
            command,
            expected_outputs=[
                artifact
            ],
            cwd=out,
            timeout=1800,
        )
    )

    runtime = (
        time.perf_counter()
        - started
    )

    if not execution.success:
        raise RuntimeError(
            "BAMBU_SLICE_FAILED:"
            + sample_id
            + ":"
            + strategy
            + "\nSTDOUT:\n"
            + execution.stdout[-3000:]
            + "\nSTDERR:\n"
            + execution.stderr[-3000:]
        )

    if not artifact.is_file():
        raise RuntimeError(
            "BAMBU_ARTIFACT_MISSING:"
            + sample_id
            + ":"
            + strategy
        )

    repair_bambu_model_settings_xml(
        artifact
    )

    set_gate_semantics(
        "bambu"
    )

    gate = (
        gate_module.inspect_final_gcode_printability(
            artifact,
            geometry_path=geometry,
        )
    )

    text = read_zip_gcode(
        artifact
    )

    critical = None

    if (
        sample_id
        == "base_recessed_hole"
    ):
        critical = (
            inspect_recess_preservation(
                text
            )
        )

    result = {
        "sample":
            sample_id,

        "strategy":
            strategy,

        "slicer":
            "bambu",

        "runtime_seconds":
            runtime,

        "artifact":
            str(artifact),

        "gate":
            gate_metrics(
                gate
            ),

        "critical_feature":
            critical,

        "process_settings": {
            "enable_support":
                process.get(
                    "enable_support"
                ),

            "support_type":
                process.get(
                    "support_type"
                ),

            "support_style":
                process.get(
                    "support_style"
                ),

            "support_threshold_angle":
                process.get(
                    "support_threshold_angle"
                ),
        },
    }

    write_json(
        out
        / "result.json",
        result,
    )

    return result


# ------------------------------------------------------------
# Orca slice
# ------------------------------------------------------------

def run_orca(
    sample_id: str,
    geometry: Path,
    *,
    support: bool,
) -> dict:

    strategy = (
        "orca_mop_plus_support_fallback"
        if support
        else
        "orca_mop_without_support"
    )

    out = (
        RUN_ROOT
        / sample_id
        / strategy
    )

    clean_dir(
        out
    )

    process_path = (
        out
        / "process.json"
    )

    process = (
        make_orca_process(
            process_path,
            support=support,
        )
    )

    command = [
        str(ORCA),

        "--arrange",
        "0",

        "--ensure-on-bed",

        "--slice",
        "0",

        "--no-check",

        "--load-settings",
        ";".join(
            [
                str(
                    ORC[
                        "machine"
                    ]
                ),
                str(
                    process_path
                ),
            ]
        ),

        "--load-filaments",
        str(
            ORC[
                "filament"
            ]
        ),

        "--load-defaultfila",

        "--outputdir",
        str(out),

        str(
            geometry
        ),
    ]

    started = time.perf_counter()

    proc = subprocess.run(
        command,
        cwd=out,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=1800,
    )

    runtime = (
        time.perf_counter()
        - started
    )

    if proc.returncode != 0:
        raise RuntimeError(
            "ORCA_SLICE_FAILED:"
            + sample_id
            + ":"
            + strategy
            + f":exit={proc.returncode}"
            + "\nSTDOUT:\n"
            + proc.stdout[-3000:]
            + "\nSTDERR:\n"
            + proc.stderr[-3000:]
        )

    gcodes = sorted(
        out.glob(
            "*.gcode"
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not gcodes:
        raise RuntimeError(
            "ORCA_GCODE_MISSING:"
            + sample_id
            + ":"
            + strategy
        )

    gcode = gcodes[0]

    wrapper = (
        out
        / "gate_input.gcode.3mf"
    )

    wrap_orca_gcode(
        gcode,
        wrapper,
        process,
    )

    set_gate_semantics(
        "orca"
    )

    gate = (
        gate_module.inspect_final_gcode_printability(
            wrapper,
            geometry_path=geometry,
        )
    )

    text = gcode.read_text(
        encoding="utf-8",
        errors="replace",
    )

    critical = None

    if (
        sample_id
        == "base_recessed_hole"
    ):
        critical = (
            inspect_recess_preservation(
                text
            )
        )

    result = {
        "sample":
            sample_id,

        "strategy":
            strategy,

        "slicer":
            "orca",

        "runtime_seconds":
            runtime,

        "gcode":
            str(gcode),

        "wrapper":
            str(wrapper),

        "gate_semantic_adapter":
            (
                "exact 'overhang wall' "
                "-> existing bridge validation"
            ),

        "gate":
            gate_metrics(
                gate
            ),

        "critical_feature":
            critical,

        "process_settings": {
            "make_overhang_printable":
                process.get(
                    "make_overhang_printable"
                ),

            "make_overhang_printable_angle":
                process.get(
                    "make_overhang_printable_angle"
                ),

            "make_overhang_printable_hole_size":
                process.get(
                    "make_overhang_printable_hole_size"
                ),

            "enable_support":
                process.get(
                    "enable_support"
                ),

            "support_type":
                process.get(
                    "support_type"
                ),
        },
    }

    write_json(
        out
        / "result.json",
        result,
    )

    return result


# ------------------------------------------------------------
# Execute benchmark
# ------------------------------------------------------------

print(
    "=== STRATEGY BENCHMARK V1 ==="
)

print(
    "PREFLIGHT =",
    PREFLIGHT_PATH,
)

print(
    "RUN_ROOT =",
    RUN_ROOT,
)

print(
    "ELIGIBLE_SAMPLES =",
    len(eligible),
)

print(
    "AUTO_ORIENT_RUNS = 0"
)

print(
    "ORCA_GATE_ADAPTER = exact overhang wall -> bridge"
)

print("")

all_results = []

slice_count = 0

for index, sample in enumerate(
    eligible,
    start=1,
):

    sample_id = sample["id"]

    geometry = Path(
        sample[
            "canonical_geometry"
        ][
            "path"
        ]
    ).resolve()

    if not geometry.is_file():
        raise RuntimeError(
            "CANONICAL_GEOMETRY_MISSING:"
            + sample_id
        )

    print(
        "----------------------------------------"
    )

    print(
        f"SAMPLE {index}/8 = {sample_id}"
    )

    print(
        "GEOMETRY =",
        geometry,
    )

    # 1. Bambu Normal
    normal = run_bambu(
        sample_id,
        geometry,
        tree=False,
    )

    slice_count += 1

    all_results.append(
        normal
    )

    print(
        "BAMBU_NORMAL =",
        normal[
            "gate"
        ][
            "status"
        ],
        "| dangerous =",
        normal[
            "gate"
        ][
            "dangerous_layer_count"
        ],
        "| bad_area =",
        normal[
            "gate"
        ][
            "total_bad_area_mm2"
        ],
    )

    # 2. Bambu Tree
    tree = run_bambu(
        sample_id,
        geometry,
        tree=True,
    )

    slice_count += 1

    all_results.append(
        tree
    )

    print(
        "BAMBU_TREE =",
        tree[
            "gate"
        ][
            "status"
        ],
        "| dangerous =",
        tree[
            "gate"
        ][
            "dangerous_layer_count"
        ],
        "| bad_area =",
        tree[
            "gate"
        ][
            "total_bad_area_mm2"
        ],
    )

    # 3. Orca MOP, support OFF
    mop = run_orca(
        sample_id,
        geometry,
        support=False,
    )

    slice_count += 1

    all_results.append(
        mop
    )

    print(
        "ORCA_MOP =",
        mop[
            "gate"
        ][
            "status"
        ],
        "| dangerous =",
        mop[
            "gate"
        ][
            "dangerous_layer_count"
        ],
        "| bad_area =",
        mop[
            "gate"
        ][
            "total_bad_area_mm2"
        ],
    )

    # 4. Conditional fallback only when MOP blocks.
    if (
        mop[
            "gate"
        ][
            "status"
        ]
        != "pass"
    ):

        fallback = run_orca(
            sample_id,
            geometry,
            support=True,
        )

        slice_count += 1

        all_results.append(
            fallback
        )

        print(
            "ORCA_MOP_SUPPORT =",
            fallback[
                "gate"
            ][
                "status"
            ],
            "| dangerous =",
            fallback[
                "gate"
            ][
                "dangerous_layer_count"
            ],
            "| bad_area =",
            fallback[
                "gate"
            ][
                "total_bad_area_mm2"
            ],
        )

    if (
        sample_id
        == "base_recessed_hole"
    ):

        relevant = [
            result
            for result in all_results
            if (
                result["sample"]
                == sample_id
            )
        ]

        for result in relevant:

            critical = (
                result.get(
                    "critical_feature"
                )
                or {}
            )

            print(
                "HOLE_PRESERVATION",
                result[
                    "strategy"
                ],
                "=",
                critical.get(
                    "preserved"
                ),
                "| intrusion_segments =",
                critical.get(
                    "model_intrusion_segment_count"
                ),
            )

    print(
        "SLICES_COMPLETED =",
        slice_count,
    )

# ------------------------------------------------------------
# Aggregate
# ------------------------------------------------------------

strategies = sorted(
    {
        result[
            "strategy"
        ]
        for result
        in all_results
    }
)

summary = {}

for strategy in strategies:

    rows = [
        result
        for result in all_results
        if (
            result[
                "strategy"
            ]
            == strategy
        )
    ]

    pass_count = sum(
        1
        for row in rows
        if (
            row[
                "gate"
            ][
                "status"
            ]
            == "pass"
        )
    )

    bad_areas = [
        float(
            row[
                "gate"
            ][
                "total_bad_area_mm2"
            ]
            or 0.0
        )
        for row in rows
    ]

    support_lengths = [
        float(
            row[
                "gate"
            ][
                "support_xy_length_mm"
            ]
            or 0.0
        )
        for row in rows
    ]

    runtimes = [
        float(
            row[
                "runtime_seconds"
            ]
        )
        for row in rows
    ]

    hole_rows = [
        row
        for row in rows
        if (
            row[
                "sample"
            ]
            == "base_recessed_hole"
        )
    ]

    hole_preserved = None

    if hole_rows:
        hole_preserved = (
            hole_rows[0]
            .get(
                "critical_feature",
                {}
            )
            .get(
                "preserved"
            )
        )

    summary[strategy] = {
        "evaluated_samples":
            len(rows),

        "pass_count":
            pass_count,

        "pass_rate":
            (
                pass_count
                / len(rows)
                if rows
                else 0.0
            ),

        "total_bad_area_mm2":
            sum(
                bad_areas
            ),

        "mean_bad_area_mm2":
            (
                sum(
                    bad_areas
                )
                / len(
                    bad_areas
                )
                if bad_areas
                else None
            ),

        "total_support_xy_mm":
            sum(
                support_lengths
            ),

        "mean_runtime_seconds":
            (
                sum(
                    runtimes
                )
                / len(
                    runtimes
                )
                if runtimes
                else None
            ),

        "base_recessed_hole_preserved":
            hole_preserved,
    }

# ------------------------------------------------------------
# Decision rules
# ------------------------------------------------------------

primary = {
    key: value
    for key, value in summary.items()
    if key
    in (
        "bambu_normal_auto_support",
        "bambu_tree_auto_hybrid",
        "orca_mop_without_support",
    )
}

eligible_winners = []

for strategy, metrics in primary.items():

    hole_ok = (
        metrics[
            "base_recessed_hole_preserved"
        ]
        is not False
    )

    if hole_ok:
        eligible_winners.append(
            (
                metrics[
                    "pass_count"
                ],
                -float(
                    metrics[
                        "total_bad_area_mm2"
                    ]
                ),
                -float(
                    metrics[
                        "total_support_xy_mm"
                    ]
                ),
                strategy,
            )
        )

eligible_winners.sort(
    reverse=True
)

best_primary = (
    eligible_winners[0][3]
    if eligible_winners
    else None
)

final_report = {
    "schema_version":
        "1.0",

    "preflight":
        str(
            PREFLIGHT_PATH
        ),

    "run_root":
        str(
            RUN_ROOT
        ),

    "eligible_sample_count":
        len(
            eligible
        ),

    "expected_pre_strategy_block":
        "true_floating_island",

    "auto_orient_runs":
        0,

    "slice_count":
        slice_count,

    "orca_gate_adapter":
        (
            "exact overhang wall "
            "-> existing bridge validation"
        ),

    "results":
        all_results,

    "strategy_summary":
        summary,

    "best_primary_by_v1_rules":
        best_primary,

    "decision_policy": {
        "priority_1":
            "critical geometry preservation",

        "priority_2":
            "strict Gate pass count",

        "priority_3":
            "lower residual bad area",

        "priority_4":
            "lower support material cost",
    },
}

report_path = (
    RUN_ROOT
    / "benchmark_results.json"
)

write_json(
    report_path,
    final_report,
)

print("")
print(
    "========================================"
)

print(
    "=== STRATEGY SUMMARY ==="
)

print(
    "========================================"
)

for strategy in strategies:

    metrics = summary[
        strategy
    ]

    print(
        strategy,
        "=",
        {
            "evaluated":
                metrics[
                    "evaluated_samples"
                ],

            "pass":
                metrics[
                    "pass_count"
                ],

            "pass_rate":
                round(
                    metrics[
                        "pass_rate"
                    ],
                    4,
                ),

            "bad_area_sum":
                round(
                    metrics[
                        "total_bad_area_mm2"
                    ],
                    4,
                ),

            "support_xy_sum":
                round(
                    metrics[
                        "total_support_xy_mm"
                    ],
                    2,
                ),

            "hole_preserved":
                metrics[
                    "base_recessed_hole_preserved"
                ],
        },
    )

print("")
print(
    "BEST_PRIMARY_BY_V1_RULES =",
    best_primary,
)

print(
    "TOTAL_SLICING_RUNS =",
    slice_count,
)

print(
    "AUTO_ORIENT_RUNS = 0"
)

print(
    "PIPELINE_SOURCE_FILES_MODIFIED = 0"
)

print(
    "REPORT =",
    report_path,
)

print(
    "NEXT_GATE = BENCHMARK_RESULT_REVIEW"
)
