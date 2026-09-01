from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re

from pathlib import Path
from typing import Any


class FilamentMaterializerError(RuntimeError):
    pass


_HEX_COLOUR = re.compile(
    r"^#[0-9A-Fa-f]{6}$"
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def _load_profile(
    path: Path,
) -> dict[str, Any]:
    path = path.expanduser().resolve()

    if not path.is_file():
        raise FilamentMaterializerError(
            f"Base filament profile missing: {path}"
        )

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception as exc:
        raise FilamentMaterializerError(
            f"Cannot parse filament profile: {path}"
        ) from exc

    if not isinstance(data, dict):
        raise FilamentMaterializerError(
            "Filament profile root must be an object."
        )

    if data.get("type") != "filament":
        raise FilamentMaterializerError(
            "Base profile is not a filament preset."
        )

    if "filament_type" not in data:
        raise FilamentMaterializerError(
            "Base filament profile does not contain "
            "filament_type."
        )

    return data


def _set_existing_shape(
    obj: dict[str, Any],
    key: str,
    value: str,
) -> None:
    current = obj.get(key)

    if isinstance(current, str):
        obj[key] = value
        return

    if (
        isinstance(current, list)
        and len(current) == 1
        and isinstance(current[0], str)
    ):
        obj[key] = [value]
        return

    raise FilamentMaterializerError(
        f"Unsupported {key} field shape: "
        f"{type(current).__name__}"
    )


def _normalise_colour_value(
    value: Any,
) -> str:
    if isinstance(value, str):
        return value.upper()

    if (
        isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], str)
    ):
        return value[0].upper()

    raise FilamentMaterializerError(
        "Unsupported filament_colour field shape."
    )


def _validate_no_device_mapping(
    obj: dict[str, Any],
) -> None:
    forbidden_exact = {
        "ams_mapping",
        "ams_slot",
        "slot_mapping",
        "physical_slot",
        "printer_ip",
        "device_id",
    }

    violations = []

    for key in obj:
        if key.lower() in forbidden_exact:
            violations.append(key)

    if violations:
        raise FilamentMaterializerError(
            "Filament profile contains device mapping "
            f"field(s): {sorted(violations)}"
        )


