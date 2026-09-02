"""Prepare one model with Bambu Studio's native automatic tree support."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import os
import shutil

from am_print_executor.bambu_auto_orient import (
    BambuAutoOrientError,
    auto_orient_with_bambu_cli,
)
from am_print_executor.bambu_headless_cli import run_bambu_cli
from am_print_executor.bambu_project_repair import (
    TEXTURED_PEI_BED_TYPE,
    TEXTURED_PEI_PLATE,
    TEXTURED_PEI_PLA_BED_TEMPERATURE_C,
    TEXTURED_PEI_Z_COMPENSATION_MM,
    finalize_bambu_gcode_3mf,
    validate_bambu_gcode_3mf,
    validate_bambu_project_3mf,
)
from am_print_executor.detachable_support import (
    detachable_process,
    validate_support_mode,
)
from am_print_executor.preparation_version import PREPARATION_REVISION


PIPELINE_NAME = "bambu_native_direct_print_v3"
NATIVE_SUPPORT_TYPE = "tree(auto)"
NATIVE_SUPPORT_STYLE = "tree_hybrid"


def save_report(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _apply_textured_pei_pla_temperature(filament_jsons) -> list[dict]:
    """Pin the material side of the Textured PEI contract before slicing."""

    changes: list[dict] = []
    for value in filament_jsons:
        path = Path(value)
        profile = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(profile, dict):
            raise ValueError(f"filament_profile_must_be_json_object:{path}")
        raw_types = profile.get("filament_type")
        filament_types = raw_types if isinstance(raw_types, list) else [raw_types]
        is_pla = any(str(item).upper() == "PLA" for item in filament_types)
        changed_keys: list[str] = []
        if is_pla:
            for key in ("textured_plate_temp", "textured_plate_temp_initial_layer"):
                current = profile.get(key)
                replacement = (
                    [str(TEXTURED_PEI_PLA_BED_TEMPERATURE_C)] * max(1, len(current))
                    if isinstance(current, list)
                    else str(TEXTURED_PEI_PLA_BED_TEMPERATURE_C)
                )
                if current != replacement:
                    profile[key] = replacement
                    changed_keys.append(key)
            if changed_keys:
                save_report(path, profile)
        changes.append(
            {
                "path": str(path),
                "filament_types": [str(item) for item in filament_types if item is not None],
                "pla_profile": is_pla,
                "temperature_c": (
                    TEXTURED_PEI_PLA_BED_TEMPERATURE_C if is_pla else None
                ),
                "changed_keys": changed_keys,
            }
        )
    if not any(item["pla_profile"] for item in changes):
        raise ValueError("textured_pei_preparation_requires_pla_profile")
    return changes


def slice_fixed_geometry(
    *,
    source: Path,
    directory: Path,
    studio_exe: Path,
    machine_json: Path,
    process_json: Path,
    filament_jsons,
    build_plate: str = TEXTURED_PEI_PLATE,
    support_mode: str = "detachable",
) -> dict:
    """Slice an oriented project with native tree support, without a GUI."""
    validate_support_mode(support_mode)
    directory.mkdir(parents=True, exist_ok=False)
    artifact = directory / "candidate.gcode.3mf"
    command = [
        str(studio_exe),
        "--arrange",
        "0",
        "--ensure-on-bed",
        "--support-type=tree(auto)",
        "--slice",
        "0",
        "--debug",
        "2",
        "--outputdir",
        str(directory),
        "--export-3mf",
        artifact.name,
        "--load-settings",
        f"{machine_json};{process_json}",
        "--curr-bed-type",
        build_plate,
        "--load-filaments",
        ";".join(map(str, filament_jsons)),
        str(source),
    ]
    execution = run_bambu_cli(
        command,
        expected_outputs=[artifact],
        cwd=directory,
        timeout=600,
    )
    save_report(directory / "slice.json", asdict(execution))
    if not execution.success:
        raise RuntimeError(
            f"Bambu native tree-support slice failed; see {directory / 'slice.json'}"
        )

    finalization = finalize_bambu_gcode_3mf(
        artifact,
        expected_bed_type=build_plate,
    )
    save_report(directory / "post_slice_validation.json", finalization)

    return {
        "artifact": artifact,
        "process_json": Path(process_json),
        "support_type": NATIVE_SUPPORT_TYPE,
        "support_style": NATIVE_SUPPORT_STYLE,
        "headless": True,
        "bambu_slice_succeeded": True,
        "build_plate": build_plate,
        "post_slice_validation_performed": True,
        "post_slice_validation": finalization,
    }


def _acceptance_receipt(
    *,
    source: Path,
    source_hash: str,
    project: Path,
    geometry: Path,
    oriented: dict,
    sliced: dict,
) -> dict:
    artifact = Path(sliced["artifact"])
    return {
        "status": "pass",
        "acceptance_basis": "bambu_cli_slice_and_artifact_validation",
        "post_slice_validation_performed": True,
        "source": str(source),
        "source_sha256": source_hash,
        "source_unchanged": source_hash
        == hashlib.sha256(source.read_bytes()).hexdigest(),
        "project": str(project),
        "artifact": str(artifact),
        "geometry": str(geometry),
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "geometry_sha256": hashlib.sha256(geometry.read_bytes()).hexdigest(),
        "auto_orient": oriented,
        "support_mode": "detachable",
        "support_generator": "bambu_studio_native",
        "support_type": NATIVE_SUPPORT_TYPE,
        "support_style": NATIVE_SUPPORT_STYLE,
        "build_plate": sliced["build_plate"],
        "post_slice_validation": sliced["post_slice_validation"],
        "model_self_support_required": False,
        "physical_removal_verified": False,
        "physical_print_performed": False,
        "printer_upload_performed": False,
        "headless": True,
    }


def prepare_verified_print(
    source: Path,
    output_path: Path,
    *,
    studio_exe: Path | None = None,
    machine_json: Path | None = None,
    process_json: Path | None = None,
    filament_jsons=None,
    allow_structural_changes: bool = True,
    critical_regions: tuple[dict, ...] = (),
    target_height_mm: float | None = None,
    support_mode: str = "detachable",
    build_plate: str = TEXTURED_PEI_PLATE,
) -> dict:
    """Orient and slice with Bambu's automatic tree supports only.

    ``allow_structural_changes`` remains accepted for callers created before
    this migration, but this implementation never changes model geometry to
    repair printability. ``critical_regions`` are retained in the audit only.
    """
    import tempfile

    import numpy as np

    from am_model_generator.coordinate_frame import load_print_scene
    from am_print_executor.bambu_profile_resolver import materialize_bambu_cli_profiles
    from am_print_executor.developer_mode_backend_v1120 import (
        discover_bambu_profiles,
        discover_bambu_studio,
    )
    from am_print_executor.mesh_integrity import (
        exact_welded_copy,
        inspect_mesh_integrity,
    )

    validate_support_mode(support_mode)
    if build_plate != TEXTURED_PEI_PLATE:
        raise ValueError(
            "unsupported_build_plate_for_verified_preparation: " + repr(build_plate)
        )
    source = Path(source).resolve()
    output_path = Path(output_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output_path.exists():
        raise FileExistsError(
            "Refusing to overwrite an earlier print artifact: " + str(output_path)
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(
        tempfile.mkdtemp(prefix="bambu_tree_preparation_", dir=output_path.parent)
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    mesh = exact_welded_copy(load_print_scene(source).to_mesh())
    validation = inspect_mesh_integrity(mesh)
    if not validation["valid"] or mesh.body_count != 1:
        raise ValueError("bambu_tree_preparation_requires_one_connected_solid")
    input_scale = 1.0
    if target_height_mm is not None:
        if not np.isfinite(target_height_mm) or target_height_mm <= 0:
            raise ValueError("invalid_target_height_mm")
        input_scale = float(target_height_mm) / float(mesh.extents[2])
        mesh.apply_scale(input_scale)

    studio_exe = Path(studio_exe or discover_bambu_studio() or "").resolve()
    if not studio_exe.is_file():
        raise ValueError("bambu_studio_not_found")
    discovered = discover_bambu_profiles(studio_exe)
    resolved = materialize_bambu_cli_profiles(
        studio_exe=studio_exe,
        machine_json=Path(machine_json or discovered["machine"]),
        process_json=Path(process_json or discovered["process"]),
        filament_jsons=list(filament_jsons or [discovered["filament"]]),
        cache_dir=directory / "profiles",
    )
    filament_temperature_changes = _apply_textured_pei_pla_temperature(
        resolved["filaments"]
    )
    process = json.loads(Path(resolved["process"]).read_text(encoding="utf-8"))
    process, support_action = detachable_process(process)
    process_file = directory / "bambu_tree_support_process.json"
    save_report(process_file, process)

    prepared = directory / "scaled_source.stl"
    mesh.export(prepared)
    project = directory / "auto_oriented.3mf"
    profiles = {
        "studio_exe": studio_exe,
        "machine_json": Path(resolved["machine"]),
        "process_json": process_file,
        "filament_jsons": list(resolved["filaments"]),
    }
    try:
        oriented = auto_orient_with_bambu_cli(
            source_model=prepared,
            output_path=project,
            require_flat_source=False,
            preserve_source_upright=False,
            trust_bambu_result=True,
            build_plate=build_plate,
            **profiles,
        )
    except BambuAutoOrientError as exc:
        save_report(
            directory / "execution_error.json",
            {
                "stage": "bambu_native_auto_orient",
                "error": str(exc),
                "geometry_rejected": exc.geometry_rejected,
                "source": str(source),
                "source_sha256": source_hash,
                "headless": True,
            },
        )
        raise
    save_report(directory / "auto_orient.json", oriented)
    project_validation = validate_bambu_project_3mf(
        project,
        expected_bed_type=build_plate,
    )
    save_report(directory / "project_validation.json", project_validation)
    sliced = slice_fixed_geometry(
        source=project,
        directory=directory / "tree_support_slice",
        support_mode=support_mode,
        build_plate=build_plate,
        **profiles,
    )
    if source_hash != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("source_changed_during_preparation")
    publish_fd, publish_name = tempfile.mkstemp(
        prefix=".verified_",
        suffix=".gcode.3mf",
        dir=output_path.parent,
    )
    os.close(publish_fd)
    publish_path = Path(publish_name)
    try:
        with publish_path.open("wb") as target, Path(sliced["artifact"]).open("rb") as current:
            shutil.copyfileobj(current, target)
        delivery_validation = validate_bambu_gcode_3mf(
            publish_path,
            expected_bed_type=build_plate,
        )
        os.replace(publish_path, output_path)
    finally:
        try:
            publish_path.unlink()
        except FileNotFoundError:
            pass
    delivery_validation["path"] = str(output_path)
    save_report(directory / "delivery_artifact_validation.json", delivery_validation)
    published_sliced = {
        **sliced,
        "artifact": output_path,
        "delivery_artifact_validation": delivery_validation,
    }
    receipt = _acceptance_receipt(
        source=source,
        source_hash=source_hash,
        project=project,
        geometry=prepared,
        oriented=oriented,
        sliced=published_sliced,
    )
    receipt["delivery_artifact_validation_performed"] = True
    receipt["delivery_artifact_validation"] = delivery_validation
    save_report(directory / "acceptance.json", receipt)
    save_report(
        directory / "preparation.json",
        {
            "pipeline": PIPELINE_NAME,
            "source": str(source),
            "source_sha256": source_hash,
            "uniform_input_scale": input_scale,
            "support_mode": "detachable",
            "support_generator": "bambu_studio_native",
            "support_type": NATIVE_SUPPORT_TYPE,
            "support_style": NATIVE_SUPPORT_STYLE,
            "headless": True,
            "bambu_decision_owner": True,
            "post_orientation_validation_performed": False,
            "project_container_validation_performed": True,
            "project_container_validation": project_validation,
            "post_slice_validation_performed": True,
            "post_slice_validation": sliced["post_slice_validation"],
            "delivery_artifact_validation_performed": True,
            "delivery_artifact_validation": delivery_validation,
            "build_plate": {
                "curr_bed_type": build_plate,
                "bed_type": TEXTURED_PEI_BED_TYPE,
                "pla_bed_temperature_c": TEXTURED_PEI_PLA_BED_TEMPERATURE_C,
                "z_compensation_mm": TEXTURED_PEI_Z_COMPENSATION_MM,
            },
            "filament_profile_adjustments": filament_temperature_changes,
            "structural_geometry_changes_requested": bool(allow_structural_changes),
            "structural_geometry_changes_applied": False,
            "critical_regions_recorded": list(critical_regions),
            "actions": [support_action],
        },
    )

    artifact_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
    project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
    geometry_hash = hashlib.sha256(prepared.read_bytes()).hexdigest()

    result = {
        "status": "slice_complete",
        "pipeline": PIPELINE_NAME,
        "preparation_revision": PREPARATION_REVISION,
        "terminal_blocker": None,
        "acceptance_basis": "bambu_cli_slice_and_artifact_validation",
        "post_slice_validation_performed": True,
        "post_slice_validation": sliced["post_slice_validation"],
        "delivery_artifact_validation_performed": True,
        "delivery_artifact_validation": delivery_validation,
        "support_mode": "detachable",
        "support_generator": "bambu_studio_native",
        "support_type": NATIVE_SUPPORT_TYPE,
        "support_style": NATIVE_SUPPORT_STYLE,
        "model_self_support_required": False,
        "headless": True,
        "build_plate": {
            "curr_bed_type": build_plate,
            "bed_type": TEXTURED_PEI_BED_TYPE,
            "pla_bed_temperature_c": TEXTURED_PEI_PLA_BED_TEMPERATURE_C,
            "z_compensation_mm": TEXTURED_PEI_Z_COMPENSATION_MM,
        },
        "artifact": {
            "path": str(output_path),
            "sha256": artifact_hash,
            "size_bytes": output_path.stat().st_size,
        },
        "project": {
            "path": str(project),
            "sha256": project_hash,
            "size_bytes": project.stat().st_size,
        },
        "project_container_validation_performed": True,
        "project_container_validation": project_validation,
        "geometry_path": str(prepared),
        "geometry_sha256": geometry_hash,
        "auto_orient_applied": True,
        "acceptance": receipt,
        "preparation_directory": str(directory),
        "source_unchanged": source_hash
        == hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    save_report(directory / "result.json", result)
    return result
