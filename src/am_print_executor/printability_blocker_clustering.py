from __future__ import annotations

import math

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any


_GEOMETRY_BLOCKER_KINDS = {
    "unsupported_extrusion_region",
    "unsupported_layer_island",
    "unsafe_bridge",
}

_SUPPORT_BLOCKER_KINDS = {
    "unanchored_support_toolpath",
}


@dataclass(frozen=True)
class BlockerObservation:
    """One spatial issue emitted by the strict G-code Gate."""

    observation_id: str
    z_mm: float
    kind: str
    area_mm2: float
    xy_bounds_mm: (
        tuple[
            tuple[float, float],
            tuple[float, float],
        ]
        | None
    )
    features: tuple[str, ...] = ()
    span_mm: float | None = None
    anchored_contact_count: int | None = None
    raw_details: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BlockerCluster:
    """A deterministic spatial/vertical defect cluster."""

    cluster_id: str
    classification: str
    recommended_repair_families: tuple[str, ...]
    local_additive_candidate: bool
    observation_ids: tuple[str, ...]
    observation_count: int
    layer_count: int
    z_min_mm: float
    z_max_mm: float
    z_span_mm: float
    xy_bounds_mm: (
        tuple[
            tuple[float, float],
            tuple[float, float],
        ]
        | None
    )
    total_reported_area_mm2: float
    maximum_reported_area_mm2: float
    kinds: tuple[str, ...]
    features: tuple[str, ...]
    dominant_kind: str | None
    dominant_feature: str | None
    persistent_across_layers: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise_name(value: Any) -> str:
    return " ".join(
        str(value)
        .strip()
        .lower()
        .split()
    )


def _normalise_bounds(
    value: Any,
) -> (
    tuple[
        tuple[float, float],
        tuple[float, float],
    ]
    | None
):
    if not isinstance(
        value,
        Sequence,
    ):
        return None

    if len(value) != 2:
        return None

    lower = value[0]
    upper = value[1]

    if not isinstance(
        lower,
        Sequence,
    ):
        return None

    if not isinstance(
        upper,
        Sequence,
    ):
        return None

    if len(lower) < 2:
        return None

    if len(upper) < 2:
        return None

    try:
        x0 = float(lower[0])
        y0 = float(lower[1])
        x1 = float(upper[0])
        y1 = float(upper[1])

    except (TypeError, ValueError):
        return None

    return (
        (
            min(x0, x1),
            min(y0, y1),
        ),
        (
            max(x0, x1),
            max(y0, y1),
        ),
    )


def _bbox_gap_mm(
    left: BlockerObservation,
    right: BlockerObservation,
) -> float:
    if (
        left.xy_bounds_mm is None
        or right.xy_bounds_mm is None
    ):
        return math.inf

    (lx0, ly0), (
        lx1,
        ly1,
    ) = left.xy_bounds_mm

    (rx0, ry0), (
        rx1,
        ry1,
    ) = right.xy_bounds_mm

    dx = max(
        lx0 - rx1,
        rx0 - lx1,
        0.0,
    )

    dy = max(
        ly0 - ry1,
        ry0 - ly1,
        0.0,
    )

    return math.hypot(
        dx,
        dy,
    )


def _issue_family(
    kind: str,
) -> str:
    value = _normalise_name(
        kind
    )

    if value in _GEOMETRY_BLOCKER_KINDS:
        return "geometry"

    if value in _SUPPORT_BLOCKER_KINDS:
        return "support_path"

    return value


def _dominant(
    values: Sequence[str],
) -> str | None:
    filtered = [
        value
        for value in values
        if value
    ]

    if not filtered:
        return None

    counts = Counter(
        filtered
    )

    return sorted(
        counts.items(),
        key=lambda item: (
            -item[1],
            item[0],
        ),
    )[0][0]


def _cluster_classification(
    observations: Sequence[
        BlockerObservation
    ],
) -> tuple[
    str,
    tuple[str, ...],
    bool,
]:
    kinds = {
        item.kind
        for item in observations
    }

    features = {
        feature
        for item in observations
        for feature in item.features
    }

    if kinds == {
        "unanchored_support_toolpath"
    }:
        return (
            "support_path_defect",
            (
                "slicer_support_path_review",
            ),
            False,
        )

    if (
        "unsupported_layer_island"
        in kinds
    ):
        return (
            "detached_growth",
            (
                "connection_blend_analysis",
                "semantic_geometry_regeneration",
            ),
            False,
        )

    if "unsafe_bridge" in kinds:
        return (
            "bridge_span",
            (
                "local_self_support_envelope",
                "slicer_support_review",
            ),
            True,
        )

    local_overhang_features = {
        "floating vertical shell",
        "outer wall",
        "inner wall",
        "internal solid infill",
        "solid infill",
    }

    if (
        kinds.intersection(
            {
                "unsupported_extrusion_region",
            }
        )
        and features.intersection(
            local_overhang_features
        )
    ):
        return (
            "local_overhang",
            (
                "local_self_support_envelope",
                "local_underside_blend",
            ),
            True,
        )

    return (
        "unsupported_geometry",
        (
            "geometry_context_analysis",
        ),
        False,
    )


