$ErrorActionPreference = "Stop"

$Root = 'C:\Users\PC\Documents\GitHub\nl-am-requirement-parser'
Set-Location -LiteralPath $Root

if (Test-Path -LiteralPath (Join-Path $Root '.venv\Scripts\python.exe')) {
    $Py = (Resolve-Path (Join-Path $Root '.venv\Scripts\python.exe')).Path
}
else {
    $Py = (Get-Command python -ErrorAction Stop).Source
}

$env:PYTHONPATH = (Join-Path $Root 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:NL_AM_ROOT = $Root

Write-Host "=== FIX_PRINTABILITY_REGRESSION_FLAT_BASE : RESUME ONLY ===" -ForegroundColor Cyan
Write-Host "ROOT=$Root"
Write-Host "PYTHON=$Py"
Write-Host ""
Write-Host "No M1->M4 full rerun. No printer/MQTT. No commit/push. No retry/sleep." -ForegroundColor DarkGray
Write-Host ""

Write-Host "=== 1/4 focused flat-base regression tests ===" -ForegroundColor Cyan
& $Py -m unittest discover -s tests -p "test_flat_base_gate.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "FOCUSED_TESTS = FAIL. Stop before Bambu."
}
$env:NL_AM_FOCUSED_TESTS = "PASS"

