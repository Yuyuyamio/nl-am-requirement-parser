from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math

from pathlib import Path
from typing import Any

import numpy as np
import trimesh


class PrintableOrientationError(RuntimeError):
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
    loaded = trimesh.load(
        path,
        force="mesh",
        process=False,
    )

    if isinstance(loaded, trimesh.Scene):
        meshes = [
            geometry
            for geometry in loaded.geometry.values()
            if isinstance(geometry, trimesh.Trimesh)
        ]

        if not meshes:
            raise PrintableOrientationError(
                "Input contains no mesh."
            )

        loaded = trimesh.util.concatenate(meshes)

    if not isinstance(loaded, trimesh.Trimesh):
        raise PrintableOrientationError(
            "Input is not a triangle mesh."
        )

    if len(loaded.faces) == 0:
        raise PrintableOrientationError(
            "Input mesh has no triangles."
        )

    return loaded


def _axis_aligned_rotations() -> list[np.ndarray]:
    """
    All 24 right-handed axis-aligned rotations.
    These are fallback candidates only.
    """
    transforms: list[np.ndarray] = []

    for perm in itertools.permutations(range(3)):
        for signs in itertools.product(
            (-1.0, 1.0),
            repeat=3,
        ):
            r = np.zeros((3, 3), dtype=float)

            for row, col in enumerate(perm):
                r[row, col] = signs[row]

            if np.linalg.det(r) < 0.999:
                continue

            t = np.eye(4)
            t[:3, :3] = r

            transforms.append(t)

    return transforms


def _candidate_transforms(
    mesh: trimesh.Trimesh,
) -> list[tuple[np.ndarray, float, str]]:
    results: list[
        tuple[np.ndarray, float, str]
    ] = []

    # Physical stable-pose calculation is the primary source.
    try:
        hull = mesh.convex_hull

        transforms, probabilities = (
            trimesh.poses.compute_stable_poses(
                hull,
                center_mass=np.asarray(
                    mesh.center_mass,
                    dtype=float,
                ),
                sigma=0.0,
                n_samples=1,
                threshold=0.0,
            )
        )

        for transform, probability in zip(
            transforms,
            probabilities,
        ):
            results.append(
                (
                    np.asarray(
                        transform,
                        dtype=float,
                    ),
                    float(probability),
                    "stable_pose",
                )
            )

    except Exception:
        # The fallback below is intentionally retained.
        pass

    for transform in _axis_aligned_rotations():
        results.append(
            (
                transform,
                0.0,
                "axis_fallback",
            )
        )

    # Keep only unique rotation matrices.
    unique: list[
        tuple[np.ndarray, float, str]
    ] = []

    seen: set[tuple[float, ...]] = set()

    for transform, probability, source in results:
        key = tuple(
            np.round(
                transform[:3, :3],
                5,
            ).reshape(-1)
        )

        if key in seen:
            continue

        seen.add(key)

        unique.append(
            (
                transform,
                probability,
                source,
            )
        )

    if not unique:
        raise PrintableOrientationError(
            "No orientation candidates generated."
        )

    return unique


def _place_on_bed(
    mesh: trimesh.Trimesh,
    *,
    bed_x_mm: float,
    bed_y_mm: float,
) -> None:
    """
    Put an already-oriented model onto the physical bed.

    Coordinate policy:
      Z minimum -> 0
      XY center -> physical bed center

    This is deliberately NOT centered around local (0, 0),
    because Bambu printer profiles use positive build-area
    coordinates. With Bambu arrange disabled, negative XY
    coordinates can cause CLI_NO_SUITABLE_OBJECTS (-50).
    """
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

    object_center_x = (
        float(bounds[0, 0])
        + float(bounds[1, 0])
    ) / 2.0

    object_center_y = (
        float(bounds[0, 1])
        + float(bounds[1, 1])
    ) / 2.0

    bed_center_x = (
        float(bed_x_mm) / 2.0
    )

    bed_center_y = (
        float(bed_y_mm) / 2.0
    )

    mesh.apply_translation(
        [
            bed_center_x
            - object_center_x,

            bed_center_y
            - object_center_y,

            0.0,
        ]
    )


