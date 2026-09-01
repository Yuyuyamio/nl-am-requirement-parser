from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


class BambuProfileResolutionError(RuntimeError):
    pass


_METADATA_KEYS = {
    "inherits",
    "include",
}


def _read_json(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()

    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig")
        )
    except FileNotFoundError as exc:
        raise BambuProfileResolutionError(
            f"Bambu profile missing: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise BambuProfileResolutionError(
            f"Invalid Bambu profile JSON: {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise BambuProfileResolutionError(
            f"Bambu profile must be a JSON object: {path}"
        )

    return value


def _build_name_index(
    category_root: Path,
) -> dict[str, Path]:
    category_root = Path(category_root).resolve()

    index: dict[str, Path] = {}
    duplicates: dict[str, list[Path]] = {}

    for path in category_root.rglob("*.json"):
        try:
            value = _read_json(path)
        except BambuProfileResolutionError:
            continue

        name = value.get("name")

        if not isinstance(name, str) or not name.strip():
            continue

        name = name.strip()

        previous = index.get(name)

        if previous is None:
            index[name] = path.resolve()
            continue

        if previous != path.resolve():
            duplicates.setdefault(
                name,
                [previous],
            ).append(path.resolve())

    if duplicates:
        details = "; ".join(
            f"{name}: {paths}"
            for name, paths in sorted(duplicates.items())
        )

        raise BambuProfileResolutionError(
            "Ambiguous Bambu profile names: "
            + details
        )

    return index


def _merge_profile(
    path: Path,
    *,
    index: dict[str, Path],
    stack: tuple[str, ...] = (),
) -> dict[str, Any]:
    path = Path(path).resolve()
    current = _read_json(path)

    name_value = current.get("name")
    name = (
        str(name_value).strip()
        if name_value is not None
        else path.stem
    )

    if name in stack:
        chain = " -> ".join((*stack, name))
        raise BambuProfileResolutionError(
            f"Bambu profile inheritance cycle: {chain}"
        )

    next_stack = (*stack, name)

    merged: dict[str, Any] = {}

    parent = current.get("inherits")

    if isinstance(parent, str) and parent.strip():
        parent_name = parent.strip()
        parent_path = index.get(parent_name)

        if parent_path is None:
            raise BambuProfileResolutionError(
                f"Unresolved Bambu inherits '{parent_name}' "
                f"from {path}"
            )

        merged.update(
            _merge_profile(
                parent_path,
                index=index,
                stack=next_stack,
            )
        )

    includes = current.get("include", [])

    if isinstance(includes, str):
        includes = [includes]

    if includes is None:
        includes = []

    if not isinstance(includes, list):
        raise BambuProfileResolutionError(
            f"Invalid include list in {path}"
        )

    for include_name in includes:
        if not isinstance(include_name, str):
            raise BambuProfileResolutionError(
                f"Invalid include entry in {path}"
            )

        include_name = include_name.strip()

        if not include_name:
            continue

        include_path = index.get(include_name)

        if include_path is None:
            raise BambuProfileResolutionError(
                f"Unresolved Bambu include '{include_name}' "
                f"from {path}"
            )

        merged.update(
            _merge_profile(
                include_path,
                index=index,
                stack=next_stack,
            )
        )

    for key, value in current.items():
        if key in _METADATA_KEYS:
            continue

        merged[key] = value

    return merged


def flatten_system_profile(
    profile_path: Path,
    *,
    category_root: Path,
) -> dict[str, Any]:
    profile_path = Path(profile_path).resolve()
    category_root = Path(category_root).resolve()

    leaf = _read_json(profile_path)

    leaf_name_value = leaf.get("name")

    if not isinstance(leaf_name_value, str):
        raise BambuProfileResolutionError(
            f"Bambu leaf profile has no name: {profile_path}"
        )

    leaf_name = leaf_name_value.strip()

    index = _build_name_index(category_root)

    if leaf_name not in index:
        index[leaf_name] = profile_path

    flat = _merge_profile(
        profile_path,
        index=index,
    )

    # The CLI currently expects a full user-style config rather
    # than a raw system resource preset. Retain the original
    # system preset as the parent identity while supplying every
    # resolved setting explicitly.
    flat["from"] = "User"
    flat["name"] = f"NL-AM CLI Full - {leaf_name}"
    flat["inherits"] = leaf_name

    flat.pop("include", None)
    flat.pop("instantiation", None)

    profile_type = str(
        flat.get("type", "")
    ).strip().lower()

    if profile_type == "machine":
        flat["printer_settings_id"] = flat["name"]

    elif profile_type == "process":
        flat["print_settings_id"] = flat["name"]

    elif profile_type == "filament":
        flat["filament_settings_id"] = [
            flat["name"]
        ]

    return flat


def _is_under(
    path: Path,
    root: Path,
) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _write_deterministic_profile(
    value: dict[str, Any],
    *,
    kind: str,
    cache_dir: Path,
) -> Path:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    digest = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:16]

    cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        cache_dir
        / f"{kind}_{digest}.json"
    )

    pretty = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

    if not output.is_file():
        output.write_text(
            pretty,
            encoding="utf-8",
        )
    else:
        existing = output.read_text(
            encoding="utf-8-sig"
        )

        if existing != pretty:
            raise BambuProfileResolutionError(
                f"Profile cache collision: {output}"
            )

    return output.resolve()


def materialize_bambu_cli_profiles(
    *,
    studio_exe: Path,
    machine_json: Path,
    process_json: Path,
    filament_jsons: Iterable[Path],
    cache_dir: Path,
) -> dict[str, Any]:
    studio_exe = Path(studio_exe).resolve()

    profile_root = (
        studio_exe.parent
        / "resources"
        / "profiles"
        / "BBL"
    ).resolve()

    category_roots = {
        "machine": profile_root / "machine",
        "process": profile_root / "process",
        "filament": profile_root / "filament",
    }

    def convert(
        path: Path,
        kind: str,
    ) -> Path:
        path = Path(path).resolve()

        category_root = category_roots[kind].resolve()

        # Explicit external user profiles are already allowed to
        # be full configs. Only bundled BBL system resources need
        # inheritance/include expansion.
        if not _is_under(path, category_root):
            return path

        flat = flatten_system_profile(
            path,
            category_root=category_root,
        )

        return _write_deterministic_profile(
            flat,
            kind=kind,
            cache_dir=cache_dir,
        )

    machine = convert(
        machine_json,
        "machine",
    )

    process = convert(
        process_json,
        "process",
    )

    filaments = [
        convert(path, "filament")
        for path in filament_jsons
    ]

    if not filaments:
        raise BambuProfileResolutionError(
            "At least one filament profile is required."
        )

    return {
        "machine": machine,
        "process": process,
        "filaments": filaments,
        "cache_dir": Path(cache_dir).resolve(),
    }