def materialize_filament_profiles(
    *,
    base_profiles: list[Path],
    colours: list[str],
    output_dir: Path,
    names: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if not colours:
        raise FilamentMaterializerError(
            "At least one colour is required."
        )

    clean_colours = []

    for colour in colours:
        colour = colour.strip().upper()

        if not _HEX_COLOUR.fullmatch(
            colour
        ):
            raise FilamentMaterializerError(
                "Filament colours must use "
                "#RRGGBB format."
            )

        clean_colours.append(
            colour
        )

    if not base_profiles:
        raise FilamentMaterializerError(
            "At least one base profile is required."
        )

    resolved_bases = [
        path.expanduser().resolve()
        for path in base_profiles
    ]

    if (
        len(resolved_bases) == 1
        and len(clean_colours) > 1
    ):
        resolved_bases = (
            resolved_bases
            * len(clean_colours)
        )

    if (
        len(resolved_bases)
        != len(clean_colours)
    ):
        raise FilamentMaterializerError(
            "Base profile count must be 1 "
            "or exactly match colour count."
        )

    if names is None:
        clean_names = [
            f"NL-AM Material {index:02d}"
            for index in range(
                1,
                len(clean_colours) + 1,
            )
        ]
    else:
        clean_names = [
            value.strip()
            for value in names
        ]

        if (
            len(clean_names)
            != len(clean_colours)
        ):
            raise FilamentMaterializerError(
                "Name count must equal colour count."
            )

        if not all(clean_names):
            raise FilamentMaterializerError(
                "Material names cannot be empty."
            )

    output_dir = (
        output_dir
        .expanduser()
        .resolve()
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest_path = (
        output_dir
        / "filament_materialization.json"
    )

    managed_outputs = list(
        output_dir.glob(
            "filament_[0-9][0-9].json"
        )
    )

    if (
        manifest_path.exists()
        or managed_outputs
    ):
        if not overwrite:
            raise FilamentMaterializerError(
                "Managed materialized profiles already "
                "exist. Use --overwrite for an "
                "intentional rebuild."
            )

        for path in managed_outputs:
            path.unlink()

        if manifest_path.exists():
            manifest_path.unlink()

    original_hashes = {
        str(path): _sha256(path)
        for path in set(resolved_bases)
    }

    rows = []

    for index, (
        base_path,
        colour,
        display_name,
    ) in enumerate(
        zip(
            resolved_bases,
            clean_colours,
            clean_names,
        ),
        start=1,
    ):
        source = _load_profile(
            base_path
        )

        _validate_no_device_mapping(
            source
        )

        materialized = copy.deepcopy(
            source
        )

        if "filament_colour" in materialized:
            _set_existing_shape(
                materialized,
                "filament_colour",
                colour,
            )
        else:
            # The fully resolved Bambu filament preset used by
            # the CLI may legitimately omit project-level colour.
            # Derived logical profiles add it explicitly; Bambu
            # Studio remains responsible for generating its own
            # project mapping fields.
            materialized["filament_colour"] = [
                colour
            ]

        # "name" is a preset-level identifier and is
        # intentionally made unique for each logical material.
        materialized["name"] = (
            display_name
        )

        # Some fully resolved Bambu profiles also expose
        # filament_settings_id. Preserve its existing schema
        # shape instead of guessing string-vs-list form.
        if "filament_settings_id" in materialized:
            _set_existing_shape(
                materialized,
                "filament_settings_id",
                display_name,
            )

        # A materialized preset is local/user-derived.
        if "from" in materialized:
            current_from = materialized[
                "from"
            ]

            if isinstance(
                current_from,
                str,
            ):
                materialized[
                    "from"
                ] = "User"

        _validate_no_device_mapping(
            materialized
        )

        output_path = (
            output_dir
            / f"filament_{index:02d}.json"
        )

        output_path.write_text(
            json.dumps(
                materialized,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        reloaded = _load_profile(
            output_path
        )

        actual_colour = (
            _normalise_colour_value(
                reloaded[
                    "filament_colour"
                ]
            )
        )

        if actual_colour != colour:
            raise FilamentMaterializerError(
                "Materialized filament colour "
                "failed round-trip validation."
            )

        rows.append(
            {
                "logical_filament_id":
                    index,

                "display_name":
                    display_name,

                "colour":
                    colour,

                "base_profile":
                    str(base_path),

                "base_profile_sha256":
                    original_hashes[
                        str(base_path)
                    ],

                "path":
                    str(output_path),

                "sha256":
                    _sha256(output_path),

                "filament_type":
                    reloaded.get(
                        "filament_type"
                    ),

                "filament_vendor":
                    reloaded.get(
                        "filament_vendor"
                    ),
            }
        )

    # Verify source profiles were never modified.
    for path_text, before_hash in (
        original_hashes.items()
    ):
        after_hash = _sha256(
            Path(path_text)
        )

        if after_hash != before_hash:
            raise FilamentMaterializerError(
                "Base filament profile changed "
                "during materialization."
            )

    manifest = {
        "schema_version":
            "1.0.0",

        "module":
            "M4",

        "stage":
            "filament_profile_materialization",

        "status":
            "filament_profiles_materialized",

        "logical_filament_count":
            len(rows),

        "profiles":
            rows,

        "policy": {
            "base_profiles_modified":
                False,

            "ams_mapping_embedded":
                False,

            "physical_slot_embedded":
                False,

            "printer_ip_embedded":
                False,

            "device_id_embedded":
                False,

            "colour_hardcoded":
                False,

            "material_count_hardcoded":
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
            "filament_profiles_materialized",

        "manifest":
            str(manifest_path),

        "logical_filament_count":
            len(rows),

        "profiles":
            rows,

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
            "Materialize logical Bambu filament "
            "profiles without AMS device mapping."
        )
    )

    parser.add_argument(
        "--base-profile",
        action="append",
        required=True,
    )

    parser.add_argument(
        "--colour",
        action="append",
        required=True,
    )

    parser.add_argument(
        "--name",
        action="append",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
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
            materialize_filament_profiles(
                base_profiles=[
                    Path(value)
                    for value
                    in args.base_profile
                ],

                colours=
                    args.colour,

                names=
                    args.name,

                output_dir=Path(
                    args.output_dir
                ),

                overwrite=
                    args.overwrite,
            )
        )

    except Exception as exc:
        print(
            json.dumps(
                {
                    "status":
                        "filament_materialization_blocked",

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