def extract_blocker_observations(
    gate_report: Mapping[
        str,
        Any,
    ],
) -> list[BlockerObservation]:
    """Flatten spatial Gate issues without changing their semantics."""

    observations: list[
        BlockerObservation
    ] = []

    sequence = 0

    dangerous_layers = (
        gate_report.get(
            "dangerous_layers"
        )
        or []
    )

    for layer in dangerous_layers:
        if not isinstance(
            layer,
            Mapping,
        ):
            continue

        try:
            z_mm = float(
                layer.get(
                    "z_mm",
                    0.0,
                )
            )

        except (TypeError, ValueError):
            continue

        issues = (
            layer.get(
                "issues"
            )
            or []
        )

        for issue in issues:
            if not isinstance(
                issue,
                Mapping,
            ):
                continue

            kind = _normalise_name(
                issue.get(
                    "kind",
                    "unknown",
                )
            )

            try:
                area = float(
                    issue.get(
                        "area_mm2",
                        0.0,
                    )
                    or 0.0
                )

            except (TypeError, ValueError):
                area = 0.0

            features = tuple(
                sorted(
                    {
                        _normalise_name(
                            value
                        )
                        for value
                        in (
                            issue.get(
                                "features"
                            )
                            or []
                        )
                        if _normalise_name(
                            value
                        )
                    }
                )
            )

            span = issue.get(
                "span_mm"
            )

            try:
                span_value = (
                    float(span)
                    if span is not None
                    else None
                )

            except (TypeError, ValueError):
                span_value = None

            contacts = issue.get(
                "anchored_contact_count"
            )

            try:
                contacts_value = (
                    int(contacts)
                    if contacts is not None
                    else None
                )

            except (TypeError, ValueError):
                contacts_value = None

            sequence += 1

            observations.append(
                BlockerObservation(
                    observation_id=(
                        f"obs_{sequence:05d}"
                    ),
                    z_mm=round(
                        z_mm,
                        6,
                    ),
                    kind=kind,
                    area_mm2=area,
                    xy_bounds_mm=(
                        _normalise_bounds(
                            issue.get(
                                "xy_bounds_mm"
                            )
                        )
                    ),
                    features=features,
                    span_mm=span_value,
                    anchored_contact_count=(
                        contacts_value
                    ),
                    raw_details=dict(
                        issue
                    ),
                )
            )

    return observations


def _union_bounds(
    observations: Sequence[
        BlockerObservation
    ],
) -> (
    tuple[
        tuple[float, float],
        tuple[float, float],
    ]
    | None
):
    bounds = [
        item.xy_bounds_mm
        for item in observations
        if item.xy_bounds_mm
        is not None
    ]

    if not bounds:
        return None

    return (
        (
            min(
                item[0][0]
                for item in bounds
            ),
            min(
                item[0][1]
                for item in bounds
            ),
        ),
        (
            max(
                item[1][0]
                for item in bounds
            ),
            max(
                item[1][1]
                for item in bounds
            ),
        ),
    )


