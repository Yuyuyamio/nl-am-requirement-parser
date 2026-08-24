from __future__ import annotations

import argparse
import hashlib
import json
import shutil

from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from am_print_executor.bambu_headless_cli import (
    run_bambu_cli,
)
from am_print_executor.gcode_printability_gate import (
    inspect_final_gcode_printability,
)
from am_print_executor.flat_base_gate import (
    inspect_flat_printing_base,
)
from am_print_executor.multimaterial_project import (
    repair_bambu_model_settings_xml,
)
from am_print_executor.printable_orientation import (
    orient_for_printing,
)


class M3PrintabilityOptimizerError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def _load_mesh(path: Path) -> trimesh.Trimesh:
    obj = trimesh.load(
        path,
        force="mesh",
        process=False,
    )

    if isinstance(obj, trimesh.Scene):
        meshes = [
            g
            for g in obj.geometry.values()
            if isinstance(g, trimesh.Trimesh)
        ]

        if not meshes:
            raise M3PrintabilityOptimizerError(
                "No triangle mesh in input."
            )

        obj = trimesh.util.concatenate(meshes)

    if not isinstance(obj, trimesh.Trimesh):
        raise M3PrintabilityOptimizerError(
            "Input is not a triangle mesh."
        )

    return obj


def _place_at_bed_center(
    mesh: trimesh.Trimesh,
    *,
    bed_x_mm: float = 256.0,
    bed_y_mm: float = 256.0,
) -> None:

    bounds = np.asarray(
        mesh.bounds,
        dtype=float,
    )

    mesh.apply_translation(
        [
            0.0,
            0.0,
            -float(bounds[0, 2]),
        ]
    )

    bounds = np.asarray(
        mesh.bounds,
        dtype=float,
    )

    cx = (
        float(bounds[0, 0])
        + float(bounds[1, 0])
    ) / 2.0

    cy = (
        float(bounds[0, 1])
        + float(bounds[1, 1])
    ) / 2.0

    mesh.apply_translation(
        [
            bed_x_mm / 2.0 - cx,
            bed_y_mm / 2.0 - cy,
            0.0,
        ]
    )


def export_orientation_candidates(
    *,
    input_stl: Path,
    work_dir: Path,
    top_n: int = 6,
) -> list[dict[str, Any]]:

    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Reuse the project's canonical orientation analysis.
    primary_stl = (
        work_dir
        / "_primary_orientation.stl"
    )

    report_path = (
        work_dir
        / "_orientation_candidates.json"
    )

    report = orient_for_printing(
        input_path=input_stl,
        output_path=primary_stl,
        report_path=report_path,
    )

    records = report.get(
        "top_candidates",
        []
    )

    if not records:
        raise M3PrintabilityOptimizerError(
            "Orientation analysis returned no candidates."
        )

    original = _load_mesh(
        input_stl
    )

    exported = []

    seen_sha = set()

    for index, record in enumerate(
        records[:top_n]
    ):

        mesh = original.copy()

        transform = np.asarray(
            record["transform"],
            dtype=float,
        )

        mesh.apply_transform(
            transform
        )

        _place_at_bed_center(
            mesh
        )

        # Orientation search may rotate a previously protected planar base
        # away from the bed. Reject that candidate instead of treating one
        # lowest point or a curved surface as a valid placement.
        flat_base = inspect_flat_printing_base(mesh)
        if not flat_base["base_flatness_passed"]:
            continue

        candidate_path = (
            work_dir
            / (
                f"orientation_{index:02d}"
                ".stl"
            )
        )

        mesh.export(
            candidate_path
        )

        sha = _sha256(
            candidate_path
        )

        if sha in seen_sha:
            candidate_path.unlink(
                missing_ok=True
            )
            continue

        seen_sha.add(sha)

        bounds = np.asarray(
            mesh.bounds,
            dtype=float,
        )

        extents = np.asarray(
            mesh.extents,
            dtype=float,
        )

        exported.append({
            "candidate_index":
                index,

            "path":
                str(candidate_path),

            "sha256":
                sha,

            "source":
                record.get("source"),

            "stable_probability":
                record.get(
                    "stable_probability",
                    0.0,
                ),

            "geometry_score":
                record.get(
                    "score",
                    0.0,
                ),

            "bed_contact_area_mm2":
                flat_base[
                    "bed_contact_area_mm2"
                ],

            "flat_base":
                flat_base,

            "overhang_area_mm2":
                record.get(
                    "overhang_area_mm2",
                    0.0,
                ),

            "bounds_mm":
                bounds.tolist(),

            "extents_mm":
                extents.tolist(),

            "height_mm":
                float(extents[2]),

            "transform":
                record["transform"],
        })

    return exported


