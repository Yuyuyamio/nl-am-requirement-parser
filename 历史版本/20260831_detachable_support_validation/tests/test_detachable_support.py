from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import trimesh

from am_print_executor.detachable_support import detachable_process, inspect_detachable_support
from am_print_executor.gcode_support_continuity import parse_extrusion_segments


def block(size, center):
    m = trimesh.creation.box(size)
    m.apply_translation(center)
    return m


class DetachableSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'model.stl'
        self.artifact = self.root / 'slice.3mf'
        self.mesh = trimesh.boolean.union([block((20,20,1),(0,0,.5)),
            block((2,2,5),(0,0,3)),block((8,2,1),(3,0,5.5))],engine='manifold')
        self.settings, _ = detachable_process({'layer_height':'.2','outer_wall_line_width':'.42'}, .4)
        self.settings.update(nozzle_diameter=['.4'],extruder_offset=['0x0'])

    def write(self, *, model_z=5.12, support_z=4.76, support_y=0, metadata=True, support=True):
        self.mesh.export(self.source)
        vertices = ''.join(f'<vertex x="{v[0]}" y="{v[1]}" z="{v[2]}"/>' for v in self.mesh.vertices)
        faces = ''.join(f'<triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>' for f in self.mesh.faces)
        xml = f'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources><object id="1" type="model"><mesh><vertices>{vertices}</vertices><triangles>{faces}</triangles></mesh></object></resources><build><item objectid="1"/></build></model>'
        code = 'G90\nM83\n' + ('; LINE_WIDTH: .42\n; LAYER_HEIGHT: .12\n' if metadata else '')
        if support:
            code += f'; FEATURE: Support interface\nG1 X2 Y{support_y} Z{support_z}\nG1 X5 E.1\n'
        code += f'; FEATURE: Outer wall\nG1 X2 Y0 Z{model_z}\nG1 X5 E.1\n'
        with zipfile.ZipFile(self.artifact,'w') as z:
            z.writestr('3D/3dmodel.model',xml)
            z.writestr('Metadata/project_settings.config',json.dumps(self.settings))
            z.writestr('Metadata/plate_1.gcode',code)
        return inspect_detachable_support(self.artifact, geometry_path=self.source)

    def test_measures_positive_contact_clearance(self):
        r = self.write()
        self.assertEqual(r['status'],'pass',r)
        self.assertAlmostEqual(r['minimum_measured_contact_gap_mm'],.24)
        self.assertFalse(r['physical_removal_verified'])
        self.assertTrue(r['physical_removal_test_required'])

    def test_profile_gap_cannot_hide_actual_fused_contact(self):
        r = self.write(model_z=4.88)
        self.assertEqual(r['status'],'blocked')
        self.assertIn('actual_support_contacts_are_fused_or_too_close',r['blockers'])

    def test_lateral_bead_overlap_is_fused(self):
        r = self.write(model_z=4.76,support_y=.3)
        self.assertEqual(r['status'],'blocked')
        self.assertGreater(r['contact_clash_count'],0)

    def test_missing_actual_width_height_fails_closed(self):
        r = self.write(metadata=False)
        self.assertEqual(r['status'],'blocked')
        self.assertIn('missing_or_invalid_actual_bead_dimensions',r['blockers'])

    def test_zero_or_excessive_nominal_gap_is_not_breakaway_profile(self):
        for gap in ['0','2']:
            self.settings['support_top_z_distance']=gap
            self.assertIn('contact_gap_outside_breakaway_design_range',self.write()['blockers'])

    def test_enclosed_support_is_not_claimed_removable(self):
        self.mesh = trimesh.boolean.union([block((20,20,1),(0,0,.5)),
            block((2,12,7),(-1,0,4)),block((2,12,7),(7,0,4)),
            block((6,2,7),(3,-5,4)),block((6,2,7),(3,5,4)),
            block((10,12,1),(3,0,7.5))],engine='manifold')
        r = self.write(model_z=7.12,support_z=6.76)
        self.assertEqual(r['status'],'blocked',r)
        self.assertIn('support_inside_model_hole_requires_redesign_or_manual_review',r['blockers'])

    def test_support_free_model_needs_no_removal_test(self):
        r = self.write(support=False)
        self.assertEqual(r['status'],'pass')
        self.assertFalse(r['physical_removal_test_required'])

    def test_actual_path_dimensions_follow_arcs_and_layer_changes(self):
        code = 'G90\nM83\n; FEATURE: Support\n; LINE_WIDTH: .42\n; LAYER_HEIGHT: .3\nG1 X1 Y0 Z.3\nG3 X-1 Y0 I-1 J0 E.2\n; LAYER_HEIGHT: .12\nG1 X0 E.1\n'
        paths = parse_extrusion_segments(code)
        self.assertTrue(all(s.layer_height_mm == .3 for s in paths[:-1]))
        self.assertEqual(paths[-1].layer_height_mm,.12)
        self.assertTrue(all(s.line_width_mm == .42 for s in paths))

    def test_workflow_default_rechecks_separation_and_blocks_fused_support(self):
        from am_print_automation.workflow import ProductionServices, AutomationConfig
        self.assertEqual(AutomationConfig().support_mode,'detachable')
        with patch('am_print_executor.gcode_printability_gate.inspect_final_gcode_printability',return_value={'status':'pass'}) as gate, \
             patch('am_print_executor.detachable_support.inspect_detachable_support',return_value={'status':'blocked','blockers':['fused']}), \
             patch('am_print_executor.slice_geometry_frame.placed_mesh_from_project',return_value=self.mesh):
            r = ProductionServices(AutomationConfig()).validate_gcode(self.artifact,self.source)
        self.assertEqual(r['status'],'blocked')
        self.assertTrue(gate.call_args.kwargs['credit_removable_support'])
        self.assertIn('fused',r['blockers'])

    def test_mode_and_removal_receipt_are_bound_to_production_job(self):
        from am_print_automation.workflow import _validate_prepared_slice, AutomationConfig
        self.write()
        service = Mock(); service.config = AutomationConfig()
        service.validate_gcode.return_value = {'status':'pass'}
        receipt = {'status':'pass','support_mode':'detachable','model_self_support_required':False,
            'removal':{'status':'pass'},'artifact_sha256':hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
            'geometry_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest()}
        prepared = {'pipeline':'verified_support_orient_reslice_v3','status':'slice_complete',
            'support_mode':'detachable','model_self_support_required':False,'acceptance':receipt}
        self.assertEqual(_validate_prepared_slice(service,prepared,self.artifact,self.source)['status'],'pass')
        receipt['removal']['status']='blocked'
        self.assertEqual(_validate_prepared_slice(service,prepared,self.artifact,self.source)['status'],'blocked')
        prepared.update(support_mode='permanent',model_self_support_required=True)
        receipt.update(support_mode='permanent',model_self_support_required=True)
        self.assertEqual(_validate_prepared_slice(service,prepared,self.artifact,self.source)['status'],'blocked')

    def test_detachable_failure_never_falls_back_to_fused_buttresses(self):
        from types import SimpleNamespace
        from am_print_executor.verified_print_preparation import prepare_verified_print
        self.write()
        process = self.root/'process.json'
        process.write_text(json.dumps(self.settings),encoding='utf-8')
        machine = self.root/'machine.json'
        machine.write_text(json.dumps({'nozzle_diameter':['.4'],'printable_area':['0x0','256x0','256x256','0x256']}),encoding='utf-8')
        profiles = {'machine':machine,'process':process,'filaments':[]}
        current = {'artifact':self.artifact,'geometry':self.source,
            'gate':{'status':'blocked','dangerous_layer_count':1},
            'flat_base':{'base_flatness_passed':True},'removal':{'status':'pass'}}
        with patch('am_print_executor.developer_mode_backend_v1120.discover_bambu_profiles',return_value={'machine':machine,'process':process,'filament':process}), \
             patch('am_print_executor.bambu_profile_resolver.materialize_bambu_cli_profiles',return_value=profiles), \
             patch('am_print_executor.verified_print_preparation.slice_fixed_geometry',return_value=current) as slicer, \
             patch('am_print_executor.general_printability_geometry_repair.build_printability_repair_request',return_value=SimpleNamespace(blocker_clusters=[],coordinate_mapping={})), \
             patch('am_print_executor.local_anchored_growth.build_anchored_growth_envelopes',return_value=([],[])), \
             patch('am_print_executor.model_buttresses.add_model_buttresses') as fused, \
             patch('am_print_executor.developer_mode_backend_v1120.inspect_gcode_3mf',return_value={}):
            result = prepare_verified_print(self.source,self.root/'forbidden.3mf',studio_exe=self.root/'studio.exe')
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['support_mode'],'detachable')
        self.assertEqual(slicer.call_args.kwargs['support_mode'],'detachable')
        fused.assert_not_called()
        self.assertFalse((self.root/'forbidden.3mf').exists())


if __name__ == '__main__': unittest.main()
