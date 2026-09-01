from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_PROFILE_ROOT = Path(
    r"C:\Program Files\Bambu Studio\resources\profiles\BBL"
)

TARGETS = {
    "machine": (
        "machine",
        "Bambu Lab X1 Carbon 0.4 nozzle.json",
        "x1c_machine_full.json",
    ),
    "process": (
        "process",
        "0.20mm Standard @BBL X1C.json",
        "x1c_process_020_standard_full.json",
    ),
    "filament": (
        "filament",
        "Bambu PLA Basic @BBL X1C.json",
        "x1c_bambu_pla_basic_full.json",
    ),
}


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Not a JSON object: {path}")
    return data


def build_index(root: Path) -> tuple[
    dict[tuple[str, str], Path],
    dict[str, list[Path]],
]:
    typed: dict[tuple[str, str], Path] = {}
    by_name: dict[str, list[Path]] = {}

    for path in root.rglob("*.json"):
        try:
            data = read_json(path)
        except Exception:
            continue

        name = data.get("name")
        profile_type = data.get("type")

        if isinstance(name, str) and name:
            by_name.setdefault(name, []).append(path)
            if isinstance(profile_type, str) and profile_type:
                typed[(profile_type, name)] = path

        # Include/template files are often referenced by filename stem.
        by_name.setdefault(path.stem, []).append(path)

    return typed, by_name


def resolve_named(
    name: str,
    expected_type: str | None,
    *,
    typed: dict[tuple[str, str], Path],
    by_name: dict[str, list[Path]],
) -> Path:
    if expected_type:
        hit = typed.get((expected_type, name))
        if hit is not None:
            return hit

    candidates = by_name.get(name, [])
    unique: list[Path] = []
    seen: set[Path] = set()

    for p in candidates:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)

    if len(unique) == 1:
        return unique[0]

    if expected_type:
        typed_candidates = []
        for p in unique:
            try:
                d = read_json(p)
            except Exception:
                continue
            if d.get("type") == expected_type:
                typed_candidates.append(p)
        if len(typed_candidates) == 1:
            return typed_candidates[0]

    if not unique:
        raise RuntimeError(
            f"Cannot resolve inherited/include profile: {name!r}"
        )

    raise RuntimeError(
        f"Ambiguous inherited/include profile {name!r}: "
        + "; ".join(str(p) for p in unique)
    )


def merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    result.update(overlay)
    return result


def flatten_profile(
    path: Path,
    *,
    typed: dict[tuple[str, str], Path],
    by_name: dict[str, list[Path]],
    stack: tuple[Path, ...] = (),
) -> dict[str, Any]:
    path = path.resolve()

    if path in stack:
        cycle = " -> ".join(str(p) for p in (*stack, path))
        raise RuntimeError(f"Profile inheritance cycle: {cycle}")

    data = read_json(path)
    profile_type = data.get("type")
    if not isinstance(profile_type, str):
        profile_type = None

    result: dict[str, Any] = {}

    inherits = data.get("inherits")
    if isinstance(inherits, str) and inherits.strip():
        parent = resolve_named(
            inherits.strip(),
            profile_type,
            typed=typed,
            by_name=by_name,
        )
        result = merge(
            result,
            flatten_profile(
                parent,
                typed=typed,
                by_name=by_name,
                stack=(*stack, path),
            ),
        )

    includes = data.get("include")
    if isinstance(includes, str):
        includes = [includes]

    if isinstance(includes, list):
        for item in includes:
            if not isinstance(item, str) or not item.strip():
                continue
            inc = resolve_named(
                item.strip(),
                profile_type,
                typed=typed,
                by_name=by_name,
            )
            result = merge(
                result,
                flatten_profile(
                    inc,
                    typed=typed,
                    by_name=by_name,
                    stack=(*stack, path),
                ),
            )

    own = dict(data)
    own.pop("inherits", None)
    own.pop("include", None)

    result = merge(result, own)

    # Full CLI configs should no longer depend on system inheritance.
    result["from"] = "User"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flatten Bambu Studio X1C machine/process/filament presets for CLI slicing."
    )
    parser.add_argument(
        "--profile-root",
        default=str(DEFAULT_PROFILE_ROOT),
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/live_demo/_bambu_cli_profiles",
    )
    args = parser.parse_args()

    root = Path(args.profile_root).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()

    if not root.is_dir():
        print("RESULT=FAIL")
        print(f"REASON=PROFILE_ROOT_NOT_FOUND")
        print(f"PROFILE_ROOT={root}")
        return 2

    typed, by_name = build_index(root)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"PROFILE_ROOT={root}")
    print(f"OUTPUT_DIR={out_dir}")

    outputs: dict[str, str] = {}

    for key, (subdir, filename, out_name) in TARGETS.items():
        source = root / subdir / filename

        if not source.is_file():
            print("RESULT=FAIL")
            print(f"REASON=SOURCE_NOT_FOUND:{key}")
            print(f"SOURCE={source}")
            return 3

        flat = flatten_profile(
            source,
            typed=typed,
            by_name=by_name,
        )

        out_path = out_dir / out_name
        out_path.write_text(
            json.dumps(flat, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        outputs[key] = str(out_path)

        print(f"{key.upper()}_SOURCE={source}")
        print(f"{key.upper()}_FULL={out_path}")
        print(f"{key.upper()}_KEYS={len(flat)}")

    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
