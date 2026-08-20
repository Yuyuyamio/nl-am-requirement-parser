from __future__ import annotations

from collections.abc import Sequence


class AmsMappingError(ValueError):
    pass


# X1/P1/A1 project-file wire contract used by the current
# Developer Mode backend. This is deliberately device-layer
# knowledge and must not leak into model/project manifests.
X1C_PROJECT_MAPPING_CAPACITY = 5

MIN_PHYSICAL_SLOT = 0
MAX_PHYSICAL_SLOT = 255


def validate_logical_mapping(
    mapping: Sequence[int],
    *,
    expected_count: int | None = None,
    capacity: int = X1C_PROJECT_MAPPING_CAPACITY,
    require_distinct: bool = False,
) -> list[int]:
    values = list(mapping)

    if not values:
        raise AmsMappingError(
            "AMS logical mapping cannot be empty."
        )

    if capacity < 1:
        raise AmsMappingError(
            "AMS mapping capacity must be positive."
        )

    for value in values:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
        ):
            raise AmsMappingError(
                "AMS logical mapping must contain integers."
            )

        if value < MIN_PHYSICAL_SLOT:
            raise AmsMappingError(
                "AMS logical mapping must not contain -1 "
                "or other negative padding values. "
                "Wire padding is generated only by the "
                "device backend."
            )

        if value > MAX_PHYSICAL_SLOT:
            raise AmsMappingError(
                "AMS logical mapping values must be "
                "between 0 and 255."
            )

    if len(values) > capacity:
        raise AmsMappingError(
            "AMS logical mapping exceeds device wire "
            f"capacity: mapping={len(values)}, "
            f"capacity={capacity}."
        )

    if (
        expected_count is not None
        and len(values) != expected_count
    ):
        raise AmsMappingError(
            "AMS mapping length mismatch: "
            f"project has {expected_count} filament "
            f"preset(s), mapping has {len(values)} "
            "value(s)."
        )

    if (
        require_distinct
        and len(set(values)) != len(values)
    ):
        raise AmsMappingError(
            "Final multi-material AMS mapping requires "
            "distinct physical slots."
        )

    return values


def parse_logical_mapping(
    text: str,
    *,
    expected_count: int | None = None,
    require_distinct: bool = False,
) -> list[int]:
    tokens = [
        token.strip()
        for token in text.split(",")
        if token.strip()
    ]

    if not tokens:
        raise AmsMappingError(
            "AMS logical mapping cannot be empty."
        )

    try:
        values = [
            int(token)
            for token in tokens
        ]
    except ValueError as exc:
        raise AmsMappingError(
            "AMS logical mapping must be "
            "comma-separated integers."
        ) from exc

    return validate_logical_mapping(
        values,
        expected_count=expected_count,
        require_distinct=require_distinct,
    )


def to_x1c_wire_mapping(
    logical_mapping: Sequence[int],
) -> list[int]:
    values = validate_logical_mapping(
        logical_mapping,
        capacity=X1C_PROJECT_MAPPING_CAPACITY,
    )

    return (
        values
        + [-1] * (
            X1C_PROJECT_MAPPING_CAPACITY
            - len(values)
        )
    )


def external_spool_wire_mapping() -> list[int]:
    return [-1]
