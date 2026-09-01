from pathlib import Path
import tempfile
import unittest
import numpy as np
import trimesh
from am_model_generator.coordinate_frame import load_print_scene, export_print_glb, GLTF_TO_PRINT
from am_model_generator.normalization import export_glb_at_target_height
from am_print_executor.printable_orientation import _candidate_transforms
from am_print_executor.semantic_pose_gate import inspect_upright_source_preserved
from test_m4_bambu_auto_orient import _write_project


class SemanticPoseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_asymmetric_gltf_y_height_becomes_print_z_height(self):
        # Width=4, anatomical height=10, front/back depth=6. A Z-only
        # scaler would get both the pose and physical target height wrong.
        raw=trimesh.creation.box(extents=(4,10,6))
        raw.apply_translation((2,5,3))
        source=self.root/'source.glb'
        source.write_bytes(trimesh.exchange.gltf.export_glb(trimesh.Scene(raw)))
        printed=load_print_scene(source).to_mesh()
        np.testing.assert_allclose(printed.extents,[4,6,10])
        np.testing.assert_allclose(printed.bounds[0], [0,-6,0])
        destination=self.root/'normalized.glb'
        report=export_glb_at_target_height(source,destination,30)
        world=trimesh.load_scene(destination,process=False).to_mesh()
        self.assertAlmostEqual(world.extents[1],30)
        self.assertAlmostEqual(world.bounds[0,1],0)
        self.assertAlmostEqual(report['source_height_mm'],10)
        again=load_print_scene(destination).to_mesh()
        np.testing.assert_allclose(again.extents,[12,18,30],atol=1e-5)
        # Reload/export repeatedly: there must be no second 90-degree rotation.
        second=self.root/'second.glb'
        second.write_bytes(export_print_glb(again))
        np.testing.assert_allclose(load_print_scene(second).to_mesh().bounds,again.bounds,atol=1e-5)

    def test_scene_node_rotation_is_composed_before_format_conversion(self):
        mesh=trimesh.creation.box(extents=(2,3,7))
        scene=trimesh.Scene(mesh)
        node=trimesh.transformations.rotation_matrix(np.pi/2,[1,0,0])
        scene.apply_transform(node)
        path=self.root/'nodes.glb'
        path.write_bytes(trimesh.exchange.gltf.export_glb(scene))
        expected=scene.to_mesh()
        expected.apply_transform(GLTF_TO_PRINT)
        np.testing.assert_allclose(load_print_scene(path).to_mesh().bounds,expected.bounds,atol=1e-5)

    def test_stable_pose_search_does_not_flip_an_upright_object(self):
        model=trimesh.creation.box(extents=(4,6,30))
        candidates=_candidate_transforms(model)
        self.assertGreater(len(candidates),0)
        for transform,_,_ in candidates:
            np.testing.assert_allclose(transform[:3,:3] @ [0,0,1], [0,0,1],atol=1e-5)

    def test_auto_orient_can_be_flat_but_anatomically_wrong(self):
        source=self.root/'cube.stl'
        cube=trimesh.creation.box(extents=(20,20,20))
        cube.apply_translation((0,0,10))
        cube.export(source)
        project=self.root/'laid_on_back.3mf'
        _write_project(project,build_transform='1 0 0 0 0 1 0 -1 0 0 0 10')
        result=inspect_upright_source_preserved(source,project)
        self.assertEqual(result['status'],'blocked')
        self.assertAlmostEqual(result['tilt_degrees'],90)
        _write_project(project,build_transform='0 1 0 -1 0 0 0 0 1 128 128 0')
        result=inspect_upright_source_preserved(source,project)
        self.assertEqual(result['status'],'pass')
        self.assertAlmostEqual(result['tilt_degrees'],0)


if __name__=='__main__':
    unittest.main()
