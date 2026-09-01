from pathlib import Path
import json
import trimesh
from am_print_executor.geometry_fidelity_gate import normalized_boolean_copy, mesh_validation
root=Path(__file__).resolve().parents[1]
paths=[root/'tests/fixtures/flat_base_regression_current_mouse/current_mouse_curved_base.glb',
       root/'outputs/automatic_jobs/AUTO-20260824-161128-9EA75FD9/m2/M2-4E07E6303C05/raw_model.glb']
for path in paths:
    scene=trimesh.load_scene(path,process=False)
    mesh=normalized_boolean_copy(scene.to_mesh())
    print(json.dumps({'path':str(path),'validation':mesh_validation(mesh),'scene_metadata':scene.metadata,'mesh_metadata':mesh.metadata},ensure_ascii=False),flush=True)
