import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.model_buttresses import add_model_buttresses
from am_print_executor.printability_blocker_clustering import cluster_gate_blockers
import test_fdm_layer_printability_gate as gate_helpers


class Helpers:
    square=staticmethod(gate_helpers.FdmLayerPrintabilityGateTests.square)
    write_artifact=staticmethod(gate_helpers.FdmLayerPrintabilityGateTests.write_artifact)
    write_grounded_mesh=staticmethod(gate_helpers.FdmLayerPrintabilityGateTests.write_grounded_mesh)


def block(extents,center):
    mesh=trimesh.creation.box(extents=extents)
    mesh.apply_translation(center)
    return mesh


def defects(z,bounds):
    return cluster_gate_blockers({'status':'blocked','dangerous_layers':[{'z_mm':z,'issues':[
        {'kind':'unsupported_layer_island','area_mm2':1.,'xy_bounds_mm':bounds,'features':['outer wall']}]}]})


class ModelSelfSupportTests(unittest.TestCase):
    def test_removable_support_pass_does_not_mask_model_island(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands=[]
            for z in (.2,.4):
                commands+=Helpers.square(center_x=0,center_y=0,size=10,z=z,feature='Outer wall')
                commands+=Helpers.square(center_x=20,center_y=20,size=2,z=z,feature='Support')
            commands+=Helpers.square(center_x=20,center_y=20,size=2,z=.6,feature='Outer wall')
            path=Helpers.write_artifact(Path(tmp),commands)
            geometry=Helpers.write_grounded_mesh(Path(tmp))
            self.assertEqual(inspect_final_gcode_printability(path,geometry_path=geometry)['status'],'pass')
            result=inspect_final_gcode_printability(path,geometry_path=geometry,credit_removable_support=False)
            self.assertEqual(result['status'],'blocked')
            self.assertGreater(result['unsupported_layer_island_count'],0)

    def test_model_column_passes_with_no_support_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands=[]
            for z in (.2,.4,.6):
                commands+=Helpers.square(center_x=0,center_y=0,size=10,z=z,feature='Outer wall')
            self.assertEqual(inspect_final_gcode_printability(
                Helpers.write_artifact(Path(tmp),commands),geometry_path=Helpers.write_grounded_mesh(Path(tmp)),
                credit_removable_support=False)['status'],'pass')

    def test_generic_gusset_is_additive_deterministic_and_embedded(self):
        # A mechanical ledge, with no organic/anatomical labels.
        source=trimesh.boolean.union([block((20,20,1),(0,0,.5)),
            block((4,8,16),(0,0,8)),block((9,4,2),(3,0,15))],engine='manifold')
        before=source.vertices.copy()
        areas=defects(14.2,[[5.,-1.],[6.,1.]])
        first,a=add_model_buttresses(source,areas)
        second,b=add_model_buttresses(source,areas)
        self.assertTrue(first.is_volume)
        self.assertEqual(first.body_count,1)
        self.assertGreater(first.volume,source.volume)
        self.assertAlmostEqual(trimesh.boolean.intersection([source,first],engine='manifold').volume,source.volume,places=3)
        np.testing.assert_array_equal(source.vertices,before)
        np.testing.assert_allclose(first.bounds,source.bounds)
        self.assertEqual(hashlib.sha256(first.export(file_type='stl')).hexdigest(),hashlib.sha256(second.export(file_type='stl')).hexdigest())
        self.assertEqual(a,b)
        self.assertTrue(all(p['fully_embedded_anchor'] for p in a['parts']))
        self.assertTrue(all(p['slope_xy_per_z']<=.65 for p in a['parts']))

    def test_empty_defects_do_not_modify_a_positive_sample(self):
        source=block((10,10,10),(0,0,5))
        candidate,action=add_model_buttresses(source,[])
        self.assertFalse(action['changed'])
        self.assertAlmostEqual(candidate.volume,source.volume)

    def test_protected_feature_and_detached_geometry_rejected(self):
        source=trimesh.boolean.union([block((20,20,1),(0,0,.5)),
            block((4,8,16),(0,0,8)),block((9,4,2),(3,0,15))],engine='manifold')
        with self.assertRaisesRegex(ValueError,'critical_region'):
            add_model_buttresses(source,defects(14.2,[[5.,-1.],[6.,1.]]),
                critical_regions=({'bounds_mm':[[4,-3,2],[8,3,16]]},))
        detached=trimesh.util.concatenate([source,block((1,1,1),(30,0,20))])
        with self.assertRaisesRegex(ValueError,'one_connected'):
            add_model_buttresses(detached,[])

    def test_production_validation_cannot_accept_old_supported_artifact(self):
        from am_print_automation.workflow import ProductionServices, AutomationConfig
        with patch('am_print_executor.gcode_printability_gate.inspect_final_gcode_printability',return_value={'status':'blocked'}) as gate, \
             patch('am_print_executor.slice_geometry_frame.placed_mesh_from_project',return_value=block((10,10,10),(0,0,5))):
            service=ProductionServices(AutomationConfig())
            result=service.validate_gcode(Path('old.3mf'),Path('old.stl'))
            self.assertEqual(result['status'],'blocked')
            self.assertFalse(gate.call_args.kwargs['credit_removable_support'])

    def test_rejected_preparation_cannot_publish_an_intermediate_passing_slice(self):
        from am_print_automation.workflow import _validate_prepared_slice
        from unittest.mock import Mock
        service=Mock();service.validate_gcode.return_value={'status':'pass'}
        prepared={'pipeline':'model_self_support_orient_reslice_v2','status':'blocked',
                  'terminal_blocker':'auto_orientation_rejected','model_self_support_required':True}
        result=_validate_prepared_slice(service,prepared,Path('unused.3mf'),Path('unused.stl'))
        self.assertEqual(result['status'],'blocked')

    def test_receipt_is_bound_to_actual_geometry_and_gcode(self):
        from am_print_automation.workflow import _validate_prepared_slice
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'final.3mf';artifact.write_bytes(b'accepted-gcode')
            geometry=Path(tmp)/'final.stl';geometry.write_bytes(b'accepted-geometry')
            prepared={'pipeline':'model_self_support_orient_reslice_v2','status':'slice_complete',
                'model_self_support_required':True,'acceptance':{'status':'pass','model_self_support_required':True,
                'artifact_sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),
                'geometry_sha256':hashlib.sha256(geometry.read_bytes()).hexdigest()}}
            service=Mock();service.validate_gcode.return_value={'status':'pass'}
            self.assertEqual(_validate_prepared_slice(service,prepared,artifact,geometry)['status'],'pass')
            geometry.write_bytes(b'different-geometry')
            self.assertEqual(_validate_prepared_slice(service,prepared,artifact,geometry)['status'],'blocked')

    def test_support_fallback_cannot_restart_the_repair_budget(self):
        from am_print_automation.workflow import ProductionServices, AutomationConfig
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);geometry=root/'round_2'/'candidate.stl'
            geometry.parent.mkdir();geometry.write_bytes(b'rejected')
            (root/'preparation.json').write_text('{}',encoding='utf-8')
            (root/'result.json').write_text(json.dumps({'pipeline':'model_self_support_orient_reslice_v2',
                'status':'blocked','geometry_path':str(geometry)}),encoding='utf-8')
            with patch('am_print_executor.verified_print_preparation.prepare_verified_print') as prepare:
                result=ProductionServices(AutomationConfig()).slice_stl_with_support(geometry,root/'forbidden.3mf')
            self.assertEqual(result['status'],'blocked')
            self.assertTrue(result['repair_retry_skipped'])
            prepare.assert_not_called()
            self.assertFalse((root/'forbidden.3mf').exists())


if __name__=='__main__': unittest.main()
