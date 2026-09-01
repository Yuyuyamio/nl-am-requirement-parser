from pathlib import Path
import json
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.general_printability_geometry_repair import build_printability_repair_request
import argparse

p=argparse.ArgumentParser()
p.add_argument('artifact',type=Path)
p.add_argument('geometry',type=Path)
p.add_argument('output',type=Path)
a=p.parse_args()
gate=inspect_final_gcode_printability(a.artifact,geometry_path=a.geometry,credit_removable_support=False)
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(gate,indent=2),encoding='utf-8')
request=build_printability_repair_request(source_geometry=a.geometry,gate_report=gate)
print(json.dumps({'status':gate['status'],'dangerous_layers':gate['dangerous_layer_count'],
                 'clusters':[c.to_dict() for c in request.blocker_clusters]},indent=2),flush=True)
