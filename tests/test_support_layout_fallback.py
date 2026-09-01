import json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import trimesh

from am_print_executor.verified_print_preparation import prepare_verified_print
from am_print_executor.detachable_support import detachable_process
from am_print_executor.bambu_auto_orient import BambuAutoOrientError


class SupportLayoutFallbackTests(unittest.TestCase):
    def run_preparation(self, *, layout_passes, removal_passes=True, final_error=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'source.stl'; output=root/'output.gcode.3mf'
            mesh=trimesh.creation.box((10,10,10));mesh.apply_translation((0,0,5));mesh.export(source)
            original=source.read_bytes()
            machine=root/'machine.json';process=root/'process.json';filament=root/'filament.json'
            machine.write_text(json.dumps({'printable_area':['0x0','256x0','256x256','0x256'],'nozzle_diameter':['.4']}))
            process.write_text(json.dumps({'layer_height':'.2','outer_wall_line_width':'.42'}));filament.write_text('{}')
            initial_process,_=detachable_process(json.loads(process.read_text()),.4)
            calls=[]
            def sliced(**kw):
                calls.append(kw)
                directory=kw['directory'];directory.mkdir()
                artifact=directory/'candidate.gcode.3mf';artifact.write_bytes(b'local test artifact')
                geometry=directory/'candidate.stl';geometry.write_bytes(kw['source'].read_bytes())
                is_alternate=len(calls)==2
                gate={'status':'pass' if is_alternate and layout_passes else 'blocked',
                    'unanchored_support_component_count':0 if is_alternate and layout_passes else 1,
                    'dangerous_layer_count':0 if is_alternate and layout_passes else 1,'total_bad_area_mm2':0}
                result={'artifact':artifact,'geometry':geometry,'gate':gate,'flat_base':{'base_flatness_passed':True},
                    'removal':{'status':'pass' if not is_alternate or removal_passes else 'blocked'},
                    'process_json':kw['process_json']}
                (directory/'gate.json').write_text(json.dumps(gate))
                return result
            def finalized(**kw):
                if final_error is not None:
                    raise final_error
                chosen=json.loads(kw['process_json'].read_text())
                self.assertEqual(chosen['support_style'],'grid')
                self.assertEqual(float(chosen['support_base_pattern_spacing']),2)
                for key,value in initial_process.items():
                    if key not in {'support_style','support_base_pattern_spacing'}:
                        self.assertEqual(chosen[key],value,key)
                return {'status':'pass','artifact':str(kw['prior_artifact']),'geometry':str(kw['source']),
                    'flat_base':{'base_flatness_passed':True},'removal':{'status':'pass'},'auto_orient':{'output':{}}}
            with patch('am_print_executor.bambu_profile_resolver.materialize_bambu_cli_profiles',return_value={
                    'machine':machine,'process':process,'filaments':[filament]}), \
                 patch('am_print_executor.developer_mode_backend_v1120.discover_bambu_profiles',return_value={}), \
                 patch('am_print_executor.developer_mode_backend_v1120.inspect_gcode_3mf',side_effect=lambda path:{'path':str(path)}), \
                 patch('am_print_executor.verified_print_preparation.slice_fixed_geometry',side_effect=sliced), \
                 patch('am_print_executor.verified_print_preparation.finalize_verified_geometry',side_effect=finalized) as final, \
                 patch('am_print_executor.general_printability_geometry_repair.build_printability_repair_request',
                       return_value=SimpleNamespace(coordinate_mapping={},blocker_clusters=[])), \
                 patch('am_print_executor.local_anchored_growth.build_anchored_growth_envelopes',return_value=([],[])), \
                 patch('am_print_executor.near_base_gap_fill.build_near_base_gap_fills',return_value=([],[])):
                kwargs=dict(studio_exe=root/'studio.exe',machine_json=machine,
                    process_json=process,filament_jsons=[filament],support_mode='detachable')
                if final_error is not None and not final_error.geometry_rejected:
                    with self.assertRaises(BambuAutoOrientError):prepare_verified_print(source,output,**kwargs)
                    self.assertFalse(output.exists())
                    self.assertEqual(source.read_bytes(),original)
                    self.assertEqual(len(list(root.glob('verified_preparation_*/execution_error.json'))),1)
                    return
                result=prepare_verified_print(source,output,**kwargs)
            self.assertEqual(len(calls),2)  # initial + one layout, never an unbounded retry
            self.assertEqual(source.read_bytes(),original)
            success=layout_passes and removal_passes and final_error is None
            self.assertEqual(result['status'],'slice_complete' if success else 'blocked')
            self.assertEqual(output.exists(),success)
            self.assertEqual(final.call_count,int(layout_passes and removal_passes))

    def test_selected_layout_survives_auto_orient_and_final_slice(self):
        self.run_preparation(layout_passes=True)

    def test_failed_layout_does_not_restart_repair_budget(self):
        self.run_preparation(layout_passes=False)

    def test_fused_support_is_never_accepted_even_if_connectivity_passes(self):
        self.run_preparation(layout_passes=True,removal_passes=False)

    def test_auto_orient_io_error_is_a_software_failure_not_bad_geometry(self):
        self.run_preparation(layout_passes=True,final_error=BambuAutoOrientError('temporary file inaccessible'))

    def test_real_orientation_rejection_remains_geometry_blocked(self):
        self.run_preparation(layout_passes=True,final_error=BambuAutoOrientError('upright pose changed',geometry_rejected=True))
