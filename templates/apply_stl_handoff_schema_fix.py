from __future__ import annotations

import argparse
import py_compile
import shutil
from datetime import datetime
from pathlib import Path


IMPORT_MARKER = """from .normalization import (
    ensure_valid_normalization_receipt,
)
"""

IMPORT_REPLACEMENT = """from .normalization import (
    ensure_valid_normalization_receipt,
)
from .normalized_validation import (
    ensure_valid_normalized_mesh_report,
)
"""

OLD_BLOCK = """    if manifest.get("status") == (
        "generated"
    ):
        validation_result = (
            validate_m2_mesh(
                task_path
            )
        )
        validation_report = (
            validation_result["report"]
        )

        if (
            not validation_result.get(
                "hard_constraints_passed"
            )
            or validation_report.get(
                "model_file"
            )
            != NORMALIZED_MODEL_FILENAME
        ):
            raise M2ProviderError(
                "M2_STL_SOURCE_VALIDATION_FAILED",
                "归一化模型没有通过当前Mesh验证",
            )

"""

NEW_BLOCK = """    if manifest.get("status") == (
        "generated"
    ):
        ensure_valid_normalized_mesh_report(
            normalized_validation
        )

"""


def resolve_target(project_root: Path) -> Path:
    return (
        project_root
        / "src"
        / "am_model_generator"
        / "stl_handoff.py"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply the normalized-mesh Schema fix to stl_handoff.py."
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Repository root. Default: current directory.",
    )
    args = parser.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    target = resolve_target(root)

    print(f"PROJECT_ROOT={root}")
    print(f"TARGET={target}")

    if not target.is_file():
        print("RESULT=FAIL")
        print("REASON=TARGET_NOT_FOUND")
        return 2

    text = target.read_text(encoding="utf-8-sig")

    already_has_import = (
        "from .normalized_validation import (" in text
        and "ensure_valid_normalized_mesh_report" in text
    )
    already_has_new_block = NEW_BLOCK in text

    if already_has_import and already_has_new_block:
        print("RESULT=ALREADY_FIXED")
        try:
            py_compile.compile(str(target), doraise=True)
        except Exception as exc:
            print("VERIFY=FAIL")
            print(f"REASON={exc}")
            return 3
        print("VERIFY=PASS")
        return 0

    if OLD_BLOCK not in text:
        print("RESULT=FAIL")
        print("REASON=EXPECTED_OLD_VALIDATION_BLOCK_NOT_FOUND")
        return 4

    if not already_has_import:
        if IMPORT_MARKER not in text:
            print("RESULT=FAIL")
            print("REASON=IMPORT_MARKER_NOT_FOUND")
            return 5
        text = text.replace(
            IMPORT_MARKER,
            IMPORT_REPLACEMENT,
            1,
        )

    text = text.replace(
        OLD_BLOCK,
        NEW_BLOCK,
        1,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = target.with_name(
        f"{target.name}.pre_normalized_schema_fix_{timestamp}.bak"
    )
    shutil.copy2(target, backup)

    target.write_text(text, encoding="utf-8")

    try:
        py_compile.compile(str(target), doraise=True)
    except Exception as exc:
        shutil.copy2(backup, target)
        print("RESULT=FAIL")
        print("REASON=COMPILE_FAILED_ROLLED_BACK")
        print(f"DETAIL={exc}")
        return 6

    print("RESULT=FIX_APPLIED")
    print(f"BACKUP={backup}")
    print("VERIFY=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
