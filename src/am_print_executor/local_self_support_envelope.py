from __future__ import annotations

import math

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from am_print_executor.general_printability_geometry_repair import (
    PrintabilityRepairRequest,
)
from am_print_executor.printability_blocker_clustering import (
    BlockerCluster,
)


@dataclass(frozen=True)
class EnvelopeStrength:
    name: str
    top_margin_mm: float
    maximum_anchor_depth_mm: float
    slope_safety_factor: float
    anchor_embed_mm: float


@dataclass(frozen=True)
class EnvelopeCandidatePlan:
    schema_version: str
    cluster_id: str
    cluster_classification: str
    strength: str

    status: str
    rejection_reason: str | None

    top_center_xy_mm: tuple[float, float] | None
    top_size_xy_mm: tuple[float, float] | None
    top_z_mm: float | None

    anchor_center_xy_mm: tuple[float, float] | None
    anchor_section_z_mm: float | None
    bottom_z_mm: float | None

    vertical_depth_mm: float | None
    lateral_center_shift_mm: float | None
    maximum_allowed_shift_mm: float | None
    slope_ratio_xy_per_z: float | None

    estimated_repair_volume_mm3: float
    estimated_volume_ratio: float

    protected_base_min_z_mm: float
    protected_base_clearance_z_mm: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_STRENGTHS = (
    EnvelopeStrength(
        name="minimal",
        top_margin_mm=0.10,
        maximum_anchor_depth_mm=2.0,
        slope_safety_factor=0.80,
        anchor_embed_mm=0.10,
    ),
    EnvelopeStrength(
        name="balanced",
        top_margin_mm=0.20,
        maximum_anchor_depth_mm=3.0,
        slope_safety_factor=0.90,
        anchor_embed_mm=0.12,
    ),
    EnvelopeStrength(
        name="strong",
        top_margin_mm=0.30,
        maximum_anchor_depth_mm=4.0,
        slope_safety_factor=1.00,
        anchor_embed_mm=0.15,
    ),
)


