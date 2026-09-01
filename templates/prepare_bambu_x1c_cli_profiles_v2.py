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
        "x1c_machine_full_v2.json",
    ),
    "process": (
        "process",
        "0.20mm Standard @BBL X1C.json",
        "x1c_process_020_standard_full_v2.json",
    ),
    "filament": (
        "filament",
        "Bambu PLA Basic @BBL X1C.json",
        "x1c_bambu_pla_basic_full_v2.json",
    ),
}


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Not a JSON object: {path}")
    return data


def build_index(root: Path):
    typed: dict[tuple[str, str], Path] = {}
    by_name: dict[str, list[Path]] = {}

    for path in root.rglob("*.json"):
        try:
            data = read_json(path)
        except Exception:
            continue

        name = data.get("name")
        ptype = data.get("type")

        if isinstance(name, str) and name:
            by_name.setdefault(name, []).append(path)
            if isinstance(ptype, str) and ptype:
                typed[(ptype, name)] = path

        by_name.setdefault(path.stem, []).append(path)

    return typed, by_name


def resolve_named(
    name: str,
    expected_type: str | None,
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

    raise RuntimeError(f"Cannot uniquely resolve profile: {name!r}")


def flatten_profile(
    path: Path,
    typed: dict[tuple[str, str], Path],
    by_name: dict[str, list[Path]],
    stack: tuple[Path, ...] = (),
) -> dict[str, Any]:
    path = path.resolve()
    if path in stack:
        raise RuntimeError("Profile inheritance cycle")

    data = read_json(path)
    ptype = data.get("type")
    expected_type = ptype if isinstance(ptype, str) else None

    result: dict[str, Any] = {}

    inherits = data.get("inherits")
    if isinstance(inherits, str) and inherits.strip():
        parent = resolve_named(
            inherits.strip(),
            expected_type,
            typed,
            by_name,
        )
        result.update(
            flatten_profile(
                parent,
                typed,
                by_name,
                (*stack, path),
            )
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
                expected_type,
                typed,
                by_name,
            )
            result.update(
                flatten_profile(
                    inc,
                    typed,
                    by_name,
                    (*stack, path),
                )
            )

    own = dict(data)
    own.pop("inherits", None)
    own.pop("include", None)
    result.update(own)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile-root",
        default=str(DEFAULT_PROFILE_ROOT),
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/live_demo/_bambu_cli_profiles_v2",
    )
    args = parser.parse_args()

    root = Path(args.profile_root).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()

    if not root.is_dir():
        print("RESULT=FAIL")
        print("REASON=PROFILE_ROOT_NOT_FOUND")
        print(f"PROFILE_ROOT={root}")
        return 2

    typed, by_name = build_index(root)
    out_dir.mkdir(parents=True, exist_ok=True)

    for key, (subdir, filename, out_name) in TARGETS.items():
        source = root / subdir / filename

        if not source.is_file():
            print("RESULT=FAIL")
            print(f"REASON=SOURCE_NOT_FOUND:{key}")
            print(f"SOURCE={source}")
            return 3

        leaf = read_json(source)
        leaf_name = leaf.get("name")

        if not isinstance(leaf_name, str) or not leaf_name.strip():
            print("RESULT=FAIL")
            print(f"REASON=LEAF_NAME_MISSING:{key}")
            return 4

        flat = flatten_profile(source, typed, by_name)

        # Important for newer Bambu Studio CLI:
        # keep a system-profile identity while retaining full flattened values.
        flat["from"] = "User"
        flat["inherits"] = leaf_name

        out_path = out_dir / out_name
        out_path.write_text(
            json.dumps(flat, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        print(f"{key.upper()}_SOURCE={source}")
        print(f"{key.upper()}_FULL={out_path}")
        print(f"{key.upper()}_NAME={flat.get('name')}")
        print(f"{key.upper()}_INHERITS={flat.get('inherits')}")
        print(f"{key.upper()}_FROM={flat.get('from')}")
        print(f"{key.upper()}_KEYS={len(flat)}")

    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
