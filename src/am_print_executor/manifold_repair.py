from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


def load_triangle_mesh(
    path: str | Path,
) -> trimesh.Trimesh:
    path = Path(path)

    loaded = trimesh.load(
        str(path),
        process=False,
    )

    if isinstance(
        loaded,
        trimesh.Scene,
    ):
        meshes = [
            g
            for g in loaded.geometry.values()
            if isinstance(
                g,
                trimesh.Trimesh,
            )
            and len(g.faces) > 0
        ]

        if not meshes:
            raise ValueError(
                "scene contains no triangle mesh"
            )

        loaded = (
            trimesh.util.concatenate(
                meshes
            )
        )

    if not isinstance(
        loaded,
        trimesh.Trimesh,
    ):
        raise TypeError(
            "input is not a Trimesh"
        )

    if (
        len(loaded.vertices) == 0
        or len(loaded.faces) == 0
    ):
        raise ValueError(
            "input mesh is empty"
        )

    loaded = loaded.copy()

    # STL commonly stores duplicate vertices per triangle.
    # Topology must be evaluated only after canonical vertex welding;
    # otherwise a geometrically watertight STL may appear to have
    # thousands of boundary edges.
    try:
        loaded.merge_vertices()
    except Exception:
        pass

    try:
        loaded.remove_unreferenced_vertices()
    except Exception:
        pass

    return loaded


def topology_stats(
    mesh: trimesh.Trimesh,
) -> dict[str, Any]:
    edges = np.asarray(
        mesh.edges,
        dtype=np.int64,
    )

    boundary = 0
    nonmanifold = 0

    if len(edges):
        ordered = np.sort(
            edges,
            axis=1,
        )

        _, counts = np.unique(
            ordered,
            axis=0,
            return_counts=True,
        )

        boundary = int(
            np.sum(
                counts == 1
            )
        )

        nonmanifold = int(
            np.sum(
                counts > 2
            )
        )

    try:
        components = mesh.split(
            only_watertight=False
        )

        component_count = len(
            components
        )

    except Exception:
        component_count = -1

    volume = None

    try:
        volume = float(
            abs(mesh.volume)
        )
    except Exception:
        pass

    return {
        "vertices":
            int(
                len(mesh.vertices)
            ),

        "faces":
            int(
                len(mesh.faces)
            ),

        "watertight":
            bool(
                mesh.is_watertight
            ),

        "winding_consistent":
            bool(
                mesh.is_winding_consistent
            ),

        "boundary_edges":
            boundary,

        "nonmanifold_edges":
            nonmanifold,

        "component_count":
            component_count,

        "bounds_mm":
            np.asarray(
                mesh.bounds,
                dtype=float,
            ).tolist(),

        "extents_mm":
            np.asarray(
                mesh.extents,
                dtype=float,
            ).tolist(),

        "volume_mm3":
            volume,
    }


def _cleanup(
    mesh: trimesh.Trimesh,
) -> trimesh.Trimesh:
    mesh = mesh.copy()

    # Newer trimesh.
    try:
        mask = mesh.nondegenerate_faces()
        mesh.update_faces(mask)
    except Exception:
        # Older trimesh.
        try:
            mesh.remove_degenerate_faces()
        except Exception:
            pass

    try:
        mask = mesh.unique_faces()
        mesh.update_faces(mask)
    except Exception:
        try:
            mesh.remove_duplicate_faces()
        except Exception:
            pass

    try:
        mesh.remove_unreferenced_vertices()
    except Exception:
        pass

    try:
        mesh.merge_vertices()
    except Exception:
        pass

    try:
        trimesh.repair.fix_winding(
            mesh
        )
    except Exception:
        pass

    try:
        trimesh.repair.fix_normals(
            mesh,
            multibody=True,
        )
    except TypeError:
        try:
            trimesh.repair.fix_normals(
                mesh
            )
        except Exception:
            pass
    except Exception:
        pass

    try:
        trimesh.repair.fill_holes(
            mesh
        )
    except Exception:
        pass

    try:
        mesh.remove_unreferenced_vertices()
    except Exception:
        pass

    return mesh


def _restore_reference_bounds(
    mesh: trimesh.Trimesh,
    reference: trimesh.Trimesh,
) -> trimesh.Trimesh:
    """
    Voxel marching-cubes may shift the surface by
    a fraction of one voxel. Restore the original
    exact XYZ extents and center.
    """
    mesh = mesh.copy()

    ref_bounds = np.asarray(
        reference.bounds,
        dtype=float,
    )

    out_bounds = np.asarray(
        mesh.bounds,
        dtype=float,
    )

    ref_extents = (
        ref_bounds[1]
        - ref_bounds[0]
    )

    out_extents = (
        out_bounds[1]
        - out_bounds[0]
    )

    if np.any(
        out_extents <= 1e-9
    ):
        raise ValueError(
            "repaired mesh has zero extent"
        )

    scale = (
        ref_extents
        / out_extents
    )

    out_center = (
        out_bounds[0]
        + out_bounds[1]
    ) / 2.0

    ref_center = (
        ref_bounds[0]
        + ref_bounds[1]
    ) / 2.0

    mesh.apply_translation(
        -out_center
    )

    transform = (
        np.eye(
            4,
            dtype=float,
        )
    )

    transform[
        0,
        0,
    ] = scale[0]

    transform[
        1,
        1,
    ] = scale[1]

    transform[
        2,
        2,
    ] = scale[2]

    mesh.apply_transform(
        transform
    )

    mesh.apply_translation(
        ref_center
    )

    return mesh


