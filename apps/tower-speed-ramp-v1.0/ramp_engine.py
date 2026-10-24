"""Volume-based feed-rate ramp. Never changes the source file or path order."""
import math, os, re, tempfile
from pathlib import Path

MARKER='; IPT_VOLUME_RAMP_V1'
NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
WORDS=re.compile(r'([XYZEF])\s*('+NUMBER+r')')

def validate(start_percent,volume):
    start_percent=float(start_percent);volume=float(volume)
    if not math.isfinite(start_percent) or not 0<start_percent<=100:
        raise ValueError('Starting speed must be greater than 0 and at most 100 percent.')
    if not math.isfinite(volume) or not 0<volume<=1000:
        raise ValueError('Ramp volume must be greater than 0 and at most 1000 mm³.')
    return start_percent,volume

def rewrite(source,destination,manifest,start_percent=25.,ramp_volume=5.):
    """Requires audited plain, absolute-XYZ G-code. Emits only linear subdivisions.

    F is restored after every modified source move, preserving modal travel,
    retraction and model speeds. Retraction repayment is not priming volume.
    """
    start_percent,ramp_volume=validate(start_percent,ramp_volume)
    source=Path(source);destination=Path(destination)
    if source.resolve()==destination.resolve() or destination.exists():raise ValueError('Choose a new ramped G-code filename; the input cannot be overwritten.')
    xy={'X':None,'Y':None,'Z':0.};relative=False;e=0.;feed=None;tool=None;layer=None;feature=None
    volume=0.;debt={};modified=0;pickups=0;completed=0;done=False
    diameters=manifest.get('filament_diameters',['1.75']*12)
    if not isinstance(diameters,list):diameters=[diameters]*12
    tb=manifest['tower_bounds'];brim=manifest.get('brim_width',0)
    frame=manifest.get('frame_tool',manifest.get('petg_tool'))
    def inside(p,b):return all(p[k] is not None and b[i]-.03<=p[k]<=b[i+2]+.03 for i,k in enumerate(('X','Y')))
    def fmt(v):return f'{v:.9f}'.rstrip('0').rstrip('.') or '0'
    created=False
    try:
        with source.open(encoding='utf-8') as src,destination.open('x',encoding='utf-8',newline='\n') as out:
            created=True
            out.write(f'{MARKER} start_percent={start_percent:g} volume_mm3={ramp_volume:g}\n')
            for lineno,line in enumerate(src,1):
                if line.startswith(MARKER):raise ValueError('This file has already been ramped. Select the original Orca export.')
                ztag=re.match(r'\s*;\s*Z\s*:\s*('+NUMBER+')',line,re.I)
                if ztag:layer=float(ztag[1])
                ftag=re.match(r'\s*;\s*(?:FEATURE|TYPE)\s*:\s*(.*?)\s*$',line,re.I)
                if ftag:feature=ftag[1].lower()
                code=line.split(';',1)[0].strip();cmd=code.split()[0] if code else ''
                if '*' in code or re.match(r'N\d',code):raise ValueError('Numbered/checksummed G-code cannot be ramped.')
                if cmd in ('G20','G91','G2','G3','G93','G95'):raise ValueError(f'Unsupported motion mode {cmd} at line {lineno}. Use millimeters, absolute XYZ and linear moves.')
                match=re.fullmatch(r'T(\d+)',code)
                if match:
                    new=int(match[1])
                    if new!=tool:volume=0.;done=False;pickups+=1
                    tool=new
                if cmd=='M83':relative=True
                if cmd=='M82':relative=False
                args={k:float(v) for k,v in WORDS.findall(code)}
                if any(not math.isfinite(v) for v in args.values()):raise ValueError('Non-finite motion parameter.')
                if cmd=='G92':
                    if any(k in args for k in ('X','Y','Z')):raise ValueError('XYZ coordinate resets cannot be ramped.')
                    e=args.get('E',e)
                if cmd not in ('G0','G1'):
                    out.write(line);continue
                previous=xy.copy();old_e=e
                for k in xy:
                    if k in args:xy[k]=args[k]
                if 'F' in args:feed=args['F']
                de=args.get('E',0.) if relative else args.get('E',e)-e
                if 'E' in args:e=e+de if relative else args['E']
                owed=debt.get(tool,0.)
                paid=min(max(de,0.),owed)
                debt[tool]=owed-de if de<0 else max(0.,owed-de)
                deposited=max(0.,de-paid)
                bounds=tb
                if layer is not None and layer<=manifest.get('first_layer_height',.2)+.001 and tool==frame and feature in ('brim','skirt/brim'):
                    bounds=[tb[0]-brim,tb[1]-brim,tb[2]+brim,tb[3]+brim]
                if layer is not None and deposited>0 and any(k in args for k in ('X','Y')) and any(previous[k] is None for k in ('X','Y')):
                    raise ValueError(f'Unknown XY starting position at extrusion line {lineno}.')
                tower=(layer is not None and tool is not None and inside(previous,bounds) and inside(xy,bounds)
                       and any(xy[k]!=previous[k] for k in ('X','Y')) and deposited>0)
                if not tower or volume>=ramp_volume or start_percent==100:
                    out.write(line);continue
                if feed is None or feed<=0:raise ValueError(f'No positive feed rate for tower move at line {lineno}.')
                residue=WORDS.sub('',code[len(cmd):]).strip()
                if residue:raise ValueError(f'Unsupported tower motion parameters at line {lineno}: {residue}')
                if tool>=len(diameters):raise ValueError(f'Missing filament diameter for T{tool}.')
                diameter=float(diameters[tool])
                if not math.isfinite(diameter) or diameter<=0:raise ValueError('Invalid filament diameter.')
                area=math.pi*(diameter/2)**2;added=deposited*area
                # Bound speed increments to 1/40 of the requested ramp volume.
                cuts=[];repay_fraction=paid/de
                if repay_fraction>0:cuts.append(repay_fraction)
                until=min(added,ramp_volume-volume)
                count=max(1,math.ceil(until/(ramp_volume/40)))
                for j in range(1,count+1):cuts.append((paid+until*j/count/area)/de)
                if cuts[-1]<1-1e-12:cuts.append(1.)
                cuts[-1]=1.
                previous_fraction=0.;emitted_e=0.
                for j,fraction in enumerate(cuts):
                    before=volume+max(0.,de*previous_fraction-paid)*area
                    factor=start_percent/100+(1-start_percent/100)*min(1.,before/ramp_volume)
                    words=['G1']
                    for k in ('X','Y','Z'):
                        if k in args:
                            if previous[k] is None:raise ValueError('Unknown starting coordinate for ramp subdivision.')
                            words.append(k+fmt(previous[k]+(xy[k]-previous[k])*fraction))
                    if relative:
                        value=round(de*fraction,9)-emitted_e;emitted_e+=value
                    else:value=old_e+de*fraction
                    words.extend(('E'+fmt(value),'F'+fmt(feed*factor)))
                    comment=(' ;'+line.split(';',1)[1].rstrip('\r\n')) if j==0 and ';' in line else ''
                    out.write(' '.join(words)+comment+'\n');previous_fraction=fraction
                out.write('G1 F'+fmt(feed)+' ; IPT restore sliced feed rate\n')
                modified+=1;volume+=added
                if volume>=ramp_volume-1e-8 and not done:completed+=1;done=True
    except BaseException:
        if created:destination.unlink(missing_ok=True)
        raise
    return {'modified_moves':modified,'pickups':pickups,'completed_ramps':completed,
            'start_percent':start_percent,'ramp_volume_mm3':ramp_volume}
