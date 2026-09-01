from pathlib import Path
import json
import argparse
import trimesh
from am_print_executor.model_buttresses import add_model_buttresses,individual_defects

p=argparse.ArgumentParser()
p.add_argument('slice_directory',type=Path)
p.add_argument('output',type=Path)
a=p.parse_args()
source=a.slice_directory/'candidate.stl'
artifact=a.slice_directory/'candidate.gcode.3mf'
gate=json.loads((a.slice_directory/'gate.json').read_text(encoding='utf-8'))
mesh=trimesh.load(source,force='mesh',process=True)
observations=individual_defects(gate,artifact)
print('DEFECTS',len(observations),flush=True)
original_union=trimesh.boolean.union
def inspected_union(meshes,**kwargs):
    result=original_union(meshes,**kwargs)
    if len(meshes)>2:
        print('PIECES',[(len(m.faces),m.volume) for m in result.split(only_watertight=False)],flush=True)
        for i,part in enumerate(meshes[1:]):
            overlap=trimesh.boolean.intersection([meshes[0],part],engine='manifold')
            if overlap.volume<.001: print('UNANCHORED',i,overlap.volume,part.bounds.tolist(),flush=True)
    return result
trimesh.boolean.union=inspected_union
candidate,action=add_model_buttresses(mesh,observations)
a.output.mkdir(parents=True,exist_ok=False)
candidate.export(a.output/'candidate.stl')
(a.output/'action.json').write_text(json.dumps(action,indent=2),encoding='utf-8')
print('PASS',action['added_volume_ratio'],flush=True)
