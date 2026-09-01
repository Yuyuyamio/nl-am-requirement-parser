from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MultiMaterialJobError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(
            path.read_text(encoding="utf-8-sig")
        )
    except FileNotFoundError as exc:
        raise MultiMaterialJobError(
            f"Manifest missing: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MultiMaterialJobError(
            f"Invalid JSON manifest: {path}"
        ) from exc

    if not isinstance(obj, dict):
        raise MultiMaterialJobError(
            "Manifest root must be a JSON object."
        )

    return obj


def _normalise_filaments(
    raw: Any,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise MultiMaterialJobError(
            "filament_profiles must be a non-empty list."
        )

    result: list[dict[str, Any]] = []

    # Backward-compatible QA fixture form:
    # ["gray.json", "yellow.json"]
    if all(isinstance(x, str) for x in raw):
        raw = [
            {
                "id": index + 1,
                "path": value,
            }
            for index, value in enumerate(raw)
        ]

    for row in raw:
        if not isinstance(row, dict):
            raise MultiMaterialJobError(
                "Each filament profile must be an object."
            )

        try:
            filament_id = int(row["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MultiMaterialJobError(
                "Each filament requires integer id."
            ) from exc

        path = Path(
            str(row.get("path", ""))
        ).expanduser().resolve()

        if filament_id < 1:
            raise MultiMaterialJobError(
                "Filament IDs start at 1."
            )

        if not path.is_file():
            raise MultiMaterialJobError(
                f"Filament profile missing: {path}"
            )

        result.append(
            {
                "id": filament_id,
                "path": str(path),
            }
        )

    result.sort(
        key=lambda x: x["id"]
    )

    ids = [
        row["id"]
        for row in result
    ]

    expected = list(
        range(
            1,
            len(result) + 1,
        )
    )

    if ids != expected:
        raise MultiMaterialJobError(
            "Filament IDs must be contiguous "
            f"1..N. Found {ids}."
        )

    return result


def _normalise_objects(
    raw: Any,
    *,
    valid_filament_ids: set[int],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise MultiMaterialJobError(
            "objects must be a non-empty list."
        )

    result: list[dict[str, Any]] = []

    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise MultiMaterialJobError(
                "Each object must be a JSON object."
            )

        path = Path(
            str(row.get("path", ""))
        ).expanduser().resolve()

        if not path.is_file():
            raise MultiMaterialJobError(
                f"Model file missing: {path}"
            )

        # Backward-compatible QA fixture field.
        ids_raw = row.get(
            "project_filament_ids"
        )

        if ids_raw is None:
            old_id = row.get(
                "project_filament_id"
            )

            ids_raw = [old_id]

        if (
            not isinstance(ids_raw, list)
            or not ids_raw
        ):
            raise MultiMaterialJobError(
                "project_filament_ids must "
                "be a non-empty list."
            )

        try:
            filament_ids = [
                int(x)
                for x in ids_raw
            ]
        except (TypeError, ValueError) as exc:
            raise MultiMaterialJobError(
                "Invalid project filament IDs."
            ) from exc

        missing = (
            set(filament_ids)
            - valid_filament_ids
        )

        if missing:
            raise MultiMaterialJobError(
                "Object references undefined "
                f"filament IDs: {sorted(missing)}"
            )

        count = int(
            row.get("count", 1)
        )

        if count < 1:
            raise MultiMaterialJobError(
                "Object count must be >= 1."
            )

        positions = row.get(
            "positions_mm"
        )

        # Default one centred instance.
        if positions is None:
            if count != 1:
                raise MultiMaterialJobError(
                    "count > 1 requires positions_mm."
                )

            positions = [
                [128.0, 128.0, 0.0]
            ]

        if (
            not isinstance(positions, list)
            or len(positions) != count
        ):
            raise MultiMaterialJobError(
                "positions_mm length must equal count."
            )

        clean_positions = []

        for pos in positions:
            if (
                not isinstance(pos, list)
                or len(pos) != 3
            ):
                raise MultiMaterialJobError(
                    "Each position must be [x,y,z]."
                )

            clean_positions.append(
                [
                    float(pos[0]),
                    float(pos[1]),
                    float(pos[2]),
                ]
            )

        assemble_index = row.get(
            "assemble_index"
        )

        if assemble_index is None:
            assemble_index = list(
                range(1, count + 1)
            )

        if (
            not isinstance(assemble_index, list)
            or len(assemble_index) != count
        ):
            raise MultiMaterialJobError(
                "assemble_index length "
                "must equal object count."
            )

        result.append(
            {
                "object_index": index,
                "path": str(path),
                "count": count,
                "project_filament_ids":
                    filament_ids,
                "positions_mm":
                    clean_positions,
                "assemble_index": [
                    int(x)
                    for x in assemble_index
                ],
            }
        )

    return result


def load_multimaterial_job(
    path: Path,
) -> dict[str, Any]:
    path = path.expanduser().resolve()

    raw = _read_json(path)

    status = raw.get(
        "status"
    )

    # QA fixture remains supported as an input adapter,
    # but is NOT the production schema.
    if status == "multimaterial_fixture_prepared":
        source_kind = "qa_fixture_adapter"

    elif status == "multimaterial_job_ready":
        source_kind = "generic_job"

    else:
        raise MultiMaterialJobError(
            "Manifest status must be either "
            "multimaterial_job_ready or "
            "multimaterial_fixture_prepared."
        )

    filaments = _normalise_filaments(
        raw.get("filament_profiles")
    )

    valid_ids = {
        row["id"]
        for row in filaments
    }

    objects = _normalise_objects(
        raw.get("objects"),
        valid_filament_ids=valid_ids,
    )

    used_ids = sorted(
        {
            filament_id
            for row in objects
            for filament_id
            in row["project_filament_ids"]
        }
    )

    unused = (
        valid_ids
        - set(used_ids)
    )

    if unused:
        raise MultiMaterialJobError(
            "Every declared filament must be "
            "used by at least one object. "
            f"Unused IDs: {sorted(unused)}"
        )

    return {
        "schema_version": "1.0.0",
        "module": "M4",
        "stage": "generic_multimaterial_job",
        "status": "multimaterial_job_ready",
        "source_manifest":
            str(path),
        "source_kind":
            source_kind,
        "filament_profiles":
            filaments,
        "objects":
            objects,
        "project_filament_count":
            len(filaments),
        "used_filament_ids":
            used_ids,
    }