def _voxel_solidify(
    source: trimesh.Trimesh,
    *,
    pitch_mm: float,
) -> trimesh.Trimesh:
    voxels = source.voxelized(
        pitch=float(
            pitch_mm
        )
    )

    voxels = voxels.fill()

    repaired = (
        voxels.marching_cubes
    )

    # marching_cubes returns index-space geometry.
    repaired.apply_transform(
        voxels.transform
    )

    repaired = (
        _restore_reference_bounds(
            repaired,
            source,
        )
    )

    repaired = _cleanup(
        repaired
    )

    return repaired


def repair_manifold(
    *,
    input_path: str | Path,
    output_path: str | Path,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    input_path = Path(
        input_path
    ).resolve()

    output_path = Path(
        output_path
    ).resolve()

    source = load_triangle_mesh(
        input_path
    )

    source_stats = (
        topology_stats(
            source
        )
    )

    attempts = []

    # -------------------------------------------
    # Stage A: low-loss cleanup.
    # -------------------------------------------

    conservative = _cleanup(
        source
    )

    conservative_stats = (
        topology_stats(
            conservative
        )
    )

    attempts.append({
        "method":
            "conservative",

        "stats":
            conservative_stats,
    })

    if (
        conservative_stats[
            "watertight"
        ]
        and conservative_stats[
            "boundary_edges"
        ] == 0
        and conservative_stats[
            "nonmanifold_edges"
        ] == 0
    ):
        winner = conservative
        method = "conservative"
        pitch = None

    else:
        winner = None
        method = None
        pitch = None

        # ---------------------------------------
        # Stage B: deterministic solidification.
        #
        # Around 240 cells across the largest
        # dimension, bounded for runtime/detail.
        # For a 60 mm object this is ~0.25 mm.
        # ---------------------------------------

        largest = float(
            np.max(
                source.extents
            )
        )

        base_pitch = (
            largest / 240.0
        )

        base_pitch = min(
            0.35,
            max(
                0.20,
                base_pitch,
            ),
        )

        pitches = [
            base_pitch,
            max(
                0.18,
                base_pitch * 0.80,
            ),
        ]

        seen = set()

        for candidate_pitch in pitches:
            candidate_pitch = round(
                float(
                    candidate_pitch
                ),
                4,
            )

            if candidate_pitch in seen:
                continue

            seen.add(
                candidate_pitch
            )

            repaired = (
                _voxel_solidify(
                    source,
                    pitch_mm=(
                        candidate_pitch
                    ),
                )
            )

            stats = topology_stats(
                repaired
            )

            attempts.append({
                "method":
                    "voxel_solidify",

                "pitch_mm":
                    candidate_pitch,

                "stats":
                    stats,
            })

            if (
                stats["watertight"]
                and stats[
                    "boundary_edges"
                ] == 0
                and stats[
                    "nonmanifold_edges"
                ] == 0
            ):
                winner = repaired
                method = (
                    "voxel_solidify"
                )
                pitch = (
                    candidate_pitch
                )

                break

    if winner is None:
        result = {
            "status":
                "blocked",

            "reason":
                "unable_to_create_watertight_manifold",

            "input":
                str(input_path),

            "source":
                source_stats,

            "attempts":
                attempts,
        }

        if report_path is not None:
            Path(
                report_path
            ).write_text(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        return result

    winner = _cleanup(
        winner
    )

    final_stats = topology_stats(
        winner
    )

    # Exact dimensional contract.
    source_extents = np.asarray(
        source.extents,
        dtype=float,
    )

    final_extents = np.asarray(
        winner.extents,
        dtype=float,
    )

    extent_error = np.abs(
        final_extents
        - source_extents
    )

    maximum_extent_error = float(
        np.max(
            extent_error
        )
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    winner.export(
        output_path
    )

    result = {
        "status":
            "complete",

        "input":
            str(input_path),

        "output":
            str(output_path),

        "method":
            method,

        "pitch_mm":
            pitch,

        "source":
            source_stats,

        "final":
            final_stats,

        "maximum_extent_error_mm":
            maximum_extent_error,

        "attempts":
            attempts,
    }

    if report_path is not None:
        report_path = Path(
            report_path
        )

        report_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        report_path.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return result
