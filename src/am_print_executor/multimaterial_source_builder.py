from __future__ import annotations

import argparse
import hashlib
import json

from pathlib import Path
from typing import Any

import numpy as np
import trimesh


class MultiMaterialSourceError(RuntimeError):
    pass


_AXIS_INDEX = {
    "x": 0,
    "y": 1,
    "z": 2,
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def _load_closed_mesh(
    path: Path,
) -> trimesh.Trimesh:
    path = path.expanduser().resolve()

    if not path.is_file():
        raise MultiMaterialSourceError(
            f"Input model missing: {path}"
        )

    mesh = trimesh.load_mesh(
        path,
        process=True,
    )

    if not isinstance(
        mesh,
        trimesh.Trimesh,
    ):
        raise MultiMaterialSourceError(
            "Input did not resolve to a single mesh."
        )

    if len(mesh.faces) == 0:
        raise MultiMaterialSourceError(
            "Input mesh contains no faces."
        )

    if not mesh.is_winding_consistent:
        raise MultiMaterialSourceError(
            "Normalized mesh winding is inconsistent."
        )

    if not mesh.is_watertight:
        raise MultiMaterialSourceError(
            "Normalized mesh is not watertight."
        )

    if not mesh.is_volume:
        raise MultiMaterialSourceError(
            "Normalized mesh is not a closed volume."
        )

    return mesh


def _parse_cut_ratios(
    values: list[float],
) -> list[float]:
    if not values:
        raise MultiMaterialSourceError(
            "At least one cut ratio is required."
        )

    ratios = [
        float(value)
        for value in values
    ]

    for ratio in ratios:
        if not 0.0 < ratio < 1.0:
            raise MultiMaterialSourceError(
                "Every cut ratio must satisfy 0 < ratio < 1."
            )

    if ratios != sorted(ratios):
        raise MultiMaterialSourceError(
            "Cut ratios must be strictly increasing."
        )

    if len(set(ratios)) != len(ratios):
        raise MultiMaterialSourceError(
            "Duplicate cut ratios are not allowed."
        )

    return ratios


def _parse_cut_positions(
    mesh: trimesh.Trimesh,
    *,
    axis_index: int,
    values: list[float],
) -> list[float]:
    positions = [
        float(value)
        for value in values
    ]

    if not positions:
        raise MultiMaterialSourceError(
            "At least one cut position is required."
        )

    minimum = float(
        mesh.bounds[0, axis_index]
    )

    maximum = float(
        mesh.bounds[1, axis_index]
    )

    for position in positions:
        if not minimum < position < maximum:
            raise MultiMaterialSourceError(
                "Every cut position must be strictly "
                "inside the source bounds."
            )

    if positions != sorted(positions):
        raise MultiMaterialSourceError(
            "Cut positions must be strictly increasing."
        )

    if len(set(positions)) != len(positions):
        raise MultiMaterialSourceError(
            "Duplicate cut positions are not allowed."
        )

    return positions


def _ratios_to_positions(
    mesh: trimesh.Trimesh,
    *,
    axis_index: int,
    ratios: list[float],
) -> list[float]:
    minimum = float(
        mesh.bounds[0, axis_index]
    )

    extent = float(
        mesh.extents[axis_index]
    )

    return [
        minimum + extent * ratio
        for ratio in ratios
    ]


def _plane_origin(
    mesh: trimesh.Trimesh,
    *,
    axis_index: int,
    coordinate: float,
) -> np.ndarray:
    origin = np.asarray(
        mesh.bounds.mean(axis=0),
        dtype=float,
    )

    origin[axis_index] = float(
        coordinate
    )

    return origin


def _slice_interval(
    source: trimesh.Trimesh,
    *,
    axis_index: int,
    lower: float | None,
    upper: float | None,
) -> trimesh.Trimesh:
    axis = np.zeros(
        3,
        dtype=float,
    )

    axis[axis_index] = 1.0

    part = source.copy()

    if lower is not None:
        part = part.slice_plane(
            plane_origin=_plane_origin(
                source,
                axis_index=axis_index,
                coordinate=lower,
            ),
            plane_normal=axis,
            cap=True,
        )

    if part is None:
        raise MultiMaterialSourceError(
            "Lower-plane slicing produced no mesh."
        )

    if upper is not None:
        part = part.slice_plane(
            plane_origin=_plane_origin(
                source,
                axis_index=axis_index,
                coordinate=upper,
            ),
            plane_normal=-axis,
            cap=True,
        )

    if part is None:
        raise MultiMaterialSourceError(
            "Upper-plane slicing produced no mesh."
        )

    part.remove_unreferenced_vertices()

    if len(part.faces) == 0:
        raise MultiMaterialSourceError(
            "Partition produced an empty region."
        )

    if not part.is_winding_consistent:
        raise MultiMaterialSourceError(
            "Partition winding is inconsistent."
        )

    if not part.is_watertight:
        raise MultiMaterialSourceError(
            "Partition is not watertight."
        )

    if not part.is_volume:
        raise MultiMaterialSourceError(
            "Partition is not a valid volume."
        )

    return part


def _validate_exported_stl(
    path: Path,
) -> dict[str, Any]:
    mesh = trimesh.load_mesh(
        path,
        process=True,
    )

    if not isinstance(
        mesh,
        trimesh.Trimesh,
    ):
        raise MultiMaterialSourceError(
            f"Export did not reload as mesh: {path}"
        )

    if not (
        mesh.is_watertight
        and mesh.is_winding_consistent
        and mesh.is_volume
    ):
        raise MultiMaterialSourceError(
            f"Exported partition failed topology validation: {path}"
        )

    return {
        "sha256":
            _sha256(path),

        "size_bytes":
            path.stat().st_size,

        "vertex_count":
            int(len(mesh.vertices)),

        "face_count":
            int(len(mesh.faces)),

        "is_watertight":
            bool(mesh.is_watertight),

        "is_volume":
            bool(mesh.is_volume),

        "volume_mm3":
            float(abs(mesh.volume)),

        "bounds_mm":
            mesh.bounds.tolist(),
    }


def build_multimaterial_source(
    *,
    input_model: Path,
    output_dir: Path,
    job_request_id: str,
    axis: str,
    filament_profiles: list[Path],
    position_mm: list[float],
    assembly_group: int = 1,
    cut_ratios: list[float] | None = None,
    cut_positions_mm: list[float] | None = None,
    volume_tolerance: float = 1e-5,
    overwrite: bool = False,
) -> dict[str, Any]:
    axis = axis.lower()

    if axis not in _AXIS_INDEX:
        raise MultiMaterialSourceError(
            "axis must be x, y, or z."
        )

    if (
        (cut_ratios is None)
        == (cut_positions_mm is None)
    ):
        raise MultiMaterialSourceError(
            "Supply exactly one of cut_ratios "
            "or cut_positions_mm."
        )

    if not job_request_id.strip():
        raise MultiMaterialSourceError(
            "job_request_id cannot be empty."
        )

    if assembly_group < 1:
        raise MultiMaterialSourceError(
            "assembly_group must be >= 1."
        )

    if len(position_mm) != 3:
        raise MultiMaterialSourceError(
            "position_mm must contain x, y, z."
        )

    if volume_tolerance <= 0:
        raise MultiMaterialSourceError(
            "volume_tolerance must be positive."
        )

    input_model = (
        input_model
        .expanduser()
        .resolve()
    )

    output_dir = (
        output_dir
        .expanduser()
        .resolve()
    )

    profiles = [
        path.expanduser().resolve()
        for path in filament_profiles
    ]

    for profile in profiles:
        if not profile.is_file():
            raise MultiMaterialSourceError(
                f"Filament profile missing: {profile}"
            )

    source = _load_closed_mesh(
        input_model
    )

    axis_index = _AXIS_INDEX[
        axis
    ]

    if cut_ratios is not None:
        ratios = _parse_cut_ratios(
            cut_ratios
        )

        positions = _ratios_to_positions(
            source,
            axis_index=axis_index,
            ratios=ratios,
        )

        cut_mode = "ratio"

    else:
        assert cut_positions_mm is not None

        positions = _parse_cut_positions(
            source,
            axis_index=axis_index,
            values=cut_positions_mm,
        )

        ratios = None
        cut_mode = "absolute_mm"

    expected_regions = (
        len(positions) + 1
    )

    if len(profiles) != expected_regions:
        raise MultiMaterialSourceError(
            "Filament profile count must equal "
            "number of regions. "
            f"expected={expected_regions}, "
            f"actual={len(profiles)}"
        )

    boundaries: list[
        tuple[
            float | None,
            float | None,
        ]
    ] = []

    for index in range(
        expected_regions
    ):
        lower = (
            positions[index - 1]
            if index > 0
            else None
        )

        upper = (
            positions[index]
            if index < len(positions)
            else None
        )

        boundaries.append(
            (lower, upper)
        )

    parts = []

    for lower, upper in boundaries:
        parts.append(
            _slice_interval(
                source,
                axis_index=axis_index,
                lower=lower,
                upper=upper,
            )
        )

    source_volume = float(
        abs(source.volume)
    )

    partition_volume = float(
        sum(
            abs(part.volume)
            for part in parts
        )
    )

    relative_error = abs(
        partition_volume
        - source_volume
    ) / source_volume

    if relative_error > volume_tolerance:
        raise MultiMaterialSourceError(
            "Partition volume conservation failed: "
            f"relative_error={relative_error}, "
            f"tolerance={volume_tolerance}"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest_path = (
        output_dir
        / "multimaterial_job.json"
    )

    existing_parts = list(
        output_dir.glob(
            "region_*.stl"
        )
    )

    if (
        manifest_path.exists()
        or existing_parts
    ):
        if not overwrite:
            raise MultiMaterialSourceError(
                "Managed output already exists. "
                "Use --overwrite for an intentional rebuild."
            )

        for old in existing_parts:
            old.unlink()

        if manifest_path.exists():
            manifest_path.unlink()

    objects = []
    exported_parts = []

    for index, part in enumerate(
        parts,
        start=1,
    ):
        output_path = (
            output_dir
            / f"region_{index:02d}.stl"
        )

        temp_path = (
            output_dir
            / f".region_{index:02d}.tmp.stl"
        )

        if temp_path.exists():
            temp_path.unlink()

        part.export(
            str(temp_path),
            file_type="stl",
        )

        inspection = (
            _validate_exported_stl(
                temp_path
            )
        )

        temp_path.replace(
            output_path
        )

        exported_parts.append(
            {
                "region_index":
                    index,

                "filament_id":
                    index,

                "path":
                    str(output_path),

                **inspection,
            }
        )

        objects.append(
            {
                "path":
                    str(output_path),

                "project_filament_ids":
                    [index],

                "count":
                    1,

                "positions_mm": [
                    [
                        float(position_mm[0]),
                        float(position_mm[1]),
                        float(position_mm[2]),
                    ]
                ],

                "assemble_index":
                    [assembly_group],
            }
        )

    filament_rows = [
        {
            "id":
                index,

            "path":
                str(profile),
        }
        for index, profile
        in enumerate(
            profiles,
            start=1,
        )
    ]

    manifest = {
        "schema_version":
            "1.0.0",

        "module":
            "M4",

        "stage":
            "generic_multimaterial_source",

        "status":
            "multimaterial_job_ready",

        "job_request_id":
            job_request_id,

        "source_model": {
            "path":
                str(input_model),

            "sha256":
                _sha256(input_model),

            "vertex_count":
                int(len(source.vertices)),

            "face_count":
                int(len(source.faces)),

            "bounds_mm":
                source.bounds.tolist(),

            "extents_mm":
                source.extents.tolist(),

            "volume_mm3":
                source_volume,

            "is_watertight":
                bool(source.is_watertight),

            "is_volume":
                bool(source.is_volume),
        },

        "partition": {
            "axis":
                axis,

            "mode":
                cut_mode,

            "cut_ratios":
                ratios,

            "cut_positions_mm":
                positions,

            "region_count":
                expected_regions,

            "volume_tolerance":
                volume_tolerance,

            "combined_volume_mm3":
                partition_volume,

            "relative_volume_error":
                relative_error,

            "volume_conserved":
                True,
        },

        "placement": {
            "position_mm": [
                float(position_mm[0]),
                float(position_mm[1]),
                float(position_mm[2]),
            ],

            "assembly_group":
                assembly_group,
        },

        "filament_profiles":
            filament_rows,

        "objects":
            objects,

        "generated_regions":
            exported_parts,

        "policy": {
            "source_model_modified":
                False,

            "material_count_hardcoded":
                False,

            "cut_position_hardcoded":
                False,

            "request_id_hardcoded":
                False,

            "ams_slot_mapping_embedded":
                False,

            "printer_ip_embedded":
                False,

            "network_used":
                False,

            "printer_command_sent":
                False,
        },
    }

    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "status":
            "multimaterial_source_built",

        "manifest":
            str(manifest_path),

        "job_request_id":
            job_request_id,

        "region_count":
            expected_regions,

        "filament_count":
            len(profiles),

        "relative_volume_error":
            relative_error,

        "source_model_modified":
            False,

        "network_used":
            False,

        "printer_command_sent":
            False,
    }


def cli_main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a generic closed-volume "
            "multi-material source job."
        )
    )

    parser.add_argument(
        "--input-model",
        required=True,
    )

    parser.add_argument(
        "--output-dir",
        required=True,
    )

    parser.add_argument(
        "--job-request-id",
        required=True,
    )

    parser.add_argument(
        "--axis",
        choices=("x", "y", "z"),
        required=True,
    )

    cuts = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    cuts.add_argument(
        "--cut-ratio",
        type=float,
        action="append",
    )

    cuts.add_argument(
        "--cut-position-mm",
        type=float,
        action="append",
    )

    parser.add_argument(
        "--filament-profile",
        action="append",
        required=True,
    )

    parser.add_argument(
        "--position-x-mm",
        type=float,
        required=True,
    )

    parser.add_argument(
        "--position-y-mm",
        type=float,
        required=True,
    )

    parser.add_argument(
        "--position-z-mm",
        type=float,
        required=True,
    )

    parser.add_argument(
        "--assembly-group",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--volume-tolerance",
        type=float,
        default=1e-5,
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    args = parser.parse_args(
        argv
    )

    try:
        result = (
            build_multimaterial_source(
                input_model=Path(
                    args.input_model
                ),

                output_dir=Path(
                    args.output_dir
                ),

                job_request_id=
                    args.job_request_id,

                axis=args.axis,

                cut_ratios=
                    args.cut_ratio,

                cut_positions_mm=
                    args.cut_position_mm,

                filament_profiles=[
                    Path(value)
                    for value
                    in args.filament_profile
                ],

                position_mm=[
                    args.position_x_mm,
                    args.position_y_mm,
                    args.position_z_mm,
                ],

                assembly_group=
                    args.assembly_group,

                volume_tolerance=
                    args.volume_tolerance,

                overwrite=
                    args.overwrite,
            )
        )

    except Exception as exc:
        print(
            json.dumps(
                {
                    "status":
                        "multimaterial_source_blocked",

                    "error":
                        f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        return 2

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