def build_support_profile(
    *,
    source_profile: Path,
    output_profile: Path,
    threshold_angle: int,
) -> dict[str, Any]:

    obj = json.loads(
        source_profile.read_text(
            encoding="utf-8-sig"
        )
    )

    obj.update({
        "name":
            (
                "NL-AM Printability Search "
                f"Normal Auto {threshold_angle}"
            ),

        "enable_support":
            "1",

        "support_type":
            "normal(auto)",

        "support_on_build_plate_only":
            "0",

        "support_critical_regions_only":
            "0",

        "support_remove_small_overhang":
            "0",

        "support_threshold_angle":
            str(threshold_angle),

        # Conservative support-interface policy.
        "support_interface_top_layers":
            "3",

        "support_interface_bottom_layers":
            "2",

        "support_interface_spacing":
            "0.35",

        "support_top_z_distance":
            "0.2",

        "support_bottom_z_distance":
            "0.2",

        "support_base_pattern_spacing":
            "2.0",

        "detect_floating_vertical_shell":
            "1",

        "detect_overhang_wall":
            "1",

        # Do not treat long bridges as an excuse
        # to skip support.
        "bridge_no_support":
            "0",
    })

    output_profile.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return obj


def _candidate_rank(
    item: dict[str, Any],
) -> tuple[Any, ...]:

    gate = item["printability"]

    orientation = item[
        "orientation"
    ]

    # Hard PASS is already required before this
    # function is used.
    #
    # Then prefer:
    # 1. less support toolpath
    # 2. lower overhang area
    # 3. larger bed contact
    # 4. lower object height
    return (
        float(
            gate.get(
                "support_xy_length_mm",
                1e18,
            )
        ),

        float(
            orientation.get(
                "overhang_area_mm2",
                1e18,
            )
        ),

        -float(
            orientation.get(
                "bed_contact_area_mm2",
                0.0,
            )
        ),

        float(
            orientation.get(
                "height_mm",
                1e18,
            )
        ),
    )