def _evaluate(
    original: trimesh.Trimesh,
    transform: np.ndarray,
    *,
    probability: float,
    source: str,
    bed_x_mm: float,
    bed_y_mm: float,
    bed_margin_mm: float,
    contact_tolerance_mm: float,
    overhang_angle_deg: float,
) -> tuple[dict[str, Any], trimesh.Trimesh]:

    candidate = original.copy()

    candidate.apply_transform(
        transform
    )

    _place_on_bed(
        candidate,
        bed_x_mm=bed_x_mm,
        bed_y_mm=bed_y_mm,
    )

    bounds = np.asarray(
        candidate.bounds,
        dtype=float,
    )

    extents = np.asarray(
        candidate.extents,
        dtype=float,
    )

    triangles = np.asarray(
        candidate.triangles,
        dtype=float,
    )

    face_areas = np.asarray(
        candidate.area_faces,
        dtype=float,
    )

    normals = np.asarray(
        candidate.face_normals,
        dtype=float,
    )

    face_max_z = triangles[:, :, 2].max(
        axis=1
    )

    contact_mask = (
        face_max_z
        <= contact_tolerance_mm
    )

    contact_area = float(
        face_areas[contact_mask].sum()
    )

    surface_area = float(
        candidate.area
    )

    # Downward-facing geometry beyond the configured
    # self-support angle is considered support-demanding.
    threshold = -math.cos(
        math.radians(
            overhang_angle_deg
        )
    )

    overhang_mask = (
        normals[:, 2] < threshold
    )

    overhang_area = float(
        face_areas[overhang_mask].sum()
    )

    minimum_contact_area = max(
        2.0,
        surface_area * 0.0005,
    )

    bed_min_x = float(
        bed_margin_mm
    )

    bed_min_y = float(
        bed_margin_mm
    )

    bed_max_x = float(
        bed_x_mm - bed_margin_mm
    )

    bed_max_y = float(
        bed_y_mm - bed_margin_mm
    )

    # IMPORTANT:
    # Validate absolute coordinates, not merely extents.
    # A 30 mm object located at X=-15 is still outside
    # a 0..256 Bambu build area even though its width fits.
    bed_fit = bool(
        float(bounds[0, 0]) >= bed_min_x
        and float(bounds[1, 0]) <= bed_max_x
        and float(bounds[0, 1]) >= bed_min_y
        and float(bounds[1, 1]) <= bed_max_y
    )

    contact_pass = bool(
        contact_area
        >= minimum_contact_area
    )

    support_threshold = max(
        2.0,
        surface_area * 0.002,
    )

    support_required = bool(
        overhang_area
        >= support_threshold
    )

    max_extent = max(
        float(np.max(original.extents)),
        1e-9,
    )

    height_norm = (
        float(extents[2])
        / max_extent
    )

    contact_strength = min(
        contact_area
        / max(
            minimum_contact_area,
            1e-9,
        ),
        20.0,
    ) / 20.0

    overhang_ratio = (
        overhang_area
        / max(surface_area, 1e-9)
    )

    # Stable pose probability is useful but NOT authoritative.
    # Geometry/contact/overhang still determine acceptance.
    score = (
        3.0 * float(probability)
        + 2.0 * contact_strength
        - 1.5 * overhang_ratio
        - 0.5 * height_norm
    )

    blockers: list[str] = []

    if not bed_fit:
        blockers.append(
            "xy_footprint_exceeds_bed"
        )

    if not contact_pass:
        blockers.append(
            "insufficient_bed_contact"
        )

    record = {
        "source": source,
        "stable_probability":
            float(probability),
        "score": float(score),
        "bounds_mm":
            bounds.tolist(),
        "extents_mm":
            extents.tolist(),
        "z_min_mm":
            float(bounds[0, 2]),
        "bed_contact_area_mm2":
            contact_area,
        "minimum_contact_area_mm2":
            minimum_contact_area,
        "surface_area_mm2":
            surface_area,
        "overhang_area_mm2":
            overhang_area,
        "support_required":
            support_required,
        "bed_fit":
            bed_fit,
        "contact_pass":
            contact_pass,
        "blockers":
            blockers,
        "transform":
            np.asarray(
                transform,
                dtype=float,
            ).tolist(),
    }

    return record, candidate


