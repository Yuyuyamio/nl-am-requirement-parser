from __future__ import annotations

import hashlib
import math
import os
import tempfile
import time
import zipfile

from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.etree import ElementTree as ET

from am_print_executor.bambu_headless_cli import (
    BambuCliResult,
    run_bambu_cli,
)
from am_print_executor.bambu_project_repair import (
    repair_bambu_model_settings_xml,
)
from am_print_executor.flat_base_gate import (
    FlatBaseGateError,
    inspect_flat_printing_base,
)


class BambuAutoOrientError(RuntimeError):
    def __init__(self, message: str, *, geometry_rejected: bool = False):
        super().__init__(message)
        self.geometry_rejected = geometry_rejected


_REQUIRED_PROJECT_MEMBERS = {
    "[Content_Types].xml",
    "3D/3dmodel.model",
    "Metadata/model_settings.config",
    "Metadata/project_settings.config",
    "Metadata/slice_info.config",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def inspect_auto_oriented_project(
    path: Path,
) -> dict[str, Any]:
    """Validate the committed result of Bambu Auto Orient.

    Exit zero and a ZIP header are not enough.  The project must contain the
    Bambu metadata used by the next stage, at least one printable instance,
    and finite 4x4 part transforms produced by the preparation step.
    """

    path = Path(path).expanduser().resolve()

    if not path.is_file():
        raise BambuAutoOrientError(
            f"Auto Orient project is missing: {path}"
        )

    if not zipfile.is_zipfile(path):
        raise BambuAutoOrientError(
            f"Auto Orient output is not a 3MF ZIP: {path}"
        )

    try:
        with zipfile.ZipFile(path, "r") as archive:
            bad_member = archive.testzip()

            if bad_member is not None:
                raise BambuAutoOrientError(
                    "Auto Orient output contains a corrupt member: "
                    + bad_member
                )

            names = set(archive.namelist())
            missing = sorted(
                _REQUIRED_PROJECT_MEMBERS - names
            )

            if missing:
                raise BambuAutoOrientError(
                    "Auto Orient project is incomplete; missing: "
                    + ", ".join(missing)
                )

            settings_xml = archive.read(
                "Metadata/model_settings.config"
            )
            model_xml = archive.read(
                "3D/3dmodel.model"
            )
    except (OSError, zipfile.BadZipFile) as exc:
        raise BambuAutoOrientError(
            f"Could not inspect Auto Orient project: {exc}"
        ) from exc

    try:
        settings_root = ET.fromstring(settings_xml)
        model_root = ET.fromstring(model_xml)
    except ET.ParseError as exc:
        raise BambuAutoOrientError(
            f"Auto Orient project contains invalid XML: {exc}"
        ) from exc

    objects = settings_root.findall(".//object")
    parts = settings_root.findall(".//part")
    instances = settings_root.findall(
        ".//plate/model_instance"
    )

    if not objects or not parts or not instances:
        raise BambuAutoOrientError(
            "Auto Orient project has no printable object/part/instance."
        )

    matrices: list[list[float]] = []

    for part in parts:
        matrix_node = next(
            (
                node
                for node in part.findall("metadata")
                if node.get("key") == "matrix"
            ),
            None,
        )

        if matrix_node is None:
            raise BambuAutoOrientError(
                "Auto Orient part is missing its transform matrix."
            )

        raw_values = (
            matrix_node.get("value") or ""
        ).split()

        if len(raw_values) != 16:
            raise BambuAutoOrientError(
                "Auto Orient part transform is not a 4x4 matrix."
            )

        try:
            values = [
                float(value)
                for value in raw_values
            ]
        except ValueError as exc:
            raise BambuAutoOrientError(
                "Auto Orient part transform is not numeric."
            ) from exc

        if not all(math.isfinite(value) for value in values):
            raise BambuAutoOrientError(
                "Auto Orient part transform contains a non-finite value."
            )

        matrices.append(values)

    build_items = [
        node
        for node in model_root.iter()
        if node.tag.rsplit("}", 1)[-1] == "item"
    ]

    if not build_items:
        raise BambuAutoOrientError(
            "Auto Orient project has no 3MF build item."
        )

    # Structural XML validity cannot prove an object was put on a usable
    # face. Inspect the committed world-space geometry after all 3MF part
    # and build transforms, not the original STL or metadata matrix alone.
    try:
        from am_print_executor.slice_geometry_frame import placed_mesh_from_project
        placed = placed_mesh_from_project(path)
        flat_base = inspect_flat_printing_base(placed)
    except Exception as exc:
        raise BambuAutoOrientError(f"Auto Orient geometry inspection failed: {exc}") from exc
    if not flat_base["base_flatness_passed"] or abs(float(placed.bounds[0, 2])) > .01:
        raise BambuAutoOrientError(
            "Auto Orient did not produce a stable planar bed contact: " + repr(flat_base["blockers"]),
            geometry_rejected=True,
        )

    return {
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "object_count": len(objects),
        "part_count": len(parts),
        "model_instance_count": len(instances),
        "build_item_count": len(build_items),
        "part_matrices": matrices,
        "flat_base_after_orientation": flat_base,
        "geometry_bounds_mm": placed.bounds.tolist(),
    }


def _attempt_record(
    *,
    attempt: int,
    result: BambuCliResult | None,
    validation_error: str | None,
    elapsed_seconds: float,
) -> dict[str, Any]:
    if result is None:
        return {
            "attempt": attempt,
            "success": False,
            "elapsed_seconds": round(
                elapsed_seconds,
                3,
            ),
            "runner_error": validation_error,
        }

    return {
        "attempt": attempt,
        "success": (
            result.success
            and validation_error is None
        ),
        "elapsed_seconds": round(
            elapsed_seconds,
            3,
        ),
        "raw_exit": result.raw_exit,
        "signed_exit": result.signed_exit,
        "outputs_exist": result.outputs_exist,
        "lock_wait_seconds": (
            result.lock_wait_seconds
        ),
        "process_settle_seconds": (
            result.process_settle_seconds
        ),
        "command": list(result.command),
        "validation_error": validation_error,
        "stdout_tail": result.stdout[-3000:],
        "stderr_tail": result.stderr[-3000:],
    }


def auto_orient_with_bambu_cli(
    *,
    studio_exe: Path,
    output_path: Path,
    machine_json: Path,
    process_json: Path,
    filament_jsons: Iterable[Path],
    source_model: Path | None = None,
    assemble_list: Path | None = None,
    build_plate: str | None = None,
    extra_options: Sequence[str] = (),
    require_flat_source: bool = True,
    preserve_source_upright: bool = True,
    trust_bambu_result: bool = False,
    max_attempts: int = 3,
    timeout: float = 600,
) -> dict[str, Any]:
    """Run Bambu Auto Orient as an isolated headless stage.

    Exactly one of ``source_model`` and ``assemble_list`` is required.  Each
    attempt writes to a fresh private path; the requested project is replaced
    atomically after Bambu reports success. Legacy callers may retain local
    structural checks; production sets ``trust_bambu_result=True`` and skips
    every post-orientation inspection.
    """

    studio_exe = Path(
        studio_exe
    ).expanduser().resolve()
    output_path = Path(
        output_path
    ).expanduser().resolve()
    machine_json = Path(
        machine_json
    ).expanduser().resolve()
    process_json = Path(
        process_json
    ).expanduser().resolve()
    filaments = [
        Path(path).expanduser().resolve()
        for path in filament_jsons
    ]

    if (source_model is None) == (assemble_list is None):
        raise BambuAutoOrientError(
            "Exactly one Auto Orient input is required: "
            "source_model or assemble_list."
        )

    input_path = Path(
        source_model
        if source_model is not None
        else assemble_list
    ).expanduser().resolve()

    required_inputs = [
        studio_exe,
        machine_json,
        process_json,
        *filaments,
        input_path,
    ]
    missing_inputs = [
        str(path)
        for path in required_inputs
        if not path.is_file()
    ]

    if missing_inputs:
        raise BambuAutoOrientError(
            "Auto Orient input is missing: "
            + "; ".join(missing_inputs)
        )

    if not filaments:
        raise BambuAutoOrientError(
            "Auto Orient requires at least one filament profile."
        )

    flat_base_gate: dict[str, Any] | None = None
    if source_model is not None and require_flat_source:
        try:
            flat_base_gate = inspect_flat_printing_base(input_path)
        except FlatBaseGateError as exc:
            raise BambuAutoOrientError(
                "FLAT_BASE_GATE = BLOCK before Bambu Auto Orient: "
                + str(exc)
            ) from exc
        if not flat_base_gate["base_flatness_passed"]:
            raise BambuAutoOrientError(
                "FLAT_BASE_GATE = BLOCK before Bambu Auto Orient: "
                + repr(flat_base_gate["blockers"])
            )

    if max_attempts < 1:
        raise BambuAutoOrientError(
            "max_attempts must be at least 1."
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    settings_arg = ";".join(
        (str(machine_json), str(process_json))
    )
    filaments_arg = ";".join(
        str(path)
        for path in filaments
    )
    attempts: list[dict[str, Any]] = []
    started = time.monotonic()
    successful_result: BambuCliResult | None = None
    inspection: dict[str, Any] | None = None
    repair_result: dict[str, object] | None = None
    geometry_rejections = 0

    with tempfile.TemporaryDirectory(
        prefix=".nl_am_bambu_auto_orient_",
        dir=output_path.parent,
    ) as temporary:
        work_dir = Path(temporary)

        for attempt in range(1, max_attempts + 1):
            candidate = (
                work_dir
                / f"attempt_{attempt}.project.3mf"
            )
            command = [
                str(studio_exe),
                "--orient", "1",
                "--arrange", "1",
                "--ensure-on-bed",
                *(
                    str(value)
                    for value in extra_options
                ),
                "--load-settings", settings_arg,
            ]

            if build_plate:
                command.extend(
                    ["--curr-bed-type", build_plate]
                )

            command.extend(
                [
                    "--load-filaments",
                    filaments_arg,
                ]
            )

            if assemble_list is not None:
                command.extend(
                    [
                        "--load-assemble-list",
                        str(input_path),
                    ]
                )

            command.extend(
                [
                    "--debug", "5",
                    "--export-3mf",
                    str(candidate),
                ]
            )

            if source_model is not None:
                command.append(str(input_path))

            attempt_started = time.monotonic()
            result: BambuCliResult | None = None
            error: str | None = None

            try:
                result = run_bambu_cli(
                    command,
                    expected_outputs=[candidate],
                    cwd=work_dir,
                    timeout=timeout,
                )

                if not result.success:
                    error = (
                        "Bambu CLI did not create a successful "
                        "Auto Orient artifact."
                    )
                else:
                    if trust_bambu_result:
                        # Bambu 2.7 may emit unescaped XML that its next CLI
                        # invocation cannot reopen. Normalising that one XML
                        # member is transport compatibility, not a geometry,
                        # support, or printability decision.
                        repair_result = repair_bambu_model_settings_xml(
                            candidate
                        )
                        inspection = {
                            "path": str(candidate),
                            "trusted_bambu_output": True,
                            "post_orientation_validation_performed": False,
                            "xml_transport_repair": repair_result,
                        }
                    else:
                        repair_result = repair_bambu_model_settings_xml(
                            candidate
                        )
                        inspection = (
                            inspect_auto_oriented_project(
                                candidate
                            )
                        )
                        if (
                            source_model is not None
                            and preserve_source_upright
                            and input_path.suffix.lower() == ".stl"
                        ):
                            from am_print_executor.semantic_pose_gate import inspect_upright_source_preserved
                            pose = inspect_upright_source_preserved(input_path, candidate)
                            if pose["status"] != "pass":
                                raise BambuAutoOrientError("Auto Orient changed the required upright pose: " + repr(pose), geometry_rejected=True)
                            inspection["upright_pose"] = pose
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                geometry_rejections += int(isinstance(exc, BambuAutoOrientError) and exc.geometry_rejected)

            attempts.append(
                _attempt_record(
                    attempt=attempt,
                    result=result,
                    validation_error=error,
                    elapsed_seconds=(
                        time.monotonic()
                        - attempt_started
                    ),
                )
            )

            if (
                result is not None
                and result.success
                and error is None
                and inspection is not None
            ):
                os.replace(candidate, output_path)
                successful_result = result
                if trust_bambu_result:
                    inspection["path"] = str(output_path)
                else:
                    checked_pose = inspection.get("upright_pose")
                    inspection = (
                        inspect_auto_oriented_project(
                            output_path
                        )
                    )
                    inspection["xml_repair"] = (
                        repair_result
                    )
                    if checked_pose is not None:
                        inspection["upright_pose"] = checked_pose
                break

            if attempt < max_attempts:
                time.sleep(min(0.25 * attempt, 1.0))

    if successful_result is None or inspection is None:
        raise BambuAutoOrientError(
            "Bambu Auto Orient failed after isolated retries.\n"
            f"attempts={attempts!r}",
            geometry_rejected=geometry_rejections == len(attempts),
        )

    return {
        "status": "auto_orient_complete",
        "pipeline": "bambu_headless_auto_orient_v2",
        "output": inspection,
        "command": list(successful_result.command),
        "returncode_raw": successful_result.raw_exit,
        "returncode_signed": successful_result.signed_exit,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "elapsed_seconds": round(
            time.monotonic() - started,
            3,
        ),
        "headless": True,
        "trusted_bambu_output": bool(trust_bambu_result),
        "post_orientation_validation_performed": not trust_bambu_result,
        "source_kind": (
            "model"
            if source_model is not None
            else "assemble_list"
        ),
        "flat_base_gate": flat_base_gate,
    }
