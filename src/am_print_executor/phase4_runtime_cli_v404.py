from __future__ import annotations
import argparse, getpass, json, sys
from pathlib import Path
from .gate4_runtime_v404 import Gate4V404Error, run_gate4a_v404

def main(argv=None):
    p=argparse.ArgumentParser(description='M4 Gate4A v4.0.4 robust runtime-status preflight')
    p.add_argument('--project-root', required=True)
    a=p.parse_args(argv)
    code=getpass.getpass('X1C Developer Mode Access Code (hidden; not stored): ').strip()
    try:
        result=run_gate4a_v404(Path(a.project_root), code)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Gate4V404Error as exc:
        print(json.dumps({'module':'M4','phase':4,'version':'4.0.4','status':'gate4_blocked','error':str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({'module':'M4','phase':4,'version':'4.0.4','status':'gate4_failed','error':f'{type(exc).__name__}: {exc}'}, ensure_ascii=False, indent=2))
        return 2
if __name__=='__main__': sys.exit(main())