def orient_for_printing(
    *,
    input_path: Path,
    output_path: Path,
    report_path: Path,
    bed_x_mm: float = 256.0,
    bed_y_mm: float = 256.0,
    bed_margin_mm: float = 5.0,
    contact_tolerance_mm: float = 0.25,
    overhang_angle_deg: float = 45.0,
) -> dict[str, Any]:

    input_path = Path(
        input_path
    ).resolve()

    output_path = Path(
        output_path
    ).resolve()

    report_path = Path(
        report_path
    ).resolve()

    original = _load_mesh(
        input_path
    )

    original_bounds = np.asarray(
        original.bounds,
        dtype=float,
    )

    original_extents = np.asarray(
        original.extents,
        dtype=float,
    )

    candidates = []

    for transform, probability, source in (
        _candidate_transforms(original)
    ):
        record, candidate = _evaluate(
            original,
            transform,
            probability=probability,
            source=source,
            bed_x_mm=bed_x_mm,
            bed_y_mm=bed_y_mm,
            bed_margin_mm=bed_margin_mm,
            contact_tolerance_mm=
                contact_tolerance_mm,
            overhang_angle_deg=
                overhang_angle_deg,
        )

        candidates.append(
            (
                record,
                candidate,
            )
        )

    valid = [
        item
        for item in candidates
        if not item[0]["blockers"]
    ]

    if not valid:
        report = {
            "status":
                "printable_orientation_blocked",
            "input":
                str(input_path),
            "blockers": [
                "no_valid_orientation"
            ],
            "candidate_count":
                len(candidates),
        }

        report_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        report_path.write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        raise PrintableOrientationError(
            "No printable orientation passed "
            "bed/contact constraints."
        )

    valid.sort(
        key=lambda item:
            item[0]["score"],
        reverse=True,
    )

    selected_record, selected_mesh = (
        valid[0]
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    selected_mesh.export(
        output_path
    )

    selected_rotation = np.asarray(
        selected_record["transform"],
        dtype=float,
    )[:3, :3]

    rotation_delta = float(
        np.linalg.norm(
            selected_rotation
            - np.eye(3)
        )
    )

    report = {
        "status":
            "printable_orientation_pass",
        "input_path":
            str(input_path),
        "input_sha256":
            _sha256(input_path),
        "output_path":
            str(output_path),
        "output_sha256":
            _sha256(output_path),
        "orientation_changed":
            bool(rotation_delta > 1e-6),
        "rotation_delta":
            rotation_delta,
        "original_bounds_mm":
            original_bounds.tolist(),
        "original_extents_mm":
            original_extents.tolist(),
        "selected":
            selected_record,
        "candidate_count":
            len(candidates),
        "valid_candidate_count":
            len(valid),
        "top_candidates": [
            record
            for record, _mesh in valid[:10]
        ],
        "policy": {
            "bed_x_mm":
                bed_x_mm,
            "bed_y_mm":
                bed_y_mm,
            "bed_margin_mm":
                bed_margin_mm,
            "contact_tolerance_mm":
                contact_tolerance_mm,
            "overhang_angle_deg":
                overhang_angle_deg,
            "bambu_auto_orient_authoritative":
                False,
        },
    }

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return report


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--report",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    result = orient_for_printing(
        input_path=args.input,
        output_path=args.output,
        report_path=args.report,
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
