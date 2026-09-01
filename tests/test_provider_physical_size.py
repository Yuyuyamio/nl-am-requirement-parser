"""The provider size step must precede, not replace, manufacturing validation."""
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import trimesh

from am_model_generator.contracts import M2ProviderError
from am_model_generator.coordinate_frame import FRAME_KEY, GLTF_TO_PRINT, load_print_scene
from am_model_generator.normalization import export_glb_at_target_height, scale_mesh_to_target_height


class ProviderPhysicalSizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_scene(self, mesh_or_scene, name="source.glb"):
        scene = mesh_or_scene if isinstance(mesh_or_scene, trimesh.Scene) else trimesh.Scene(mesh_or_scene)
        path = self.root / name
        path.write_bytes(trimesh.exchange.gltf.export_glb(scene))
        return path

    def assert_uniform_size_export(self, source, target):
        before_bytes = source.read_bytes()
        before = load_print_scene(source).to_mesh()
        destination = self.root / f"scaled-{target}.glb"
        report = export_glb_at_target_height(source, destination, target)
        after = load_print_scene(destination).to_mesh()
        factor = target / before.extents[2]
        expected = before.vertices * factor
        expected[:, 2] -= expected[:, 2].min()
        np.testing.assert_allclose(after.vertices, expected, atol=2e-5, rtol=1e-6)
        np.testing.assert_array_equal(after.faces, before.faces)
        np.testing.assert_allclose(after.extents, before.extents * factor, atol=2e-5)
        self.assertAlmostEqual(after.bounds[0, 2], 0, places=5)
        self.assertAlmostEqual(after.extents[2], target, places=4)
        self.assertEqual(source.read_bytes(), before_bytes)
        self.assertTrue(report["mesh_validation_required"])
        self.assertFalse(report["printability_verified"])
        self.assertEqual(report["operation"], "uniform_scale_before_mesh_repair")
        return after

    def test_open_mesh_reaches_repair_without_becoming_a_printable_pass(self):
        mesh = trimesh.creation.box(extents=(2, 6, 3))
        mesh.update_faces(np.arange(len(mesh.faces) - 1))
        self.assertFalse(mesh.is_watertight)
        source = self.write_scene(mesh)
        after = self.assert_uniform_size_export(source, 40)
        self.assertFalse(after.is_watertight)
        # The later strict entry point still refuses unrepaired solids.
        with self.assertRaises(M2ProviderError) as error:
            scale_mesh_to_target_height(after, 40)
        self.assertEqual(error.exception.code, "M2_FLAT_BASE_GATE_BLOCK")

    def test_curved_shapes_are_scaled_without_adding_a_foundation(self):
        for shape in (trimesh.creation.icosphere(subdivisions=2), trimesh.creation.capsule(count=[12, 12])):
            source = self.write_scene(shape)
            for target in (5, 40, 160):
                with self.subTest(shape=len(shape.faces), target=target):
                    self.assert_uniform_size_export(source, target)

    def test_rotated_translated_scene_uses_world_up_not_longest_axis(self):
        mesh = trimesh.creation.box(extents=(12, 3, 2))
        scene = trimesh.Scene(mesh)
        transform = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
        transform[:3, 3] = [4, 9, -5]
        scene.apply_transform(transform)
        source = self.write_scene(scene)
        expected = scene.to_mesh()
        expected.apply_transform(GLTF_TO_PRINT)
        np.testing.assert_allclose(load_print_scene(source).to_mesh().extents, expected.extents)
        self.assert_uniform_size_export(source, 37)

    def test_multiple_scene_instances_preserve_topology_until_validation(self):
        scene = trimesh.Scene()
        mesh = trimesh.creation.box(extents=(2, 6, 3))
        scene.add_geometry(mesh, geom_name="shape", node_name="first")
        transform = np.eye(4)
        transform[0, 3] = 8
        scene.graph.update(frame_to="second", matrix=transform, geometry="shape")
        source = self.write_scene(scene)
        after = self.assert_uniform_size_export(source, 40)
        self.assertEqual(len(after.split()), 2)

    def test_legacy_z_up_and_repeated_exports_do_not_rotate_again(self):
        scene = trimesh.Scene(trimesh.creation.box(extents=(9, 4, 12)))
        scene.metadata[FRAME_KEY] = {"world_up": "+Z", "version": 1}
        source = self.write_scene(scene)
        self.assert_uniform_size_export(source, 60)
        first = self.root / "scaled-60.glb"
        second = self.root / "second.glb"
        export_glb_at_target_height(first, second, 60)
        np.testing.assert_allclose(load_print_scene(first).bounds, load_print_scene(second).bounds, atol=1e-5)

    def test_invalid_targets_preserve_existing_destination(self):
        source = self.write_scene(trimesh.creation.box())
        destination = self.root / "existing.glb"
        original = b"previous artifact must survive"
        destination.write_bytes(original)
        for target in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(target=target), self.assertRaises(M2ProviderError):
                export_glb_at_target_height(source, destination, target)
            self.assertEqual(destination.read_bytes(), original)

    def test_roundtrip_failure_preserves_old_artifact_and_removes_candidate(self):
        source = self.write_scene(trimesh.creation.box(extents=(2, 3, 4)))
        destination = self.root / "existing.glb"
        original = b"previous artifact must survive"
        destination.write_bytes(original)
        with patch("am_model_generator.normalization.export_print_glb", return_value=source.read_bytes()):
            with self.assertRaises(M2ProviderError) as error:
                export_glb_at_target_height(source, destination, 40)
        self.assertEqual(error.exception.code, "M2_NORMALIZATION_ROUNDTRIP_HEIGHT_MISMATCH")
        self.assertEqual(destination.read_bytes(), original)
        self.assertFalse(list(self.root.glob(".*verify*")))

    def test_zero_height_and_invalid_model_are_not_accepted(self):
        vertices = [[0, 0, 0], [1, 0, 0], [1, 0, 1]]  # zero glTF Y height
        source = self.write_scene(trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2]], process=False))
        with self.assertRaises(M2ProviderError):
            export_glb_at_target_height(source, self.root / "out.glb", 40)
        source.write_bytes(b"invalid glb")
        with self.assertRaises(M2ProviderError):
            export_glb_at_target_height(source, self.root / "out.glb", 40)
        self.assertFalse((self.root / "out.glb").exists())

    def test_source_destination_conflict_cannot_destroy_source(self):
        source = self.write_scene(trimesh.creation.box())
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaises(M2ProviderError) as error:
            export_glb_at_target_height(source, source, 40)
        self.assertEqual(error.exception.code, "M2_NORMALIZATION_SOURCE_DESTINATION_CONFLICT")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
