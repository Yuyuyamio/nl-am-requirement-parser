"""Local real-slicer acceptance, a small removal coupon and a renamed repeat.

No printer connection, upload or physical print. Incomplete runs stay blocked.
"""
from pathlib import Path
import argparse
from datetime import datetime
import hashlib
import json
import traceback

import trimesh

from am_print_executor.bambu_headless_cli import BambuHeadlessCliError
from am_print_executor.detachable_support import detachable_process
from am_print_executor.verified_print_preparation import prepare_verified_print


def box(size, center):
    mesh = trimesh.creation.box(size)
    mesh.apply_translation(center)
    return mesh


def ellipsoid(size, center):
    mesh = trimesh.creation.icosphere(subdivisions=3)
    mesh.apply_scale(size)
    mesh.apply_translation(center)
    return mesh


def removal_coupon():
    """Two thin fins on a neck: accessible support contacts and a stable base."""
    return trimesh.boolean.union([box((16,10,1.2),(0,0,.6)), box((2.6,3,5.5),(0,0,3.8)),
        ellipsoid((2.4,1.6,2.4),(0,0,7.5)),
        ellipsoid((2.5,.75,3.5),(-3.2,0,9)),ellipsoid((2.5,.75,3.5),(3.2,0,9))],engine='manifold')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path)
    p.add_argument('--profiles',type=Path,required=True)
    p.add_argument('--height-mm',type=float,default=30)
    p.add_argument('--output-root',type=Path,default=Path('outputs/detachable_support'))
    args = p.parse_args()
    out = (args.output_root/datetime.now().strftime('validation_%Y%m%d_%H%M%S')).resolve()
    out.mkdir(parents=True,exist_ok=False)
    profiles = dict(machine_json=next(args.profiles.glob('machine_*.json')).resolve(),
        process_json=next(args.profiles.glob('process_*.json')).resolve(),
        filament_jsons=[next(args.profiles.glob('filament_*.json')).resolve()])
    machine = json.loads(profiles['machine_json'].read_text(encoding='utf-8'))
    process = json.loads(profiles['process_json'].read_text(encoding='utf-8'))
    profile, policy = detachable_process(process,float(machine['nozzle_diameter'][0]))
    (out/'proposed_process.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    coupon_path = out/'removal_coupon_UNVERIFIED.stl'
    coupon = removal_coupon()
    if not coupon.is_volume or coupon.body_count != 1:
        raise ValueError('invalid_removal_coupon')
    coupon.export(coupon_path)
    alias = out/('renamed_input'+args.source.suffix)
    alias.write_bytes(args.source.read_bytes())
    fixtures = Path('tests/fixtures/printability_strategy_benchmark_v1').resolve()
    cases = [('main',args.source.resolve(),args.height_mm,'pass'),('coupon',coupon_path,None,'pass'),
        ('block',fixtures/'control_block.stl',None,'pass'),
        ('cantilever',fixtures/'connected_cantilever.stl',None,'pass'),
        ('floating_island',fixtures/'true_floating_island.stl',None,'blocked'),
        ('renamed_repeat',alias,args.height_mm,'pass')]
    report = {'status':'incomplete','passed':False,'planned':len(cases),'completed':0,
        'support_policy':policy,'physical_print_performed':False,'cases':[]}
    for name, source, height, expected in cases:
        print('CHECK',name,flush=True)
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        target = out/name/'accepted.gcode.3mf'
        row = dict(name=name,source=str(source),source_sha256=sha,expected=expected)
        stop = False
        try:
            result = prepare_verified_print(source,target,target_height_mm=height,support_mode='detachable',**profiles)
            row.update(status='pass' if result['status']=='slice_complete' else 'blocked',
                preparation_directory=result['preparation_directory'],acceptance=result['acceptance'],
                blocker=result.get('terminal_blocker'))
            row['geometry_sha256'] = hashlib.sha256(Path(result['geometry_path']).read_bytes()).hexdigest()
        except BambuHeadlessCliError as exc:
            row.update(status='environment_blocked',error=str(exc));stop=True
            report['status']='waiting_for_bambu_studio_to_close'
        except ValueError as exc:
            row.update(status='blocked',error=str(exc))
        except Exception as exc:
            row.update(status='error',error=str(exc),traceback=traceback.format_exc())
        row['source_unchanged'] = sha == hashlib.sha256(source.read_bytes()).hexdigest()
        row['output_published'] = target.exists()
        row['expected_result_met'] = (row['status']==expected and row['source_unchanged']
            and (row['status']=='pass') == row['output_published'])
        report['cases'].append(row)
        report['completed'] = len(report['cases'])
        if report['completed'] == len(cases):
            first, last = report['cases'][0], report['cases'][-1]
            report['renamed_repeat_identical_geometry'] = bool(first.get('geometry_sha256') and
                first.get('geometry_sha256') == last.get('geometry_sha256'))
            report['passed'] = all(r['expected_result_met'] for r in report['cases']) and report['renamed_repeat_identical_geometry']
            report['status'] = 'pass' if report['passed'] else 'blocked'
        (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(name,row['status'],flush=True)
        if stop:
            break
    print('REPORT',out/'report.json',flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
