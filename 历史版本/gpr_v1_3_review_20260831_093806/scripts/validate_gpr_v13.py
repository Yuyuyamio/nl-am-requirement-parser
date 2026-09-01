"""V1.3 acceptance against existing Bambu artifacts. Never invokes a slicer."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability, inspect_mesh_topology
from am_print_executor.general_printability_geometry_repair import (
    build_boolean_union_candidate, build_printability_repair_request, plan_geometry_repair_candidates,
)


ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-cache", type=Path, help="Reuse verified read-only Gate reports from an earlier validation")
    args = parser.parse_args()
    root = ROOT / "outputs/gpr_v1_3_validation" / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    root.mkdir(parents=True, exist_ok=False)
    preflight = ROOT / "outputs/printability_strategy_benchmark_v1/preflight_20260826_162302"
    artifacts = ROOT / "outputs/printability_strategy_benchmark_v1/run_20260826_170812"
    gate_hashes = {name: sha(ROOT / "src/am_print_executor" / name) for name in
                   ("gcode_printability_gate.py", "gcode_support_continuity.py")}
    positives = {"control_block", "self_support_cone", "connected_cantilever", "bridge_two_piers", "base_recessed_hole"}
    samples = ["control_block", "self_support_cone", "connected_cantilever", "bridge_two_piers", "base_recessed_hole",
               "true_floating_island", "organic_head_on_neck", "real_current_kitten", "real_current_mouse"]
    report = {"schema_version": "gpr-v1.3", "status": "blocked", "slicing_runs": 0, "auto_orient_runs": 0,
              "maximum_candidate_attempts_per_sample": 3, "gate_code_sha256": gate_hashes,
              "samples": {}, "acceptance": {}}
    fixture_manifest = json.loads((ROOT / "tests/fixtures/printability_strategy_benchmark_v1/benchmark_manifest.json").read_text(encoding="utf-8"))
    fixture_hashes = {row["path"]: sha(ROOT / row["path"]) for row in fixture_manifest["samples"]}
    for name in samples:
        print(f"START {name}", flush=True)
        source = preflight / "canonical_geometry" / f"{name}.stl"
        record = {"source_geometry": str(source), "source_sha_before": sha(source),
                  "topology": inspect_mesh_topology(source), "flat_base": inspect_flat_printing_base(source),
                  "plans": [], "candidates": []}
        report["samples"][name] = record
        if record["topology"]["status"] != "pass":
            record["route"] = "semantic_regeneration_required"
        else:
            artifact = artifacts / name / "bambu_normal_auto_support/output.gcode.3mf"
            cache_identity = {"source_sha256": sha(source), "artifact_sha256": sha(artifact), "gate_code_sha256": gate_hashes}
            cached_path = args.gate_cache / name / "gate.json" if args.gate_cache else None
            cached = json.loads(cached_path.read_text(encoding="utf-8")) if cached_path and cached_path.is_file() else None
            gate = cached["gate"] if cached and cached["identity"] == cache_identity else inspect_final_gcode_printability(artifact, geometry_path=source)
            save(root / name / "gate.json", {"identity": cache_identity, "gate": gate})
            record["gate_status"] = gate["status"]
            record["gate_summary"] = {key: gate.get(key) for key in
                                      ("dangerous_layer_count", "total_bad_area_mm2", "worst_bad_area_mm2")}
            request = build_printability_repair_request(source_geometry=source, gate_report=gate,
                                                       flat_base_report=record["flat_base"])
            save(root / name / "request.json", request.to_dict())
            plans = plan_geometry_repair_candidates(request, maximum_clusters=12)
            record["plans"] = plans
            selected = [plan for plan in plans if plan["status"] == "candidate"][:request.repair_budget.maximum_candidates_per_round]
            for plan in selected:
                print(f"  UNION {name} {plan['cluster_id']} {plan['strength']}", flush=True)
                result = build_boolean_union_candidate(request, plan, output_directory=root / name / "candidates")
                record["candidates"].append(result)
                print(f"  RESULT {result['status']} {result['blockers']}", flush=True)
            record["geometry_safe_candidate_count"] = sum(row["status"] == "pass" for row in record["candidates"])
            record["artifact_sha_unchanged"] = sha(artifact) == cache_identity["artifact_sha256"]
            record["route"] = "preserve" if gate["status"] == "pass" else "geometry_candidate_validation_only"
        record["source_sha_after"] = sha(source)
        print(f"DONE {name} gate={record.get('gate_status', 'pre-strategy-block')} safe={record.get('geometry_safe_candidate_count', 0)}", flush=True)
        save(root / "validation.json", report)
    rows = report["samples"]
    frozen_mouse = ROOT / "outputs/flat_base_acceptance/AUTO-20260825-143340-FLATBASE-RESUME/current_mouse.repaired.bed_centered.flat_base.stl"
    report["frozen_mouse_matches_benchmark_source"] = sha(frozen_mouse) == rows["real_current_mouse"]["source_sha_before"]
    accept = report["acceptance"]
    accept["positive_samples_preserved_without_candidates"] = all(rows[name]["gate_status"] == "pass" and not rows[name]["plans"] and not rows[name]["candidates"] for name in positives)
    accept["known_bad_kitten_still_blocked"] = rows["real_current_kitten"]["gate_status"] == "blocked"
    accept["floating_island_pre_strategy_blocked"] = rows["true_floating_island"]["route"] == "semantic_regeneration_required"
    accept["mouse_has_geometry_safe_candidate"] = rows["real_current_mouse"].get("geometry_safe_candidate_count", 0) > 0
    accept["non_mouse_blocked_sample_has_geometry_safe_candidate"] = any(rows[name].get("geometry_safe_candidate_count", 0) > 0 for name in ("organic_head_on_neck", "real_current_kitten"))
    accept["source_sha_unchanged"] = all(row["source_sha_before"] == row["source_sha_after"] for row in rows.values())
    accept["benchmark_fixtures_sha_unchanged"] = all(sha(ROOT / path) == digest for path, digest in fixture_hashes.items())
    accept["all_input_flat_bases_pass"] = all(row["flat_base"]["status"] == "pass" for row in rows.values())
    accept["frozen_mouse_is_exact_source"] = report["frozen_mouse_matches_benchmark_source"]
    accept["gcode_artifacts_unchanged"] = all(row.get("artifact_sha_unchanged", True) for row in rows.values())
    accept["strict_gate_code_unchanged"] = all(sha(ROOT / "src/am_print_executor" / name) == digest for name, digest in gate_hashes.items())
    report["status"] = "pass" if all(accept.values()) else "blocked"
    report["next_gate"] = "GPR-V1.4_RESLICE_AND_PRINTABILITY_FEEDBACK" if report["status"] == "pass" else "GPR-V1.3_REVIEW"
    save(root / "validation.json", report)
    print(json.dumps({"report": str(root / "validation.json"), "status": report["status"], "acceptance": accept}, indent=2), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
