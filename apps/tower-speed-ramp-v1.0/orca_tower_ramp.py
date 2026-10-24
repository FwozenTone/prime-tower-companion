#!/usr/bin/env python3
"""Independent Orca export hook. Orca appends its working G-code filename."""
import argparse, os, stat, sys, tempfile
from pathlib import Path
import ramp_engine
import tower_project

def require_pass(result,stage):
    if not result['passed']:
        details='\n'.join(result['errors'][:10])
        raise ValueError(f'{stage} audit failed; G-code was not changed.\n{details}')

def process(gcode,project,start_percent=25.,volume=5.):
    start_percent,volume=ramp_engine.validate(start_percent,volume)
    path=Path(gcode).resolve(strict=True)
    if not path.is_file():raise ValueError('Orca did not supply a G-code file.')
    with path.open(encoding='utf-8') as stream:
        if stream.readline().startswith(ramp_engine.MARKER):
            raise ValueError('G-code already contains a speed ramp. Export a fresh slice; do not run this script twice in the post-processing list.')
    manifest=tower_project.load_manifest(project)
    minimum=max(float(manifest.get('purge_volume',5.)),volume if start_percent<100 else 0.)
    # Keep the generator's original scheduling/material audit, with a minimum
    # large enough to finish the requested ramp before model/support extrusion.
    before=tower_project.audit(path,manifest,minimum)
    require_pass(before,'Original export')
    fd,name=tempfile.mkstemp(prefix='.orca-tower-ramp-',suffix='.gcode',dir=path.parent)
    os.close(fd);temp=Path(name);temp.unlink()
    try:
        result=ramp_engine.rewrite(path,temp,manifest,start_percent,volume)
        if start_percent<100 and result['completed_ramps']!=result['pickups']:
            raise ValueError('At least one pickup lacks enough deposited tower volume to complete the ramp; G-code was not changed.')
        after=tower_project.audit(temp,manifest,minimum)
        require_pass(after,'Ramped export')
        temp.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temp,path)
        return {'speed_ramp':result,'audit':after}
    finally:temp.unlink(missing_ok=True)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,help='Saved tower .3mf, after moving the assembly in Orca')
    parser.add_argument('--start-percent',type=float,default=25.,help='Percent of each original sliced speed (default 25)')
    parser.add_argument('--volume',type=float,default=5.,help='Deposited tower volume to reach full speed, mm³ (default 5)')
    parser.add_argument('gcode',help='Working G-code path appended automatically by Orca')
    args=parser.parse_args(argv)
    try:
        result=process(args.gcode,args.project,args.start_percent,args.volume)
        stats=result['speed_ramp']
        print(f"Tower Ramp: audited; ramped {stats['modified_moves']} moves after {stats['pickups']} pickups. Original export filename retained.")
        return 0
    except Exception as error:
        print('Tower Ramp: '+str(error),file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
