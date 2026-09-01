from __future__ import annotations

import tempfile
import unittest

from pathlib import Path
from unittest import mock

import numpy as np
import trimesh

from am_model_generator.normalization import scale_mesh_to_target_height
from am_print_executor.flat_base_gate import (
    ensure_flat_printing_base,
    inspect_flat_printing_base,
)
from am_print_executor.gcode_printability_gate import inspect_mesh_topology
from am_print_executor.m3_printability_optimizer import (
    export_orientation_candidates,
)


FIXTURE_DIRECTORY = (
    Path(__file__).parent
    / "fixtures"
    / "flat_base_regression_current_mouse"
)
CURRENT_FAILED_MODEL = FIXTURE_DIRECTORY / "current_mouse_curved_base.glb"


def _load_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(path, force="scene", process=True)
    return loaded.to_mesh() if isinstance(loaded, trimesh.Scene) else loaded


class FlatBaseGateTests(unittest.TestCase):
    def test_small_flat_patch_outside_center_of_mass_is_not_stable(self) -> None:
        body = trimesh.creation.icosphere(subdivisions=3, radius=10)
        body.apply_translation((0, 0, 10))
        tiny_foot = trimesh.creation.box(extents=(3, 3, 8))
        tiny_foot.apply_translation((5, 0, 4))
        source = trimesh.boolean.union([body, tiny_foot], engine="manifold")
        report = inspect_flat_printing_base(source)
        self.assertFalse(report["base_flatness_passed"])
        self.assertIn("center_of_mass_outside_support_polygon", report["blockers"])

    def test_frozen_mouse_is_not_a_stable_flat_base(self) -> None:
        path = Path(__file__).resolve().parents[1] / "outputs/flat_base_acceptance/AUTO-20260825-143340-FLATBASE-RESUME/current_mouse.repaired.bed_centered.flat_base.stl"
        if not path.exists():
            self.skipTest("local diagnostic artifact not present")
        report = inspect_flat_printing_base(path)
        self.assertFalse(report["base_flatness_passed"])
        self.assertIn("center_of_mass_outside_support_polygon", report["blockers"])

    def test_integrated_foundation_is_planar_and_stable(self) -> None:
        from am_print_executor.printable_foundation import add_printable_foundation
        source = _load_mesh(CURRENT_FAILED_MODEL)
        vertices = source.vertices.copy()
        candidate, action = add_printable_foundation(source)
        report = inspect_flat_printing_base(candidate)
        self.assertTrue(report["base_flatness_passed"], report)
        self.assertGreater(report["bed_contact_area_mm2"], 100)
        self.assertGreater(report["stability"]["stability_margin_mm"], 1)
        self.assertLess(report["base_height_range_mm"], .001)
        self.assertAlmostEqual(candidate.extents[2], source.extents[2], places=5)
        np.testing.assert_array_equal(source.vertices, vertices)

    def test_current_real_curved_base_is_minimally_planarized(self) -> None:
        source = _load_mesh(CURRENT_FAILED_MODEL)
        before = inspect_flat_printing_base(source)

        repaired, result = scale_mesh_to_target_height(source, 30.0)
        after = inspect_flat_printing_base(repaired)

        self.assertEqual(before["status"], "block")
        self.assertLess(
            before["largest_contact_patch_area_mm2"],
            before["required_contact_area_mm2"],
        )
        self.assertTrue(result["flat_base_repair"]["applied"])
        self.assertLessEqual(
            result["flat_base_repair"]["clip_depth_mm"],
            1.5,
        )
        self.assertEqual(after["status"], "pass")
        self.assertGreaterEqual(
            after["largest_contact_patch_area_mm2"],
            after["required_contact_area_mm2"],
        )
        self.assertLessEqual(after["max_plane_deviation_mm"], 0.15)
        self.assertTrue(repaired.is_watertight)
        self.assertAlmostEqual(float(repaired.extents[2]), 30.0, places=6)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "repaired.stl"
            repaired.export(path)
            topology = inspect_mesh_topology(path)
        self.assertEqual(topology["status"], "pass")
        self.assertEqual(topology["floating_component_count"], 0)

    def test_existing_flat_base_is_preserved_exactly(self) -> None:
        source = trimesh.creation.box(extents=(20.0, 16.0, 30.0))
        source.apply_translation((0.0, 0.0, 15.0))
        vertices_before = np.asarray(source.vertices).copy()

        preserved, result = ensure_flat_printing_base(source)

        self.assertFalse(result["applied"])
        self.assertEqual(result["status"], "preserved")
        self.assertTrue(result["after"]["base_flatness_passed"])
        np.testing.assert_allclose(preserved.vertices, vertices_before)

    def test_curved_bottom_is_blocked_by_flat_base_gate(self) -> None:
        sphere = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
        sphere.apply_translation((0.0, 0.0, 10.0))

        result = inspect_flat_printing_base(sphere)

        self.assertEqual(result["status"], "block")
        self.assertFalse(result["base_flatness_passed"])
        self.assertTrue(
            any(
                blocker.startswith("insufficient_bed_contact_area")
                or blocker == "flat_base_contact_face_required"
                for blocker in result["blockers"]
            )
        )

    def test_fragmented_multi_point_contact_is_blocked(self) -> None:
        pieces: list[trimesh.Trimesh] = []
        for x in (-8.0, 8.0):
            for y in (-8.0, 8.0):
                foot = trimesh.creation.box(extents=(2.0, 2.0, 5.0))
                foot.apply_translation((x, y, 2.5))
                pieces.append(foot)
        bridge = trimesh.creation.box(extents=(20.0, 20.0, 2.0))
        bridge.apply_translation((0.0, 0.0, 6.0))
        pieces.append(bridge)
        fragmented = trimesh.util.concatenate(pieces)

        result = inspect_flat_printing_base(fragmented)

        self.assertEqual(result["status"], "block")
        self.assertEqual(result["contact_patch_count"], 4)
        self.assertTrue(
            any(
                blocker.startswith("fragmented_bed_contact")
                for blocker in result["blockers"]
            )
        )

    def test_orientation_search_rejects_candidate_that_loses_flat_base(
        self,
    ) -> None:
        cone = trimesh.creation.cone(radius=10.0, height=30.0, sections=64)
        cone.apply_translation((0.0, 0.0, 15.0))
        identity = np.eye(4)
        side = trimesh.transformations.rotation_matrix(
            np.pi / 2.0,
            (1.0, 0.0, 0.0),
        )
        orientation_report = {
            "top_candidates": [
                {
                    "transform": identity.tolist(),
                    "source": "protected_base",
                    "stable_probability": 1.0,
                    "score": 1.0,
                    "overhang_area_mm2": 0.0,
                },
                {
                    "transform": side.tolist(),
                    "source": "curved_side",
                    "stable_probability": 0.5,
                    "score": 0.5,
                    "overhang_area_mm2": 10.0,
                },
            ]
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "cone.stl"
            cone.export(source)
            with mock.patch(
                "am_print_executor.m3_printability_optimizer.orient_for_printing",
                return_value=orientation_report,
            ):
                candidates = export_orientation_candidates(
                    input_stl=source,
                    work_dir=root / "candidates",
                    top_n=2,
                )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "protected_base")
        self.assertTrue(candidates[0]["flat_base"]["base_flatness_passed"])


if __name__ == "__main__":
    unittest.main()
