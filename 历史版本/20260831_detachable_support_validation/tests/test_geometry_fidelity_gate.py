from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import trimesh

from am_print_executor.general_printability_geometry_repair import RepairBudget
from am_print_executor.geometry_fidelity_gate import (
    inspect_geometry_fidelity, mesh_validation, normalized_boolean_copy,
    _nearest_surface_distance,
)


def box(extents=(10, 10, 10), center=(0, 0, 5)):
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(center)
    return mesh


class GeometryFidelityTests(unittest.TestCase):
    def setUp(self):
        self.source = box()
        self.envelope = box((1, 1, 1), (4.75, 0, 5))
        self.candidate = trimesh.boolean.union([self.source, self.envelope], engine="manifold")

    def inspect(self, **changes):
        args = dict(source=self.source, candidate=self.candidate, repair_volume=self.envelope,
                    budget=RepairBudget(), protected_base_clearance_z_mm=0.6)
        args.update(changes)
        with patch("trimesh.boolean.difference", side_effect=AssertionError("no subtraction")):
            return inspect_geometry_fidelity(**args)

    def test_small_additive_union_passes_and_inputs_are_unchanged(self):
        before = [mesh.vertices.copy() for mesh in (self.source, self.candidate, self.envelope)]
        report = self.inspect()
        self.assertEqual(report["status"], "pass", report)
        self.assertAlmostEqual(report["volume"]["added_mm3"], 0.25, places=5)
        self.assertLess(report["volume"]["removed_ratio"], 1e-7)
        self.assertLessEqual(report["surface_displacement"]["maximum_upper_bound_mm"], 3)
        for mesh, vertices in zip((self.source, self.candidate, self.envelope), before):
            np.testing.assert_array_equal(mesh.vertices, vertices)

    def test_original_five_percent_fixture_still_blocks(self):
        envelope = box((1, 1, 1), (5, 0, 5))
        candidate = trimesh.boolean.union([self.source, envelope], engine="manifold")
        report = self.inspect(candidate=candidate, repair_volume=envelope)
        self.assertIn("bbox_change_within_budget", report["blockers"])

    def test_empty_mesh_is_blocked_without_indexing_extents(self):
        self.assertFalse(mesh_validation(trimesh.Trimesh())["valid"])
        self.assertEqual(self.inspect(candidate=trimesh.Trimesh())["status"], "blocked")

    def test_open_mesh_and_bad_winding_are_not_repaired_by_gate(self):
        for candidate in (trimesh.Trimesh(vertices=self.candidate.vertices, faces=self.candidate.faces[:-1], process=False),
                          self.candidate.copy()):
            if len(candidate.faces) == len(self.candidate.faces):
                candidate.faces[0] = candidate.faces[0][::-1]
            self.assertIn("candidate_valid", self.inspect(candidate=candidate)["blockers"])

    def test_stl_weld_is_exact_and_does_not_move_vertices(self):
        raw = trimesh.Trimesh(vertices=self.source.triangles.reshape((-1, 3)),
                              faces=np.arange(36).reshape((-1, 3)), process=False)
        normalized = normalized_boolean_copy(raw)
        self.assertEqual(len(raw.vertices), 36)
        self.assertFalse(raw.is_watertight)
        self.assertTrue(normalized.is_watertight)
        np.testing.assert_array_equal(np.unique(raw.vertices, axis=0), normalized.vertices)
        perturbed = raw.copy()
        perturbed.vertices[0, 0] += 1e-9
        self.assertFalse(normalized_boolean_copy(perturbed).is_watertight)

    def test_equal_net_volume_cannot_hide_removed_material(self):
        translated = self.source.copy()
        translated.apply_translation((0.2, 0, 0))
        report = self.inspect(candidate=translated)
        self.assertAlmostEqual(report["volume"]["removed_ratio"], 0.02, places=5)
        self.assertIn("removed_volume_within_budget", report["blockers"])

    def test_disconnected_addition_is_blocked(self):
        remote = box((0.1, 0.1, 0.1), (5.2, 0, 5))
        candidate = trimesh.boolean.union([self.source, remote], engine="manifold")
        report = self.inspect(candidate=candidate, repair_volume=remote)
        self.assertIn("component_count_preserved", report["blockers"])
        self.assertIn("volumetric_source_anchor", report["blockers"])

    def test_protected_base_addition_is_blocked_even_with_flat_contact(self):
        envelope = box((1, 1, 0.2), (4.75, 0, 0.1))
        candidate = trimesh.boolean.union([self.source, envelope], engine="manifold")
        report = self.inspect(candidate=candidate, repair_volume=envelope)
        self.assertEqual(report["candidate_flat_base"]["status"], "pass")
        self.assertIn("envelope_above_protected_base", report["blockers"])
        self.assertIn("protected_base_unchanged", report["blockers"])

    def test_critical_regions_are_protected_and_unknown_regions_fail_closed(self):
        for region in ({"bounds_mm": [[4.9, -0.2, 4.9], [5.3, 0.2, 5.1]]}, {"kind": "recess"}):
            self.assertIn("critical_regions_preserved", self.inspect(critical_regions=(region,))["blockers"])
        far = {"bounds_mm": [[-2, -2, 2], [-1, -1, 3]]}
        self.assertEqual(self.inspect(critical_regions=(far,))["status"], "pass")

    def test_actual_surface_distance_checks_triangle_interiors(self):
        points = np.array([[0, 0, 10.25], [5.25, 0, 5], [0, 0, 5]])
        np.testing.assert_allclose(_nearest_surface_distance(self.source, points), [.25, .25, 5])

    def test_missing_or_false_addition_is_rejected(self):
        self.assertIn("actual_addition", self.inspect(candidate=self.source)["blockers"])
        other_envelope = box((1, 1, 1), (-4.75, 0, 5))
        self.assertIn("addition_confined_to_envelope", self.inspect(repair_volume=other_envelope)["blockers"])

    def test_large_interior_fill_cannot_hide_behind_unchanged_bbox(self):
        source = trimesh.boolean.union([
            box((10, 10, 1), (0, 0, .5)), box((2, 10, 9), (-4, 0, 5.5)),
            box((2, 10, 9), (4, 0, 5.5)), box((6, 2, 9), (0, 4, 5.5)),
        ], engine="manifold")
        envelope = box((6.2, 8, 8), (0, 0, 5))
        candidate = trimesh.boolean.union([source, envelope], engine="manifold")
        report = self.inspect(source=source, candidate=candidate, repair_volume=envelope)
        self.assertTrue(report["checks"]["bbox_change_within_budget"])
        self.assertIn("added_volume_within_budget", report["blockers"])

    def test_height_limit_is_stricter_than_bbox_limit(self):
        envelope = box((1, 1, 1), (0, 0, 9.6))
        candidate = trimesh.boolean.union([self.source, envelope], engine="manifold")
        report = self.inspect(candidate=candidate, repair_volume=envelope)
        self.assertTrue(report["checks"]["bbox_change_within_budget"])
        self.assertIn("height_change_within_budget", report["blockers"])

    def test_nonfinite_input_fails_closed(self):
        candidate = self.candidate.copy()
        candidate.vertices[0, 0] = np.nan
        self.assertEqual(self.inspect(candidate=candidate)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