def optimize_printability(
    *,
    input_stl: Path,
    studio_exe: Path,
    machine_profile: Path,
    process_profile: Path,
    filament_profile: Path,
    output_dir: Path,
    top_n_orientations: int = 6,
) -> dict[str, Any]:

    input_stl = Path(
        input_stl
    ).resolve()

    output_dir = Path(
        output_dir
    ).resolve()

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    candidate_dir = (
        output_dir
        / "candidates"
    )

    if candidate_dir.exists():
        shutil.rmtree(
            candidate_dir
        )

    candidate_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    orientations = (
        export_orientation_candidates(
            input_stl=input_stl,
            work_dir=candidate_dir,
            top_n=top_n_orientations,
        )
    )

    strategies = [
        {
            "name":
                "normal_auto_30",
            "threshold_angle":
                30,
        },
        {
            "name":
                "normal_auto_45",
            "threshold_angle":
                45,
        },
        {
            "name":
                "normal_auto_60",
            "threshold_angle":
                60,
        },
    ]

    trials = []

    passes = []

    for orientation in orientations:

        orientation_path = Path(
            orientation["path"]
        )

        orientation_index = (
            orientation[
                "candidate_index"
            ]
        )

        for strategy in strategies:

            trial_name = (
                f"o{orientation_index:02d}_"
                f"{strategy['name']}"
            )

            trial_dir = (
                candidate_dir
                / trial_name
            )

            trial_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            support_profile = (
                trial_dir
                / "process.json"
            )

            build_support_profile(
                source_profile=
                    process_profile,
                output_profile=
                    support_profile,
                threshold_angle=
                    strategy[
                        "threshold_angle"
                    ],
            )

            artifact = (
                trial_dir
                / "candidate.gcode.3mf"
            )

            if artifact.exists():
                artifact.unlink()

            command = [
                str(studio_exe),

                # Never reorient or rearrange the
                # already validated candidate.
                "--arrange",
                "0",

                "--ensure-on-bed",

                "--slice",
                "0",

                "--debug",
                "2",

                "--outputdir",
                str(trial_dir),

                "--export-3mf",
                artifact.name,

                "--load-settings",
                ";".join([
                    str(
                        Path(
                            machine_profile
                        ).resolve()
                    ),
                    str(
                        support_profile.resolve()
                    ),
                ]),

                "--load-filaments",
                str(
                    Path(
                        filament_profile
                    ).resolve()
                ),

                str(
                    orientation_path
                ),
            ]

            slice_result = (
                run_bambu_cli(
                    command,
                    expected_outputs=[
                        artifact
                    ],
                    cwd=trial_dir,
                    timeout=1800,
                )
            )

            trial = {
                "trial":
                    trial_name,

                "orientation":
                    orientation,

                "support_strategy":
                    strategy,

                "slice": {
                    "success":
                        slice_result.success,

                    "raw_exit":
                        slice_result.raw_exit,

                    "signed_exit":
                        slice_result.signed_exit,

                    "outputs_exist":
                        slice_result.outputs_exist,

                    "stdout_tail":
                        slice_result.stdout[
                            -3000:
                        ],

                    "stderr_tail":
                        slice_result.stderr[
                            -3000:
                        ],
                },

                "artifact":
                    str(artifact),
            }

            if not slice_result.success:
                trial[
                    "printability"
                ] = {
                    "status":
                        "blocked",

                    "blockers": [
                        "slice_failed"
                    ],
                }

                trials.append(trial)
                continue

            try:
                repair_bambu_model_settings_xml(
                    artifact
                )

            except Exception as exc:
                trial[
                    "printability"
                ] = {
                    "status":
                        "blocked",

                    "blockers": [
                        "xml_repair_failed:"
                        + repr(exc)
                    ],
                }

                trials.append(trial)
                continue

            gate = (
                inspect_final_gcode_printability(
                    artifact,
                    geometry_path=orientation_path,
                )
            )

            trial[
                "printability"
            ] = gate

            trials.append(
                trial
            )

            if (
                gate.get("status")
                == "pass"
            ):
                passes.append(
                    trial
                )

    report: dict[str, Any] = {
        "schema_version":
            "0.1.0",

        "module":
            "M3",

        "optimizer":
            "multi_orientation_multi_support",

        "input_stl":
            str(input_stl),

        "input_sha256":
            _sha256(input_stl),

        "orientation_count":
            len(orientations),

        "support_strategy_count":
            len(strategies),

        "trial_count":
            len(trials),

        "pass_count":
            len(passes),

        "trials":
            trials,
    }

    if not passes:

        report["status"] = (
            "needs_geometry_regeneration"
        )

        report["next_module"] = (
            "M2_REPAIR"
        )

        failures = []

        for trial in trials:

            gate = trial.get(
                "printability",
                {}
            )

            failures.append({
                "trial":
                    trial["trial"],

                "status":
                    gate.get(
                        "status"
                    ),

                "dangerous_layer_count":
                    gate.get(
                        "dangerous_layer_count"
                    ),

                "total_bad_area_mm2":
                    gate.get(
                        "total_bad_area_mm2"
                    ),

                "worst_bad_area_mm2":
                    gate.get(
                        "worst_bad_area_mm2"
                    ),

                "longest_bad_bridge_mm":
                    gate.get(
                        "longest_bad_bridge_mm"
                    ),

                "blockers":
                    gate.get(
                        "blockers",
                        [],
                    )[:20],
            })

        feedback = {
            "schema_version":
                "0.1.0",

            "module":
                "M3_TO_M2",

            "status":
                "geometry_not_printable_under_tested_policies",

            "input_stl":
                str(input_stl),

            "reason":
                (
                    "No tested orientation/support "
                    "combination passed final G-code "
                    "printability validation."
                ),

            "required_m2_changes": [
                (
                    "reduce unsupported long "
                    "cantilevers"
                ),
                (
                    "connect isolated protrusions "
                    "to printable body/ground where "
                    "semantically acceptable"
                ),
                (
                    "reduce large downward-facing "
                    "horizontal surfaces"
                ),
                (
                    "preserve requested semantic "
                    "identity and target dimensions"
                ),
                (
                    "regenerate watertight manifold "
                    "geometry suitable for FDM"
                ),
            ],

            "trial_failures":
                failures,
        }

        feedback_path = (
            output_dir
            / "manufacturability_feedback_to_m2.json"
        )

        feedback_path.write_text(
            json.dumps(
                feedback,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        report[
            "feedback_to_m2"
        ] = str(feedback_path)

    else:

        passes.sort(
            key=_candidate_rank
        )

        winner = passes[0]

        winner_artifact = Path(
            winner["artifact"]
        )

        final_artifact = (
            output_dir
            / "m3_printable.gcode.3mf"
        )

        final_stl = (
            output_dir
            / "m3_printable_oriented.stl"
        )

        shutil.copy2(
            winner_artifact,
            final_artifact,
        )

        shutil.copy2(
            Path(
                winner[
                    "orientation"
                ]["path"]
            ),
            final_stl,
        )

        report["status"] = (
            "complete"
        )

        report["next_module"] = (
            "M4"
        )

        report["winner"] = {
            "trial":
                winner["trial"],

            "orientation":
                winner[
                    "orientation"
                ],

            "support_strategy":
                winner[
                    "support_strategy"
                ],

            "printability":
                winner[
                    "printability"
                ],

            "final_artifact":
                str(final_artifact),

            "final_artifact_sha256":
                _sha256(
                    final_artifact
                ),

            "final_stl":
                str(final_stl),

            "final_stl_sha256":
                _sha256(
                    final_stl
                ),
        }

    report_path = (
        output_dir
        / "m3_printability_optimizer_report.json"
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

    return report


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input-stl",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--studio",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--machine",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--process",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--filament",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--orientations",
        type=int,
        default=6,
    )

    args = parser.parse_args()

    report = optimize_printability(
        input_stl=args.input_stl,
        studio_exe=args.studio,
        machine_profile=args.machine,
        process_profile=args.process,
        filament_profile=args.filament,
        output_dir=args.output_dir,
        top_n_orientations=
            args.orientations,
    )

    print(
        json.dumps(
            {
                "status":
                    report["status"],

                "next_module":
                    report.get(
                        "next_module"
                    ),

                "orientation_count":
                    report[
                        "orientation_count"
                    ],

                "trial_count":
                    report[
                        "trial_count"
                    ],

                "pass_count":
                    report[
                        "pass_count"
                    ],

                "winner":
                    (
                        report.get(
                            "winner",
                            {}
                        ).get(
                            "trial"
                        )
                    ),

                "feedback_to_m2":
                    report.get(
                        "feedback_to_m2"
                    ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return (
        0
        if report["status"]
        == "complete"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
