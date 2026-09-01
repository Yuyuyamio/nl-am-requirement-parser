"""Restore glTF anatomical up before any base construction or slicing."""
from pathlib import Path
from datetime import datetime
import json
import hashlib
from am_model_generator.coordinate_frame import load_print_scene, GLTF_TO_PRINT
from am_print_executor.geometry_fidelity_gate import normalized_boolean_copy, mesh_validation
from am_print_executor.verified_print_preparation import prepare_verified_print

root=Path(__file__).resolve().parents[1]
source=root/'tests/fixtures/flat_base_regression_current_mouse/current_mouse_curved_base.glb'
out=root/'outputs/semantic_pose_repair'/datetime.now().strftime('upright_%Y%m%d_%H%M%S')
out.mkdir(parents=True,exist_ok=False)
mesh=normalized_boolean_copy(load_print_scene(source).to_mesh())
assert mesh_validation(mesh)['valid'] and mesh.body_count == 1
scale=30./float(mesh.extents[2])
mesh.apply_scale(scale)
mesh.apply_translation(-mesh.bounds[0]*[0,0,1])
stl=out/'upright_source.stl'
mesh.export(stl)
(out/'pose_source.json').write_text(json.dumps({'source':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
    'source_up_axis':'+Y','print_up_axis':'+Z','transform':GLTF_TO_PRINT.tolist(),'scale':scale,
    'visual_review':'head above torso; ears vertical; rump and rear feet down; face forward; tail low',
    'target_height_mm':30.,'status':'anatomical_pose_reviewed'},indent=2),encoding='utf-8')
print('UPRIGHT_SOURCE',stl,flush=True)
result=prepare_verified_print(stl,out/'upright_mouse.gcode.3mf',allow_structural_changes=True)
print(json.dumps({k:result[k] for k in ('status','preparation_directory','geometry_path')},indent=2),flush=True)