Write-Host ""
Write-Host "=== 2/4 current focused git diff --stat ===" -ForegroundColor Cyan
git diff --stat -- `
    src/am_print_executor/flat_base_gate.py `
    tests/test_flat_base_gate.py `
    src/am_model_generator/normalization.py `
    src/am_print_executor/manifold_repair.py `
    src/am_print_executor/m3_printability_optimizer.py

Write-Host ""
Write-Host "=== 3/4 latest candidate -> support slice -> Printability Gate -> one Auto Orient ===" -ForegroundColor Cyan

@'
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.gcode_printability_gate import (
    inspect_final_gcode_printability,
    inspect_mesh_topology,
)
from am_print_executor.bambu_headless_cli import run_bambu_cli
from am_print_executor.multimaterial_project import (
    repair_bambu_model_settings_xml,
)
from am_print_executor.bambu_auto_orient import (
    auto_orient_with_bambu_cli,
)


ROOT = Path(os.environ["NL_AM_ROOT"]).resolve()
STUDIO = Path(r"C:\Program Files\Bambu Studio\bambu-studio.exe")
ACCEPTANCE_ROOT = ROOT / "outputs" / "flat_base_acceptance"
KNOWN_OLD_FAILED_RUN = "AUTO-20260824-161128-9EA75FD9"

FOCUSED_DIFF_PATHS = [
    "src/am_print_executor/flat_base_gate.py",
    "tests/test_flat_base_gate.py",
    "src/am_model_generator/normalization.py",
    "src/am_print_executor/manifold_repair.py",
    "src/am_print_executor/m3_printability_optimizer.py",
]


def emit_and_exit(report: dict, code: int) -> None:
    out_dir = report.get("_report_dir")
    if out_dir:
        out_path = Path(out_dir) / "resume_flat_base_acceptance.json"
        safe_report = dict(report)
        safe_report.pop("_report_dir", None)
        out_path.write_text(
            json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"REPORT={out_path}")
    report.pop("_report_dir", None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(code)


def unique_paths(paths):
    seen = set()
    result = []
    for p in paths:
        p = Path(p).resolve()
        key = str(p).lower()
        if key not in seen and p.is_file():
            seen.add(key)
            result.append(p)
    return result


def find_latest_candidate() -> Path:
    if not ACCEPTANCE_ROOT.is_dir():
        raise RuntimeError(f"Acceptance root missing: {ACCEPTANCE_ROOT}")

    candidates = [
        p for p in ACCEPTANCE_ROOT.rglob("*.flat_base.stl")
        if p.is_file()
    ]
    if not candidates:
        candidates = [
            p for p in ACCEPTANCE_ROOT.rglob("*flat_base*.stl")
            if p.is_file()
        ]
    if not candidates:
        raise RuntimeError(
            "No flat-base STL candidate found under outputs\\flat_base_acceptance"
        )

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0].resolve()

    # Never silently reuse the known older acceptance artifact which had
    # 26 dangerous layers. That would violate this regression task.
    if KNOWN_OLD_FAILED_RUN.lower() in str(latest).lower():
        raise RuntimeError(
            "NO_NEW_FINAL_CANDIDATE: latest file is the known older "
            f"Printability BLOCK run: {latest}"
        )
    return latest


def choose_exactly_one(label: str, matches: list[Path]) -> Path:
    matches = unique_paths(matches)
    if len(matches) != 1:
        raise RuntimeError(
            f"PROFILE_DISCOVERY_BLOCK:{label}: expected exactly one, "
            f"found {len(matches)}: {[str(p) for p in matches]}"
        )
    return matches[0]


def discover_profiles(run_dir: Path):
    cli_dir = run_dir / ".nl_am_bambu_cli_profiles"
    profile_dir = run_dir / "profiles"

    machine = choose_exactly_one(
        "machine",
        list(cli_dir.glob("machine_*.json")),
    )

    process_priority = [
        profile_dir / "voxel_optimizer_normal_auto_60.json",
        profile_dir / "optimizer_normal_auto_60.json",
    ]
    process = next((p.resolve() for p in process_priority if p.is_file()), None)
    if process is None:
        wildcard = unique_paths(
            list(profile_dir.glob("*normal_auto_60*.json"))
        )
        if len(wildcard) == 1:
            process = wildcard[0]
        elif len(wildcard) > 1:
            # Prefer a profile explicitly associated with the voxel-repaired
            # final candidate, but refuse if that still is not unique.
            voxel = [p for p in wildcard if "voxel" in p.name.lower()]
            process = choose_exactly_one("process", voxel)
        else:
            fallback = unique_paths(
                list(profile_dir.glob("*conservative*support*.json"))
            )
            process = choose_exactly_one("process", fallback)

    filament_candidates = []
    for base in (cli_dir, profile_dir):
        if not base.is_dir():
            continue
        filament_candidates += list(base.glob("filament_*.json"))
        filament_candidates += list(base.glob("*filament*.json"))

    filaments = unique_paths(filament_candidates)

    # If the run kept filament JSON elsewhere, do not guess from another
    # acceptance run. Stop and report local candidates instead.
    if not filaments:
        raise RuntimeError(
            "PROFILE_DISCOVERY_BLOCK:filament: no run-local filament JSON found "
            f"under {cli_dir} or {profile_dir}"
        )

    return machine, Path(process).resolve(), filaments


def safe_topology(path: Path):
    try:
        return inspect_mesh_topology(path)
    except Exception as exc:
        return {
            "status": "error",
            "floating_component_count": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


fixture = (
    ROOT
    / "tests"
    / "fixtures"
    / "flat_base_regression_current_mouse"
    / "current_mouse_curved_base.glb"
)

report = {
    "task": "FIX_PRINTABILITY_REGRESSION_FLAT_BASE",
    "focused_tests": os.environ.get("NL_AM_FOCUSED_TESTS"),
    "regression_root_cause": (
        "manifold_repair._voxel_solidify Stage B globally rebuilds the surface "
        "through voxelized -> fill -> marching_cubes. _restore_reference_bounds "
        "restores extents/center but not bottom-plane coplanarity."
    ),
    "fix": (
        "Preserve an already valid base; otherwise minimally Z-clip only the "
        "lowest region and cap it with a true plane, then re-run FLAT_BASE_GATE. "
        "The later local smoothing patch is XY-only on the newly created cap "
        "boundary and never changes base Z."
    ),
}

if fixture.is_file():
    report["before_current_regression_fixture"] = {
        "path": str(fixture),
        "flat_base": inspect_flat_printing_base(fixture),
        "topology": safe_topology(fixture),
    }
else:
    report["before_current_regression_fixture"] = {
        "path": str(fixture),
        "error": "fixture_missing",
    }

try:
    candidate = find_latest_candidate()
except Exception as exc:
    report["status"] = "BLOCK"
    report["blocker"] = f"{type(exc).__name__}: {exc}"
    report["_report_dir"] = str(ACCEPTANCE_ROOT)
    emit_and_exit(report, 2)

run_dir = candidate.parent.resolve()
report["_report_dir"] = str(run_dir)
report["candidate"] = {
    "path": str(candidate),
    "mtime": datetime.fromtimestamp(candidate.stat().st_mtime).isoformat(),
}

flat = inspect_flat_printing_base(candidate)
topology = safe_topology(candidate)

report["after_geometry"] = {
    "bed_contact_area_mm2": flat.get("bed_contact_area_mm2"),
    "largest_contact_patch_area_mm2": flat.get(
        "largest_contact_patch_area_mm2"
    ),
    "contact_patch_count": flat.get("contact_patch_count"),
    "fitted_base_plane": flat.get("fitted_base_plane"),
    "max_plane_deviation_mm": flat.get("max_plane_deviation_mm"),
    "base_flatness": (
        "PASS" if flat.get("base_flatness_passed") else "FAIL"
    ),
    "floating_island": (
        "PASS"
        if topology.get("status") == "pass"
        and topology.get("floating_component_count") == 0
        else "FAIL"
    ),
    "floating_component_count": topology.get("floating_component_count"),
    "final_extents_mm": flat.get("final_extents_mm"),
    "flat_base_full": flat,
    "topology_full": topology,
}

if not flat.get("base_flatness_passed"):
    report["status"] = "BLOCK"
    report["blocker"] = "FLAT_BASE_GATE"
    report["FLAT_BASE_GATE"] = "BLOCK"
    report["PRINTABILITY_GATE"] = "NOT_RUN"
    report["BAMBU_AUTO_ORIENT"] = "NOT_RUN"
    emit_and_exit(report, 2)

if (
    topology.get("status") != "pass"
    or topology.get("floating_component_count") != 0
):
    report["status"] = "BLOCK"
    report["blocker"] = "FLOATING_TOPOLOGY_GATE"
    report["FLAT_BASE_GATE"] = "PASS"
    report["PRINTABILITY_GATE"] = "NOT_RUN"
    report["BAMBU_AUTO_ORIENT"] = "NOT_RUN"
    emit_and_exit(report, 2)

report["FLAT_BASE_GATE"] = "PASS"

if not STUDIO.is_file():
    report["status"] = "BLOCK"
    report["blocker"] = f"Bambu Studio missing: {STUDIO}"
    report["PRINTABILITY_GATE"] = "NOT_RUN"
    report["BAMBU_AUTO_ORIENT"] = "NOT_RUN"
    emit_and_exit(report, 2)

try:
    machine, process, filaments = discover_profiles(run_dir)
except Exception as exc:
    report["status"] = "BLOCK"
    report["blocker"] = f"{type(exc).__name__}: {exc}"
    report["PRINTABILITY_GATE"] = "NOT_RUN"
    report["BAMBU_AUTO_ORIENT"] = "NOT_RUN"
    emit_and_exit(report, 2)

report["profiles"] = {
    "machine": str(machine),
    "process": str(process),
    "filaments": [str(p) for p in filaments],
}

artifact = run_dir / "resume_final_support.gcode.3mf"
if artifact.exists():
    artifact.unlink()

settings_arg = ";".join([str(machine), str(process)])
filaments_arg = ";".join(str(p) for p in filaments)

command = [
    str(STUDIO),
    "--arrange", "0",
    "--ensure-on-bed",
    "--slice", "0",
    "--debug", "2",
    "--outputdir", str(run_dir),
    "--export-3mf", artifact.name,
    "--load-settings", settings_arg,
    "--load-filaments", filaments_arg,
    str(candidate),
]

slice_result = run_bambu_cli(
    command,
    expected_outputs=[artifact],
    cwd=run_dir,
    timeout=1800,
)

report["support_slice"] = {
    "success": bool(slice_result.success),
    "raw_exit": slice_result.raw_exit,
    "signed_exit": slice_result.signed_exit,
    "outputs_exist": slice_result.outputs_exist,
    "artifact": str(artifact),
    "stdout_tail": slice_result.stdout[-3000:],
    "stderr_tail": slice_result.stderr[-3000:],
}

if not slice_result.success or not artifact.is_file():
    report["status"] = "BLOCK"
    report["blocker"] = "SUPPORT_SLICE_FAILED"
    report["PRINTABILITY_GATE"] = "BLOCK"
    report["BAMBU_AUTO_ORIENT"] = "NOT_RUN"
    emit_and_exit(report, 2)

try:
    report["artifact_xml_repair"] = repair_bambu_model_settings_xml(artifact)
except Exception as exc:
    report["status"] = "BLOCK"
    report["blocker"] = f"XML_REPAIR_FAILED:{type(exc).__name__}:{exc}"
    report["PRINTABILITY_GATE"] = "BLOCK"
    report["BAMBU_AUTO_ORIENT"] = "NOT_RUN"
    emit_and_exit(report, 2)

printability = inspect_final_gcode_printability(
    artifact,
    geometry_path=candidate,
)
report["printability"] = printability
printability_pass = printability.get("status") == "pass"
report["PRINTABILITY_GATE"] = "PASS" if printability_pass else "BLOCK"

if not printability_pass:
    report["status"] = "BLOCK"
    report["blocker"] = "PRINTABILITY_GATE"
    report["BAMBU_AUTO_ORIENT"] = "NOT_RUN"
    emit_and_exit(report, 2)

auto_project = run_dir / "resume_auto_oriented.project.3mf"
if auto_project.exists():
    auto_project.unlink()

try:
    auto_orient = auto_orient_with_bambu_cli(
        studio_exe=STUDIO,
        output_path=auto_project,
        machine_json=machine,
        process_json=process,
        filament_jsons=filaments,
        source_model=candidate,
        max_attempts=1,   # hard requirement: no retry, therefore no retry sleep
        timeout=600,
    )
    report["auto_orient"] = auto_orient
    auto_pass = auto_orient.get("status") == "auto_orient_complete"
except Exception as exc:
    report["auto_orient"] = {
        "status": "error",
        "error": f"{type(exc).__name__}: {exc}",
    }
    auto_pass = False

report["BAMBU_AUTO_ORIENT"] = "PASS" if auto_pass else "BLOCK"

git_diff = subprocess.run(
    ["git", "diff", "--stat", "--", *FOCUSED_DIFF_PATHS],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=False,
)
report["git_diff_stat"] = git_diff.stdout.strip()

report["status"] = (
    "PASS"
    if (
        report["FLAT_BASE_GATE"] == "PASS"
        and report["PRINTABILITY_GATE"] == "PASS"
        and report["BAMBU_AUTO_ORIENT"] == "PASS"
    )
    else "BLOCK"
)

emit_and_exit(report, 0 if report["status"] == "PASS" else 2)
'@ | & $Py -

$ResumeCode = $LASTEXITCODE

Write-Host ""
Write-Host "=== 4/4 final focused git diff --stat ===" -ForegroundColor Cyan
git diff --stat -- `
    src/am_print_executor/flat_base_gate.py `
    tests/test_flat_base_gate.py `
    src/am_model_generator/normalization.py `
    src/am_print_executor/manifold_repair.py `
    src/am_print_executor/m3_printability_optimizer.py

Write-Host ""
if ($ResumeCode -eq 0) {
    Write-Host "RESUME_ACCEPTANCE = PASS" -ForegroundColor Green
}
else {
    Write-Host "RESUME_ACCEPTANCE = BLOCK" -ForegroundColor Yellow
}
Write-Host "Read resume_flat_base_acceptance.json in the selected latest candidate directory."
exit $ResumeCode
