"""Read-only source / local slicer experiment. Never contacts a printer."""
from pathlib import Path
import argparse
import json
from am_print_executor.verified_print_preparation import slice_fixed_geometry
from am_print_executor.gcode_printability_gate import inspect_final_gcode_printability
from am_print_executor.detachable_support import detachable_process

parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
parser.add_argument('directory', type=Path)
parser.add_argument('--gap', type=float, default=.24)
args = parser.parse_args()
args.directory.mkdir(parents=True, exist_ok=False)
profiles = Path('outputs/model_self_support/trial_1/verified_preparation_nwd6wz1m/profiles').resolve()
process = json.loads((profiles/'process_7766415163a1ef9b.json').read_text(encoding='utf-8'))
process, _ = detachable_process(process, .4)
process.update(support_top_z_distance=str(args.gap), support_bottom_z_distance=str(args.gap))
process_path = args.directory.resolve()/'process.json'
process_path.write_text(json.dumps(process, indent=2), encoding='utf-8')
result = slice_fixed_geometry(source=args.source.resolve(), directory=args.directory.resolve()/'slice',
    studio_exe=Path('C:/Program Files/Bambu Studio/bambu-studio.exe'),
    machine_json=profiles/'machine_dbec478e1cdf1ce8.json', process_json=process_path,
    filament_jsons=[profiles/'filament_93eae581e0140c44.json'], support_mode='detachable')
gate = inspect_final_gcode_printability(result['artifact'], geometry_path=result['geometry'], credit_removable_support=True)
(args.directory/'supported_gate.json').write_text(json.dumps(gate,indent=2), encoding='utf-8')
print(json.dumps({k:gate.get(k) for k in ('status','dangerous_layer_count','blockers','total_bad_area_mm2')},indent=2))