def _load_mesh(
    path: Path,
) -> trimesh.Trimesh:
    loaded = trimesh.load(
        Path(path),
        force="mesh",
        process=False,
    )

    if isinstance(
        loaded,
        trimesh.Scene,
    ):
        meshes = [
            item
            for item
            in loaded.geometry.values()
            if (
                isinstance(
                    item,
                    trimesh.Trimesh,
                )
                and len(item.faces)
            )
        ]

        if not meshes:
            raise ValueError(
                "source geometry contains no triangle mesh"
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
        raise ValueError(
            "source geometry is not a triangle mesh"
        )

    return loaded


def _safe_float(
    value: Any,
    default: float,
) -> float:
    try:
        result = float(
            value
        )

    except (TypeError, ValueError):
        return float(
            default
        )

    if not math.isfinite(
        result
    ):
        return float(
            default
        )

    return result


def _gate_dimensions(
    request: PrintabilityRepairRequest,
) -> tuple[
    float,
    float,
    float,
]:
    policy = (
        request.gate_policy
        or {}
    )

    line_width = _safe_float(
        policy.get(
            "line_width_mm"
        ),
        0.42,
    )

    layer_height = _safe_float(
        policy.get(
            "measured_layer_height_mm",
            policy.get(
                "configured_layer_height_mm",
                0.20,
            ),
        ),
        0.20,
    )

    if line_width <= 0.0:
        line_width = 0.42

    if layer_height <= 0.0:
        layer_height = 0.20

    self_support_xy = _safe_float(
        policy.get(
            "self_support_xy_mm"
        ),
        max(
            line_width * 0.65,
            layer_height * 1.10,
        ),
    )

    if self_support_xy <= 0.0:
        self_support_xy = max(
            line_width * 0.65,
            layer_height * 1.10,
        )

    return (
        line_width,
        layer_height,
        self_support_xy,
    )


def _mesh_reference_volume(
    mesh: trimesh.Trimesh,
) -> float:
    volume = abs(
        float(
            mesh.volume
        )
    )

    if (
        math.isfinite(
            volume
        )
        and volume > 1e-9
    ):
        return volume

    extents = np.asarray(
        mesh.extents,
        dtype=float,
    )

    bbox_volume = float(
        np.prod(
            np.maximum(
                extents,
                0.0,
            )
        )
    )

    return max(
        bbox_volume,
        1e-9,
    )


def _point_segment_nearest(
    point: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
) -> tuple[
    float,
    np.ndarray,
]:
    vector = (
        b - a
    )

    denominator = float(
        np.dot(
            vector,
            vector,
        )
    )

    if denominator <= 1e-18:
        nearest = a

    if denominator > 1e-18:
        ratio = float(
            np.dot(
                point - a,
                vector,
            )
            / denominator
        )

        ratio = max(
            0.0,
            min(
                1.0,
                ratio,
            ),
        )

        nearest = (
            a
            + vector * ratio
        )

    distance = float(
        np.linalg.norm(
            point - nearest
        )
    )

    return (
        distance,
        nearest,
    )


def _nearest_section_point(
    section_segments: np.ndarray,
    target_xy: tuple[
        float,
        float,
    ],
) -> tuple[
    float,
    tuple[float, float] | None,
]:
    if section_segments is None:
        return (
            math.inf,
            None,
        )

    segments = np.asarray(
        section_segments,
        dtype=float,
    )

    if segments.size == 0:
        return (
            math.inf,
            None,
        )

    point = np.asarray(
        target_xy,
        dtype=float,
    )

    best_distance = math.inf
    best_point = None

    for segment in segments:
        a = np.asarray(
            segment[0][:2],
            dtype=float,
        )

        b = np.asarray(
            segment[1][:2],
            dtype=float,
        )

        distance, nearest = (
            _point_segment_nearest(
                point,
                a,
                b,
            )
        )

        if distance < best_distance:
            best_distance = distance

            best_point = (
                float(
                    nearest[0]
                ),
                float(
                    nearest[1]
                ),
            )

    return (
        best_distance,
        best_point,
    )


def _section_at_z(
    mesh: trimesh.Trimesh,
    z_mm: float,
    cache: dict[
        float,
        np.ndarray,
    ],
) -> np.ndarray:
    key = round(
        float(z_mm),
        6,
    )

    if key in cache:
        return cache[
            key
        ]

    try:
        result = (
            trimesh.intersections.mesh_plane(
                mesh,
                plane_normal=np.asarray(
                    [0.0, 0.0, 1.0],
                    dtype=float,
                ),
                plane_origin=np.asarray(
                    [0.0, 0.0, key],
                    dtype=float,
                ),
            )
        )

        result = np.asarray(
            result,
            dtype=float,
        )

    except Exception:
        result = np.empty(
            (
                0,
                2,
                3,
            ),
            dtype=float,
        )

    cache[
        key
    ] = result

    return result


def _cluster_top_geometry(
    cluster: BlockerCluster,
    *,
    margin_mm: float,
) -> tuple[
    tuple[float, float],
    tuple[float, float],
    float,
] | None:
    bounds = (
        cluster.xy_bounds_mm
    )

    if bounds is None:
        return None

    (
        x0,
        y0,
    ), (
        x1,
        y1,
    ) = bounds

    center = (
        (
            float(x0)
            + float(x1)
        )
        * 0.5,
        (
            float(y0)
            + float(y1)
        )
        * 0.5,
    )

    size = (
        max(
            float(x1)
            - float(x0)
            + 2.0 * margin_mm,
            0.10,
        ),
        max(
            float(y1)
            - float(y0)
            + 2.0 * margin_mm,
            0.10,
        ),
    )

    return (
        center,
        size,
        float(
            cluster.z_min_mm
        ),
    )


def _protected_base_clearance(
    mesh: trimesh.Trimesh,
    *,
    layer_height_mm: float,
) -> tuple[
    float,
    float,
]:
    minimum_z = float(
        mesh.bounds[
            0,
            2,
        ]
    )

    clearance = max(
        layer_height_mm * 3.0,
        0.60,
    )

    return (
        minimum_z,
        minimum_z + clearance,
    )


def _make_plan(
    *,
    mesh: trimesh.Trimesh,
    request: PrintabilityRepairRequest,
    cluster: BlockerCluster,
    strength: EnvelopeStrength,
    section_cache: dict[
        float,
        np.ndarray,
    ],
) -> EnvelopeCandidatePlan:

    (
        line_width,
        layer_height,
        self_support_xy,
    ) = _gate_dimensions(
        request
    )

    minimum_z, protected_z = (
        _protected_base_clearance(
            mesh,
            layer_height_mm=layer_height,
        )
    )

    top = _cluster_top_geometry(
        cluster,
        margin_mm=(
            strength.top_margin_mm
        ),
    )

    if top is None:
        return EnvelopeCandidatePlan(
            schema_version="gpr-v1.2",
            cluster_id=cluster.cluster_id,
            cluster_classification=(
                cluster.classification
            ),
            strength=strength.name,
            status="rejected",
            rejection_reason="cluster_has_no_xy_bounds",
            top_center_xy_mm=None,
            top_size_xy_mm=None,
            top_z_mm=None,
            anchor_center_xy_mm=None,
            anchor_section_z_mm=None,
            bottom_z_mm=None,
            vertical_depth_mm=None,
            lateral_center_shift_mm=None,
            maximum_allowed_shift_mm=None,
            slope_ratio_xy_per_z=None,
            estimated_repair_volume_mm3=0.0,
            estimated_volume_ratio=0.0,
            protected_base_min_z_mm=minimum_z,
            protected_base_clearance_z_mm=protected_z,
        )

    if not cluster.local_additive_candidate:
        return EnvelopeCandidatePlan(
            schema_version="gpr-v1.2",
            cluster_id=cluster.cluster_id,
            cluster_classification=(
                cluster.classification
            ),
            strength=strength.name,
            status="rejected",
            rejection_reason=(
                "cluster_not_local_additive_candidate"
            ),
            top_center_xy_mm=top[0],
            top_size_xy_mm=top[1],
            top_z_mm=top[2],
            anchor_center_xy_mm=None,
            anchor_section_z_mm=None,
            bottom_z_mm=None,
            vertical_depth_mm=None,
            lateral_center_shift_mm=None,
            maximum_allowed_shift_mm=None,
            slope_ratio_xy_per_z=None,
            estimated_repair_volume_mm3=0.0,
            estimated_volume_ratio=0.0,
            protected_base_min_z_mm=minimum_z,
            protected_base_clearance_z_mm=protected_z,
        )

    top_center, top_size, top_z = top

    if (
        top_z
        <= protected_z
        + layer_height
    ):
        return EnvelopeCandidatePlan(
            schema_version="gpr-v1.2",
            cluster_id=cluster.cluster_id,
            cluster_classification=(
                cluster.classification
            ),
            strength=strength.name,
            status="rejected",
            rejection_reason="too_close_to_protected_base",
            top_center_xy_mm=top_center,
            top_size_xy_mm=top_size,
            top_z_mm=top_z,
            anchor_center_xy_mm=None,
            anchor_section_z_mm=None,
            bottom_z_mm=None,
            vertical_depth_mm=None,
            lateral_center_shift_mm=None,
            maximum_allowed_shift_mm=None,
            slope_ratio_xy_per_z=None,
            estimated_repair_volume_mm3=0.0,
            estimated_volume_ratio=0.0,
            protected_base_min_z_mm=minimum_z,
            protected_base_clearance_z_mm=protected_z,
        )

    slope_ratio = (
        self_support_xy
        / layer_height
        * strength.slope_safety_factor
    )

    slope_ratio = max(
        slope_ratio,
        0.10,
    )

    steps = max(
        1,
        int(
            math.ceil(
                strength.maximum_anchor_depth_mm
                / layer_height
            )
        ),
    )

    anchor = None

    for index in range(
        1,
        steps + 1,
    ):
        anchor_z = (
            top_z
            - layer_height
            * index
        )

        if (
            anchor_z
            <= protected_z
        ):
            break

        section = _section_at_z(
            mesh,
            anchor_z,
            section_cache,
        )

        distance, point = (
            _nearest_section_point(
                section,
                top_center,
            )
        )

        if point is None:
            continue

        vertical_depth = (
            top_z
            - anchor_z
        )

        maximum_shift = (
            slope_ratio
            * vertical_depth
        )

        # Half a current bead is permitted because the repair envelope
        # and the source shell will later be boolean-unioned with
        # deliberate overlap.
        allowed_with_bead = (
            maximum_shift
            + line_width * 0.50
        )

        if (
            distance
            <= allowed_with_bead
            + 1e-9
        ):
            anchor = (
                point,
                anchor_z,
                distance,
                vertical_depth,
                maximum_shift,
            )
            break

    if anchor is None:
        return EnvelopeCandidatePlan(
            schema_version="gpr-v1.2",
            cluster_id=cluster.cluster_id,
            cluster_classification=(
                cluster.classification
            ),
            strength=strength.name,
            status="rejected",
            rejection_reason=(
                "no_local_source_anchor_within_depth_and_slope"
            ),
            top_center_xy_mm=top_center,
            top_size_xy_mm=top_size,
            top_z_mm=top_z,
            anchor_center_xy_mm=None,
            anchor_section_z_mm=None,
            bottom_z_mm=None,
            vertical_depth_mm=None,
            lateral_center_shift_mm=None,
            maximum_allowed_shift_mm=None,
            slope_ratio_xy_per_z=slope_ratio,
            estimated_repair_volume_mm3=0.0,
            estimated_volume_ratio=0.0,
            protected_base_min_z_mm=minimum_z,
            protected_base_clearance_z_mm=protected_z,
        )

    (
        anchor_center,
        anchor_z,
        center_shift,
        vertical_depth,
        maximum_shift,
    ) = anchor

    bottom_z = max(
        protected_z,
        anchor_z
        - strength.anchor_embed_mm,
    )

    actual_depth = (
        top_z
        - bottom_z
    )

    area = (
        top_size[0]
        * top_size[1]
    )

    estimated_volume = (
        area
        * actual_depth
    )

    reference_volume = (
        _mesh_reference_volume(
            mesh
        )
    )

    volume_ratio = (
        estimated_volume
        / reference_volume
    )

    maximum_ratio = float(
        request.repair_budget
        .maximum_modified_volume_ratio
    )

    if (
        volume_ratio
        > maximum_ratio
        + 1e-12
    ):
        return EnvelopeCandidatePlan(
            schema_version="gpr-v1.2",
            cluster_id=cluster.cluster_id,
            cluster_classification=(
                cluster.classification
            ),
            strength=strength.name,
            status="rejected",
            rejection_reason="repair_volume_budget_exceeded",
            top_center_xy_mm=top_center,
            top_size_xy_mm=top_size,
            top_z_mm=top_z,
            anchor_center_xy_mm=anchor_center,
            anchor_section_z_mm=anchor_z,
            bottom_z_mm=bottom_z,
            vertical_depth_mm=actual_depth,
            lateral_center_shift_mm=center_shift,
            maximum_allowed_shift_mm=maximum_shift,
            slope_ratio_xy_per_z=slope_ratio,
            estimated_repair_volume_mm3=estimated_volume,
            estimated_volume_ratio=volume_ratio,
            protected_base_min_z_mm=minimum_z,
            protected_base_clearance_z_mm=protected_z,
        )

    return EnvelopeCandidatePlan(
        schema_version="gpr-v1.2",
        cluster_id=cluster.cluster_id,
        cluster_classification=(
            cluster.classification
        ),
        strength=strength.name,
        status="candidate",
        rejection_reason=None,
        top_center_xy_mm=top_center,
        top_size_xy_mm=top_size,
        top_z_mm=top_z,
        anchor_center_xy_mm=anchor_center,
        anchor_section_z_mm=anchor_z,
        bottom_z_mm=bottom_z,
        vertical_depth_mm=actual_depth,
        lateral_center_shift_mm=center_shift,
        maximum_allowed_shift_mm=maximum_shift,
        slope_ratio_xy_per_z=slope_ratio,
        estimated_repair_volume_mm3=estimated_volume,
        estimated_volume_ratio=volume_ratio,
        protected_base_min_z_mm=minimum_z,
        protected_base_clearance_z_mm=protected_z,
    )


def _cluster_rank(
    cluster: BlockerCluster,
) -> tuple[Any, ...]:
    return (
        0 if cluster.persistent_across_layers else 1,
        -cluster.layer_count,
        -cluster.total_reported_area_mm2,
        -cluster.maximum_reported_area_mm2,
        cluster.z_min_mm,
        cluster.cluster_id,
    )


def plan_local_self_support_envelopes(
    request: PrintabilityRepairRequest,
    *,
    strengths: tuple[
        EnvelopeStrength,
        ...
    ] = DEFAULT_STRENGTHS,
    maximum_clusters: int | None = None,
) -> list[EnvelopeCandidatePlan]:
    """Plan deterministic additive envelope candidates.

    This function NEVER modifies or writes the source geometry.
    """

    mesh = _load_mesh(
        Path(
            request.source_geometry
        )
    )

    clusters = sorted(
        request.blocker_clusters,
        key=_cluster_rank,
    )

    if maximum_clusters is not None:
        clusters = clusters[
            :max(
                0,
                int(
                    maximum_clusters
                ),
            )
        ]

    cache: dict[
        float,
        np.ndarray,
    ] = {}

    plans: list[
        EnvelopeCandidatePlan
    ] = []

    for cluster in clusters:
        for strength in strengths:
            plans.append(
                _make_plan(
                    mesh=mesh,
                    request=request,
                    cluster=cluster,
                    strength=strength,
                    section_cache=cache,
                )
            )

    return plans


def build_envelope_mesh(
    plan: EnvelopeCandidatePlan,
) -> trimesh.Trimesh:
    """Construct one watertight oblique prism repair volume."""

    if plan.status != "candidate":
        raise ValueError(
            "Only accepted candidate plans can build geometry."
        )

    if (
        plan.top_center_xy_mm is None
        or plan.top_size_xy_mm is None
        or plan.top_z_mm is None
        or plan.anchor_center_xy_mm is None
        or plan.bottom_z_mm is None
    ):
        raise ValueError(
            "Candidate plan is incomplete."
        )

    top_cx, top_cy = (
        plan.top_center_xy_mm
    )

    bottom_cx, bottom_cy = (
        plan.anchor_center_xy_mm
    )

    size_x, size_y = (
        plan.top_size_xy_mm
    )

    hx = (
        size_x
        * 0.5
    )

    hy = (
        size_y
        * 0.5
    )

    z0 = float(
        plan.bottom_z_mm
    )

    z1 = float(
        plan.top_z_mm
    )

    vertices = np.asarray(
        [
            [
                bottom_cx - hx,
                bottom_cy - hy,
                z0,
            ],
            [
                bottom_cx + hx,
                bottom_cy - hy,
                z0,
            ],
            [
                bottom_cx + hx,
                bottom_cy + hy,
                z0,
            ],
            [
                bottom_cx - hx,
                bottom_cy + hy,
                z0,
            ],
            [
                top_cx - hx,
                top_cy - hy,
                z1,
            ],
            [
                top_cx + hx,
                top_cy - hy,
                z1,
            ],
            [
                top_cx + hx,
                top_cy + hy,
                z1,
            ],
            [
                top_cx - hx,
                top_cy + hy,
                z1,
            ],
        ],
        dtype=float,
    )

    faces = np.asarray(
        [
            [0, 2, 1],
            [0, 3, 2],

            [4, 5, 6],
            [4, 6, 7],

            [0, 1, 5],
            [0, 5, 4],

            [1, 2, 6],
            [1, 6, 5],

            [2, 3, 7],
            [2, 7, 6],

            [3, 0, 4],
            [3, 4, 7],
        ],
        dtype=np.int64,
    )

    mesh = trimesh.Trimesh(
        vertices=vertices,
        faces=faces,
        process=True,
        validate=True,
    )

    if not bool(
        mesh.is_watertight
    ):
        raise ValueError(
            "Generated envelope is not watertight."
        )

    if float(
        mesh.volume
    ) < 0.0:
        mesh.invert()

    return mesh
