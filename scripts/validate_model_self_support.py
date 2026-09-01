"""Actual local slicing across unrelated shapes; no cloud generation/printer IO."""
from pathlib import Path
import argparse
import hashlib
import json
from datetime import datetime
import traceback
import trimesh
from am_print_executor.verified_print_preparation import prepare_verified_print

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--profiles',type=Path,required=True)
p.add_argument('--repeat-source',type=Path)
p.add_argument('--repeat-height',type=float)
a=p.parse_args()
root=Path(__file__).resolve().parents[1]
out=root/'outputs/model_self_support'/datetime.now().strftime('regression_%Y%m%d_%H%M%S')
out.mkdir(parents=True)
profiles={'machine_json':next(a.profiles.glob('machine_*.json')),
          'process_json':next(a.profiles.glob('process_*.json')),
          'filament_jsons':[next(a.profiles.glob('filament_*.json'))]}
cases=[(name,root/'tests/fixtures/printability_strategy_benchmark_v1'/f'{name}.stl',None,expected)
       for name,expected in [('control_block','pass'),('self_support_cone','pass'),
           ('base_recessed_hole','pass'),('connected_cantilever','pass'),
           ('bridge_two_piers','pass'),('true_floating_island','blocked'),('organic_head_on_neck','either')]]
if a.repeat_source:
    # Neither the filename nor the artifact history is visible to repair decisions.
    alias=out/('unrelated_input_name'+a.repeat_source.suffix)
    alias.write_bytes(a.repeat_source.read_bytes())
    cases.append(('renamed_repeat',alias,a.repeat_height,'pass'))
rows=[]
for name,source,height,expected in cases:
    print('RUN',name,flush=True)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    output=out/name/'model.gcode.3mf'
    row={'name':name,'source':str(source),'source_sha256':sha,'expected':expected}
    try:
        result=prepare_verified_print(source,output,target_height_mm=height,support_mode="permanent",**profiles)
        row.update(status='pass' if result['status']=='slice_complete' else 'blocked',
                   preparation_directory=result['preparation_directory'],geometry_path=result['geometry_path'],
                   acceptance=result['acceptance'])
        preparation=json.loads((Path(result['preparation_directory'])/'preparation.json').read_text(encoding='utf-8'))
        row['repair_rounds']=len(preparation['rounds'])
        row['geometry_sha256']=hashlib.sha256(Path(result['geometry_path']).read_bytes()).hexdigest()
        row['output_published']=output.exists()
    except ValueError as exc:
        row.update(status='blocked',error=str(exc),output_published=output.exists())
    except Exception as exc:
        row.update(status='error',error=str(exc),traceback=traceback.format_exc(),output_published=output.exists())
    row['source_unchanged']=sha==hashlib.sha256(source.read_bytes()).hexdigest()
    row['expected_result_met']=(row['status']==expected or expected=='either' and row['status'] in {'pass','blocked'}) and row['source_unchanged']
    if row['status']!='pass' and row['output_published']: row['expected_result_met']=False
    rows.append(row)
    (out/'report.json').write_text(json.dumps({'passed':all(r['expected_result_met'] for r in rows),
        'completed':len(rows),'planned':len(cases),'cases':rows},indent=2),encoding='utf-8')
    print(name,row['status'],row['expected_result_met'],row.get('error',''),flush=True)
print('REPORT',out/'report.json',flush=True)
raise SystemExit(0 if all(r['expected_result_met'] for r in rows) else 1)