def cluster_blocker_observations(
    observations: Sequence[
        BlockerObservation
    ],
    *,
    xy_link_mm: float,
    z_link_mm: float,
) -> list[BlockerCluster]:
    """Link nearby issues into deterministic 3D physical defect clusters."""

    records = list(
        observations
    )

    if not records:
        return []

    if xy_link_mm < 0.0:
        raise ValueError(
            "xy_link_mm must be non-negative"
        )

    if z_link_mm < 0.0:
        raise ValueError(
            "z_link_mm must be non-negative"
        )

    parent = list(
        range(
            len(records)
        )
    )

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[
                parent[index]
            ]
            index = parent[index]

        return index

    def union(
        left: int,
        right: int,
    ) -> None:
        left_root = find(
            left
        )

        right_root = find(
            right
        )

        if left_root == right_root:
            return

        if left_root < right_root:
            parent[
                right_root
            ] = left_root

        if right_root < left_root:
            parent[
                left_root
            ] = right_root

    for left_index in range(
        len(records)
    ):
        left = records[
            left_index
        ]

        for right_index in range(
            left_index + 1,
            len(records),
        ):
            right = records[
                right_index
            ]

            if (
                _issue_family(
                    left.kind
                )
                != _issue_family(
                    right.kind
                )
            ):
                continue

            z_gap = abs(
                left.z_mm
                - right.z_mm
            )

            if (
                z_gap
                > z_link_mm
                + 1e-9
            ):
                continue

            if (
                _bbox_gap_mm(
                    left,
                    right,
                )
                > xy_link_mm
                + 1e-9
            ):
                continue

            union(
                left_index,
                right_index,
            )

    grouped: dict[
        int,
        list[BlockerObservation],
    ] = {}

    for index, record in enumerate(
        records
    ):
        root = find(
            index
        )

        grouped.setdefault(
            root,
            [],
        ).append(
            record
        )

    ordered_groups = sorted(
        grouped.values(),
        key=lambda group: (
            min(
                item.z_mm
                for item in group
            ),
            min(
                item.observation_id
                for item in group
            ),
        ),
    )

    clusters: list[
        BlockerCluster
    ] = []

    for cluster_index, group in enumerate(
        ordered_groups,
        start=1,
    ):
        z_values = sorted(
            {
                item.z_mm
                for item in group
            }
        )

        kinds = tuple(
            sorted(
                {
                    item.kind
                    for item in group
                }
            )
        )

        features = tuple(
            sorted(
                {
                    feature
                    for item in group
                    for feature
                    in item.features
                }
            )
        )

        classification, repair_families, local_candidate = (
            _cluster_classification(
                group
            )
        )

        cluster = BlockerCluster(
            cluster_id=(
                f"cluster_{cluster_index:03d}"
            ),
            classification=classification,
            recommended_repair_families=(
                repair_families
            ),
            local_additive_candidate=(
                local_candidate
            ),
            observation_ids=tuple(
                sorted(
                    item.observation_id
                    for item in group
                )
            ),
            observation_count=len(
                group
            ),
            layer_count=len(
                z_values
            ),
            z_min_mm=min(
                z_values
            ),
            z_max_mm=max(
                z_values
            ),
            z_span_mm=(
                max(z_values)
                - min(z_values)
            ),
            xy_bounds_mm=(
                _union_bounds(
                    group
                )
            ),
            total_reported_area_mm2=sum(
                item.area_mm2
                for item in group
            ),
            maximum_reported_area_mm2=max(
                (
                    item.area_mm2
                    for item in group
                ),
                default=0.0,
            ),
            kinds=kinds,
            features=features,
            dominant_kind=_dominant(
                [
                    item.kind
                    for item in group
                ]
            ),
            dominant_feature=_dominant(
                [
                    feature
                    for item in group
                    for feature
                    in item.features
                ]
            ),
            persistent_across_layers=(
                len(
                    z_values
                )
                >= 2
            ),
        )

        clusters.append(
            cluster
        )

    return clusters


def cluster_gate_blockers(
    gate_report: Mapping[
        str,
        Any,
    ],
    *,
    xy_link_mm: float | None = None,
    z_link_mm: float | None = None,
) -> list[BlockerCluster]:
    """Convert one strict Gate report into 3D defect clusters."""

    policy = (
        gate_report.get(
            "policy"
        )
        or {}
    )

    try:
        cell_mm = float(
            policy.get(
                "cell_mm",
                0.40,
            )
        )

    except (TypeError, ValueError):
        cell_mm = 0.40

    try:
        line_width_mm = float(
            policy.get(
                "line_width_mm",
                0.42,
            )
        )

    except (TypeError, ValueError):
        line_width_mm = 0.42

    try:
        layer_height_mm = float(
            policy.get(
                "measured_layer_height_mm",
                policy.get(
                    "configured_layer_height_mm",
                    0.20,
                ),
            )
        )

    except (TypeError, ValueError):
        layer_height_mm = 0.20

    if xy_link_mm is None:
        xy_link_mm = max(
            cell_mm,
            line_width_mm,
        )

    if z_link_mm is None:
        # Allows adjacent normal layers and a declared 0.4 mm
        # variable-height step without joining remote defects.
        z_link_mm = max(
            layer_height_mm * 2.10,
            0.25,
        )

    observations = (
        extract_blocker_observations(
            gate_report
        )
    )

    return (
        cluster_blocker_observations(
            observations,
            xy_link_mm=float(
                xy_link_mm
            ),
            z_link_mm=float(
                z_link_mm
            ),
        )
    )
