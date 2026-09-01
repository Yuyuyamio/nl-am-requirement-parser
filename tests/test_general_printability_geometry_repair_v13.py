from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import trimesh

from am_print_executor.general_printability_geometry_repair import (
    build_boolean_union_candidate, build_printability_repair_request,
    plan_geometry_repair_candidates,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "outputs/gpr_v1_3_validation"


class GeneralRepairV13Tests(unittest.TestCase):
    def setUp(self):
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=OUTPUT_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.stl"
        self.mesh = trimesh.creation.box(extents=(10, 10, 10))
        self.mesh.apply_translation((0, 0, 5))
        self.mesh.export(self.source)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        gate = {"status": "blocked", "blockers": ["unsupported_extrusion_region"],
                "policy": {"line_width_mm": 0.4, "cell_mm": 0.4,
                           "measured_layer_height_mm": 0.2, "self_support_xy_mm": 0.26},
                "dangerous_layers": [{"z_mm": 5., "issues": [{
                    "kind": "unsupported_extrusion_region", "area_mm2": 0.08,
                    "xy_bounds_mm": [[4.85, -0.2], [5.05, 0.2]], "features": ["outer wall"]}]}]}
        self.request = build_printability_repair_request(source_geometry=self.source, gate_report=gate)
        self.plan = plan_geometry_repair_candidates(self.request)[0]

    def build(self, request=None, plan=None):
        return build_boolean_union_candidate(request or self.request, plan or self.plan,
                                             output_directory=self.root / "candidates")

    def test_stl_reload_union_fidelity_and_written_artifact_pass(self):
        report = self.build()
        self.assertEqual(report["status"], "pass", report)
        candidate = trimesh.load(report["candidate_geometry"], force="mesh", process=True)
        self.assertTrue(candidate.is_watertight)
        self.assertEqual(candidate.body_count, 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.sha)
        self.assertEqual(report["source_sha_before"], report["source_sha_after"])
        self.assertEqual(report["slicing_runs"], 0)
        self.assertEqual(report["auto_orient_runs"], 0)
        json.dumps(report, allow_nan=False)

    def test_historical_narrow_fixture_has_valid_union_but_exceeds_bbox_budget(self):
        mesh = trimesh.creation.box(extents=(2, 8, 10))
        mesh.apply_translation((0, 0, 5))
        source = self.root / "narrow.stl"
        mesh.export(source)
        # Translate the blocked area to the same 0.15 mm position relative to
        # its narrow wall. Keep the frozen 3% budget.
        gate = {"status": "blocked", "blockers": ["unsupported_extrusion_region"],
                "policy": self.request.gate_policy,
                "dangerous_layers": [{"z_mm": 5., "issues": [{
                    "kind": "unsupported_extrusion_region", "area_mm2": .04,
                    "xy_bounds_mm": [[1.05, -.2], [1.25, .2]], "features": ["outer wall"]}]}]}
        request = build_printability_repair_request(source_geometry=source, gate_report=gate)
        for plan in plan_geometry_repair_candidates(request):
            report = self.build(request, plan)
            self.assertTrue(report["union_topology"]["valid"], report)
            self.assertIn("bbox_change_within_budget", report["blockers"])
            self.assertIsNone(report["candidate_geometry"])

    def test_empty_union_fails_closed_without_writing(self):
        with patch("trimesh.boolean.union", return_value=trimesh.Trimesh()):
            report = self.build()
        self.assertIn("invalid_boolean_union_result", report["blockers"])
        self.assertFalse((self.root / "candidates").exists())

    def test_pass_request_never_repairs_even_with_stale_blockers(self):
        request = replace(self.request, gate_status="pass")
        self.assertEqual(plan_geometry_repair_candidates(request), [])
        with patch("trimesh.boolean.union", side_effect=AssertionError("must not union")):
            self.assertEqual(self.build(request)["status"], "not_needed")

    def test_changed_source_is_rejected(self):
        self.source.write_bytes(self.source.read_bytes() + b"changed")
        self.assertIn("source_sha_changed_since_request", self.build()["blockers"])

    def test_tampered_plan_is_rejected(self):
        plan = dict(self.plan, top_z_mm=7.)
        self.assertIn("plan_does_not_match_source_and_blockers", self.build(plan=plan)["blockers"])

    def test_output_must_be_validation_only_and_never_overwritten(self):
        with self.assertRaises(ValueError):
            build_boolean_union_candidate(self.request, self.plan, output_directory=ROOT / "src")
        first = self.build()
        self.assertEqual(first["status"], "pass", first)
        path = Path(first["candidate_geometry"])
        before = path.read_bytes()
        second = self.build()
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
