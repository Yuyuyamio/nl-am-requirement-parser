"""Prepare one model with Bambu Studio's native automatic tree support."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import shutil

from am_print_executor.bambu_auto_orient import (
    BambuAutoOrientError,
    auto_orient_with_bambu_cli,
)
from am_print_executor.bambu_headless_cli import run_bambu_cli
from am_print_executor.detachable_support import (
    detachable_process,
    validate_support_mode,
)
from am_print_executor.preparation_version import PREPARATION_REVISION


PIPELINE_NAME = "bambu_native_direct_print_v2"
NATIVE_SUPPORT_TYPE = "tree(auto)"
NATIVE_SUPPORT_STYLE = "tree_hybrid"


def save_report(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def slice_fixed_geometry(
    *,
    source: Path,
    directory: Path,
    studio_exe: Path,
    machine_json: Path,
    process_json: Path,
    filament_jsons,
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

    return {
        "artifact": artifact,
        "process_json": Path(process_json),
        "support_type": NATIVE_SUPPORT_TYPE,
        "support_style": NATIVE_SUPPORT_STYLE,
        "headless": True,
        "bambu_slice_succeeded": True,
        "post_slice_validation_performed": False,
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
        "acceptance_basis": "bambu_cli_slice_success",
        "post_slice_validation_performed": False,
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
    machine = json.loads(Path(resolved["machine"]).read_text(encoding="utf-8"))
    nozzle_value = machine.get("nozzle_diameter", [0.4])
    nozzle = float(nozzle_value[0] if isinstance(nozzle_value, list) else nozzle_value)
    process = json.loads(Path(resolved["process"]).read_text(encoding="utf-8"))
    process, support_action = detachable_process(process, nozzle)
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
    sliced = slice_fixed_geometry(
        source=project,
        directory=directory / "tree_support_slice",
        support_mode=support_mode,
        **profiles,
    )
    receipt = _acceptance_receipt(
        source=source,
        source_hash=source_hash,
        project=project,
        geometry=prepared,
        oriented=oriented,
        sliced=sliced,
    )
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
            "structural_geometry_changes_requested": bool(allow_structural_changes),
            "structural_geometry_changes_applied": False,
            "critical_regions_recorded": list(critical_regions),
            "actions": [support_action],
        },
    )

    if source_hash != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("source_changed_during_preparation")
    with output_path.open("xb") as target, Path(sliced["artifact"]).open("rb") as current:
        shutil.copyfileobj(current, target)
    artifact_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
    project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
    geometry_hash = hashlib.sha256(prepared.read_bytes()).hexdigest()

    result = {
        "status": "slice_complete",
        "pipeline": PIPELINE_NAME,
        "preparation_revision": PREPARATION_REVISION,
        "terminal_blocker": None,
        "acceptance_basis": "bambu_cli_slice_success",
        "post_slice_validation_performed": False,
        "support_mode": "detachable",
        "support_generator": "bambu_studio_native",
        "support_type": NATIVE_SUPPORT_TYPE,
        "support_style": NATIVE_SUPPORT_STYLE,
        "model_self_support_required": False,
        "headless": True,
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
