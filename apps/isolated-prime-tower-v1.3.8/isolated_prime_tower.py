#!/usr/bin/env python3
"""Isolated Prime Tower companion extension for OrcaSlicer 2.4.2.

Generates ordinary, filament-assigned objects in a COPY of a saved Orca project.
It is not a native plugin or a replacement for Orca's tool-change scheduler.
The audit command verifies exported G-code rather than rewriting machine commands.
Python 3.10+, standard library only.
"""
from __future__ import annotations
import argparse, copy, json, math, os, posixpath, re, sys, tempfile, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
PROD = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
ET.register_namespace('', CORE)
ET.register_namespace('p', PROD)
ET.register_namespace('BambuStudio', 'http://schemas.bambulab.com/package/2021')
Q = lambda s: '{'+CORE+'}'+s
PREFIX = 'IPT_'
WALL = .8
LANE = 28.4/6
LONG = 30.
MAX_TOOLS = 12
MANIFEST = 'Metadata/isolated_prime_tower.json'
MODEL = '3D/3dmodel.model'
SETTINGS = 'Metadata/project_settings.config'
MODEL_SETTINGS = 'Metadata/model_settings.config'
FEATURE_KEYS = ('outer_wall_filament_id','inner_wall_filament_id',
                'sparse_infill_filament_id','internal_solid_filament_id',
                'top_surface_filament_id','bottom_surface_filament_id')
class TowerError(ValueError): pass

def material_key(value):
    # Keep blends/additives distinct; only normalize spelling of plain nylon.
    key=str(value).strip().upper()
    return 'PA' if key=='NYLON' else key

def rigid_material(value):
    key=material_key(value)
    if any(word in key for word in ('SUPPORT','PVA','BVOH','HIPS','TPU','TPE','SOLUBLE')):return False
    return any(key==base or key.startswith(base+'-') or key.startswith(base+' ') for base in ('PLA','PETG','ASA','ABS','PA','PA6','PA12','PA66','PPS','PC','PEEK','PEI'))

def choose_frame(order,materials,override=None):
    candidates=[s for s in order if rigid_material(materials[s])]
    if override:
        if override not in candidates:raise TowerError('Frame slot must be a used rigid build material, not a dedicated support or flexible filament.')
        return override
    if not candidates:raise TowerError('No recognized rigid build material for the frame. Set an accurate filament type in the original 3MF.')
    # Deterministic preference, not a measured material-strength claim.
    preference=('PPS','PEEK','PEI','PC','ASA','ABS','PETG','PLA','PA')
    return min(candidates,key=lambda s:(next((i for i,k in enumerate(preference) if materials[s].startswith(k)),99),s))

class ReferenceLayers(dict):
    def __init__(self):
        super().__init__();self.pickup_layers=[];self.support_tools=set()


def reference_layers(path,allowed):
    """Read deposited model/support tools, excluding tower and bed auxiliaries."""
    layers=ReferenceLayers();layer=None;tool=None;feature=None;relative=False;e=0.;xy=None;pickup=False
    skipped={'prime tower','wipe tower','skirt','brim','skirt/brim','custom'}
    with open(path,encoding='utf-8') as stream:
        for lineno,line in enumerate(stream,1):
            if re.match(r'\s*;\s*Z\s*:',line,re.IGNORECASE):
                layer=round(float(line.split(':',1)[1]),5)
                if not math.isfinite(layer) or layer<=0:raise TowerError('Invalid reference layer Z.')
                # Feature tags describe modal state and need not repeat at a
                # layer boundary when the extrusion role has not changed.
                layers.setdefault(layer,[])
            tag=re.match(r'\s*;\s*(?:FEATURE|TYPE)\s*:\s*(.*?)\s*$',line,re.IGNORECASE)
            if tag:feature=tag[1].lower()
            tag=re.match(r'\s*;\s*(?:FEATURE|TYPE)\s*:\s*(.*?)\s*$',line,re.IGNORECASE)
            if tag:feature=tag[1].lower()
            code=line.split(';',1)[0].strip()
            if not code:continue
            match=re.fullmatch(r'T(\d+)',code)
            if match:
                new=int(match[1]);pickup=pickup or new!=tool;tool=new;continue
            cmd=code.split()[0]
            if cmd=='M83':relative=True;continue
            if cmd=='M82':relative=False;continue
            if cmd in ('G91','G2','G3'):raise TowerError('Reference requires absolute XY and arc fitting off.')
            args={k:float(v) for k,v in re.findall(r'([XYE])\s*(-?(?:\d+(?:\.\d*)?|\.\d+))',code)}
            if cmd=='G92':
                if 'X' in args or 'Y' in args:raise TowerError('Reference XY resets are unsupported.')
                e=args.get('E',e);continue
            if cmd not in ('G0','G1'):continue
            old=xy
            if 'X' in args or 'Y' in args:xy=(args.get('X',xy[0] if xy else 0.),args.get('Y',xy[1] if xy else 0.))
            de=args.get('E',0.) if relative else args.get('E',e)-e
            if 'E' in args:e=e+de if relative else args['E']
            if layer is None or de<=0 or old is None or xy==old or feature in skipped:continue
            if feature is None:raise TowerError(f'Reference extrusion at line {lineno} precedes any FEATURE/TYPE comment. Share the reference G-code so this extrusion can be identified.')
            if tool not in allowed:raise TowerError('Reference extrusion needs an explicit Tn matching the selected filaments.')
            if tool not in layers[layer]:layers[layer].append(tool)
            if 'support' in feature:layers.support_tools.add(tool)
            if pickup:layers.pickup_layers.append(layer);pickup=False
    if not layers or not any(layers.values()):raise TowerError('No reference layer/tool extrusion found.')
    return layers

def donor_schedule(layers,order,petg,height,step=None,first=None,materials=None):
    """Only identical material types may maintain each other's idle sections."""
    materials=materials or {s:('PETG' if s==petg else 'PLA') for s in order}
    groups={}
    for slot in order:groups.setdefault(materials[slot],[]).append(slot-1)
    last={key:tools[0] for key,tools in groups.items()};schedule=[];lo=0.
    for z in sorted(layers):
        if z>height+.00001:break
        if not math.isfinite(z) or z<=lo:raise TowerError('Invalid reference layer heights.')
        owners={}
        for key,tools in groups.items():
            active=[t for t in layers[z] if t in tools]
            donor=last[key] if last[key] in active or not active else active[0]
            for tool in tools:owners[tool]=tool if tool in active else donor
            last[key]=active[-1] if active else donor
        schedule.append({'z':z,'min_z':lo,'max_z':z,'tools':[owners[s-1] for s in order],
                         'reference_tools':layers[z]})
        lo=z
    return schedule

def layer_profile(schedule):
    """Orca's piecewise constant height profile, indexed by build-object ordinal."""
    values=[]
    for row in schedule:
        lo,hi=row['min_z'],row['max_z'];h=hi-lo
        values.extend((lo,h,hi,h))
    if len(values)==4:values.extend(values[-2:])
    return 'object_id=1|'+ ';'.join(f'{v:.8f}' for v in values)+'\n'

def lane_dimensions(width,volume,min_height):
    # Reserve 15% for slicer rounding and keep the existing turn spacing.
    pitch=(LANE-WALL)/2
    straight=width-WALL-pitch
    runs=max(3,math.ceil(volume/(.85*WALL*min_height*straight)))
    if runs%2==0:runs+=1
    if runs>101:raise TowerError('Requested purge volume needs an impractically deep tower. Increase tower width or reduce volume.')
    return WALL+(runs-1)*pitch,runs


def quad(a,b,c,d): return [(a,b,c),(a,c,d)]
def prism(p,z0,z1):
    ts=[]
    for i in range(1,len(p)-1):
        ts += [((*p[0],z0),(*p[i+1],z0),(*p[i],z0)),
               ((*p[0],z1),(*p[i],z1),(*p[i+1],z1))]
    for i,a in enumerate(p):
        b=p[(i+1)%len(p)]
        ts += quad((*a,z0),(*b,z0),(*b,z1),(*a,z1))
    return ts

def rect(w,h): return [(-w/2,-h/2),(w/2,-h/2),(w/2,h/2),(-w/2,h/2)]
def rounded(w,h,r):
    result=[]
    for cx,cy,start in [(w/2-r,h/2-r,0),(-w/2+r,h/2-r,90),
                        (-w/2+r,-h/2+r,180),(w/2-r,-h/2+r,270)]:
        for j in range(9):
            a=math.radians(start+j*90/8)
            result.append((cx+r*math.cos(a),cy+r*math.sin(a)))
    return result

def ring(outer,inner,height,transform):
    o=[transform(*p) for p in outer]; ii=[transform(*p) for p in inner]; ts=[]
    for i in range(len(o)):
        j=(i+1)%len(o)
        a,b,c,d=(*o[i],0),(*o[j],0),(*ii[j],0),(*ii[i],0)
        A,B,C,D=(*o[i],height),(*o[j],height),(*ii[j],height),(*ii[i],height)
        ts+=quad(a,b,B,A)+quad(d,D,C,c)+quad(a,d,c,b)+quad(A,B,C,D)
    return ts

def capsule(p,q,height,width=.8):
    angle=math.atan2(q[1]-p[1],q[0]-p[0]); r=width/2; poly=[]
    for center,start in [(q,angle-math.pi/2),(p,angle+math.pi/2)]:
        for j in range(17):
            a=start+j*math.pi/16
            poly.append((center[0]+r*math.cos(a),center[1]+r*math.sin(a)))
    return prism(poly,0,height)

def rib_outward_shift(count,lane=LANE,rib=25.):
    """Keep a 1 mm gap between opposing rounded, rotated ribs."""
    box_width=2*WALL+count*lane
    radius=min(3.,rib/2-.01)
    rib_span=2*((rib/2-radius)*math.sqrt(2)+radius)
    center_separation=box_width+2*(rib/2-.4)/math.sqrt(2)
    return max(0.,(rib_span+1.-center_separation)/2)

def geometry(count,height,width=LONG,rib=25.,lane=LANE,runs=3):
    """Fixed-width lanes; outer frame and rib positions adapt to filament count.

    Intersecting closed capsule solids are intentionally unioned by the slicer.
    Returns frame and one triangle list per filament lane, in local coordinates.
    """
    if not 1 <= count <= MAX_TOOLS: raise TowerError('Use one to twelve filament slots.')
    if height<=0 or not math.isfinite(height): raise TowerError('Invalid tower height.')
    bx=width+2*WALL; by=count*lane+2*WALL
    frame=ring(rect(bx,by),rect(width,count*lane),height,lambda x,y:(x+bx/2,y+by/2))
    corners=[(bx,by,45),(0,by,135),(0,0,225),(bx,0,315)]
    for x,y,angle in corners:
        a=math.radians(angle); co,si=math.cos(a),math.sin(a)
        radius=min(3.,rib/2-.01);inner=rib-1.6;inner_radius=min(2.2,inner/2-.01)
        shift_y=rib_outward_shift(count,lane,rib)*(1 if y>0 else -1)
        shift_x=rib_outward_shift(1,width,rib)*(1 if x>0 else -1)
        def transform(u,v):
            u+=rib/2-.4
            return x+u*co-v*si+shift_x,y+u*si+v*co+shift_y
        frame+=ring(rounded(rib,rib,radius),rounded(inner,inner,inner_radius),height,transform)
        c=rib/2-radius+(radius-.4)/math.sqrt(2)
        ends=[(x+(rib/2-.4-c)*co-v*si+shift_x,y+(rib/2-.4-c)*si+v*co+shift_y) for v in [-c,c]]
        targets=[(bx/2,y),(x,by/2)]
        if math.dist(ends[0],targets[0])>math.dist(ends[1],targets[0]): ends.reverse()
        # Adapt handle length when there are fewer than six filament lanes.
        handle=min(5.,by/4)
        for idx,(start,end) in enumerate(zip(ends,targets)):
            if idx==0:
                c1=(start[0],start[1]+(-1 if y>0 else 1)*handle)
                c2=(end[0]+(1 if x>0 else -1)*5,end[1])
            else:
                c1=(start[0]+(-1 if x>0 else 1)*5,start[1])
                c2=(end[0],end[1]+(1 if y>0 else -1)*handle)
            points=[]
            for j in range(65):
                t=j/64;s=1-t
                points.append(tuple(s**3*start[i]+3*s*s*t*c1[i]+3*s*t*t*c2[i]+t**3*end[i] for i in (0,1)))
            for p,q in zip(points,points[1:]): frame+=capsule(p,q,height)
    lanes=[]
    for k in range(count):
        y0=WALL+k*lane;y1=y0+lane
        ts=prism([(WALL,y0),(WALL+width,y0),(WALL+width,y1),(WALL,y1)],0,min(1.,height))
        pitch=(lane-WALL)/(runs-1);radius=pitch/2
        left=WALL+.4+radius;right=WALL+width-.4-radius
        ys=[y0+.4+i*pitch for i in range(runs)]
        points=[(left,ys[0]),(right,ys[0])]
        for run in range(runs-1):
            center_x=right if run%2==0 else left
            for j in range(1,49):
                a=-math.pi/2+(1 if run%2==0 else -1)*j*math.pi/48
                points.append((center_x+radius*math.cos(a),(ys[run]+ys[run+1])/2+radius*math.sin(a)))
            points.append((left if run%2==0 else right,ys[run+1]))
        for p,q in zip(points,points[1:]):ts+=capsule(p,q,height)
        lanes.append(ts)
    return frame,lanes

def lane_slab(triangles,height,lo,hi):
    """Rebuild a closed height span of geometry()'s base and capsule prisms.

    The first 12 triangles form the rectangular 0..1 mm base; all remaining
    triangles belong to capsules with vertices only at 0 and height.
    """
    result=[]
    for source,top in ((triangles[:12],min(1.,height)),(triangles[12:],height)):
        upper=min(hi,top)
        if upper<=lo:continue
        result.extend(tuple((x,y,lo if z==0 else upper) for x,y,z in tri) for tri in source)
    return result

def bounds(triangles):
    pts=[v for t in triangles for v in t]
    return [min(v[i] for v in pts) for i in range(3)]+[max(v[i] for v in pts) for i in range(3)]

def mesh_xml(parent,triangles):
    mesh=ET.SubElement(parent,Q('mesh'));vs=ET.SubElement(mesh,Q('vertices'));fs=ET.SubElement(mesh,Q('triangles'))
    table={};faces=[]
    for tri in triangles:
        face=[]
        for v in tri:
            v=tuple(round(x,6) for x in v)
            if v not in table:table[v]=len(table)
            face.append(table[v])
        if len(set(face))!=3: raise TowerError('Degenerate triangle in generated mesh.')
        faces.append(face)
    for v in table:ET.SubElement(vs,Q('vertex'),dict(zip(('x','y','z'),map(str,v))))
    for face in faces:ET.SubElement(fs,Q('triangle'),dict(zip(('v1','v2','v3'),map(str,face))))

def metadata(el):return {x.get('key'):x.get('value') for x in el.findall('metadata') if x.get('key')}
def set_meta(el,key,value):
    found=next((x for x in el.findall('metadata') if x.get('key')==key),None)
    if found is None:found=ET.SubElement(el,'metadata')
    found.set('key',key);found.set('value',str(value))
def xml_bytes(root):return ET.tostring(root,encoding='utf-8',xml_declaration=True)
def truth(v):return str(v).lower() in ('1','true')
def affine(s=None):
    v=list(map(float,s.split())) if s else [1,0,0,0,1,0,0,0,1,0,0,0]
    if len(v)!=12:raise TowerError('Invalid 3MF transformation.')
    return [[v[c*3+r] for c in range(4)] for r in range(3)]+[[0,0,0,1]]
def multiply(a,b):return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
def point(a,p):return tuple(sum(a[i][j]*p[j] for j in range(3))+a[i][3] for i in range(3))

class Project:
    def __init__(self,path,plate_number=None):
        with zipfile.ZipFile(path) as z:
            if len(z.namelist())!=len(set(z.namelist())):raise TowerError('Duplicate ZIP entries.')
            self.files={n:z.read(n) for n in z.namelist()}
        for n in (MODEL,SETTINGS,MODEL_SETTINGS):
            if n not in self.files:raise TowerError('Save a full Orca project (.3mf), not just geometry or sliced G-code.')
        if MANIFEST in self.files:raise TowerError('This project already contains an isolated tower. Start from the original project.')
        self.root=ET.fromstring(self.files[MODEL]);self.settings=json.loads(self.files[SETTINGS]);self.config=ET.fromstring(self.files[MODEL_SETTINGS])
        self.resources=self.root.find(Q('resources'));self.build=self.root.find(Q('build'))
        if self.resources is None or self.build is None:raise TowerError('Missing 3MF resources/build.')
        if self.root.get('unit','millimeter')!='millimeter':raise TowerError('Only millimeter projects are supported.')
        plates=self.config.findall('plate')
        if len(plates)>1 and plate_number is None:raise TowerError('Select a plate number with --plate for a multi-plate project.')
        self.plate_number=plate_number or 1
        if not 1<=self.plate_number<=max(1,len(plates)):raise TowerError('Plate number is outside this project.')
        self.plate=plates[self.plate_number-1] if plates else ET.SubElement(self.config,'plate')
        self.plate_offset=[0.,0.]
        if len(plates)>1:
            bed=[tuple(map(float,str(v).split('x'))) for v in self.settings['printable_area']]
            width=max(p[0] for p in bed)-min(p[0] for p in bed);depth=max(p[1] for p in bed)-min(p[1] for p in bed)
            cols=math.ceil(math.sqrt(len(plates)));index=self.plate_number-1
            self.plate_offset=[(index%cols)*width*1.2,-(index//cols)*depth*1.2]
        self.models={}
        for name,data in self.files.items():
            if name.endswith('.model'):
                r=ET.fromstring(data)
                if r.get('unit','millimeter')!='millimeter':raise TowerError('Only millimeter model parts are supported.')
                self.models[name]={o.get('id'):o for o in r.findall('./'+Q('resources')+'/'+Q('object'))}
        self.objects={o.get('id'):o for o in self.config.findall('object')}
        self.items=[x for x in self.build.findall(Q('item')) if x.get('printable','1')!='0']
        if len(plates)>1:
            members={(metadata(x).get('object_id'),int(metadata(x).get('instance_id','0'))) for x in self.plate.findall('model_instance')}
            counts={};selected=[]
            for item in self.build.findall(Q('item')):
                oid=item.get('objectid');idx=counts.get(oid,0);counts[oid]=idx+1
                if (oid,idx) in members and item.get('printable','1')!='0':selected.append(item)
            self.items=selected
        if not self.items:raise TowerError('No printable reference objects.')
        if any(metadata(o).get('name','').startswith(PREFIX) for o in self.objects.values()):raise TowerError('Remove the previous tower before regenerating.')
        if truth(self.settings.get('single_extruder_multi_material','1')):raise TowerError('Select a multi-toolhead profile; Single Extruder Multi Material must be off.')
        if truth(self.settings.get('spiral_mode','0')):raise TowerError('Spiral vase mode is incompatible.')
        self.world_points=[]
        for item in self.items:self.world_points+=self.vertices(MODEL,item.get('objectid'),affine(item.get('transform')),set())
        self.world_points=[(x-self.plate_offset[0],y-self.plate_offset[1],z) for x,y,z in self.world_points]
        if not self.world_points:raise TowerError('Reference has no printable mesh.')
        self.reference_bounds=[min(p[i] for p in self.world_points) for i in range(3)]+[max(p[i] for p in self.world_points) for i in range(3)]
        if self.reference_bounds[5]<=0:raise TowerError('Reference objects have no printable geometry above the build plate.')

    def vertices(self,path,oid,matrix,seen):
        key=(path,oid)
        if key in seen:raise TowerError('Cyclic 3MF components.')
        obj=self.models.get(path,{}).get(oid)
        if obj is None:raise TowerError('Unresolved 3MF component: '+str(key))
        result=[]
        mesh=obj.find(Q('mesh'))
        if mesh is not None:
            for v in mesh.findall('./'+Q('vertices')+'/'+Q('vertex')):
                result.append(point(matrix,tuple(float(v.get(k,'0')) for k in ('x','y','z'))))
        for c in obj.findall('./'+Q('components')+'/'+Q('component')):
            cfg=self.objects.get(oid) if path==MODEL else None
            part=next((p for p in cfg.findall('part') if p.get('id')==c.get('objectid')),None) if cfg is not None else None
            if part is not None and part.get('subtype','normal_part')!='normal_part':continue
            target=c.get('{'+PROD+'}path')
            child=posixpath.normpath(target.lstrip('/') if target and target.startswith('/') else posixpath.join(posixpath.dirname(path),target)) if target else path
            result+=self.vertices(child,c.get('objectid'),multiply(matrix,affine(c.get('transform'))),seen|{key})
        return result

    def used_filaments(self,explicit=None):
        settings=self.settings;types=settings.get('filament_type',[])
        if not isinstance(types,list):raise TowerError('Missing filament_type list.')
        used=set(explicit or [])
        if not explicit:
            for path,objects in self.models.items():
                if any('paint_color' in t.attrib for o in objects.values() for t in o.iter(Q('triangle'))):
                    raise TowerError('Painted materials need an explicit --filaments list of used Orca slot numbers.')
            for item in self.items:
                obj=self.objects.get(item.get('objectid'))
                if obj is None:used.add(1);continue
                om=metadata(obj);default=int(om.get('extruder','1')) or 1
                parts=obj.findall('part') or obj.findall('volume')
                normal=[x for x in parts if x.get('subtype','normal_part')=='normal_part']
                if not normal:used.add(default)
                for part in normal:used.add(int(metadata(part).get('extruder',str(default))) or default)
                sources=[settings,om]+[metadata(x) for x in parts]
                for source in sources:
                    for key in FEATURE_KEYS:
                        value=int(source.get(key,'0') or 0)
                        if value:used.add(value)
                    if truth(source.get('enable_support',settings.get('enable_support','0'))):
                        for key in ('support_filament','support_interface_filament'):
                            value=int(source.get(key,settings.get(key,'0')) or 0)
                            if value:used.add(value)
        if not used or any(i<1 or i>min(MAX_TOOLS,len(types)) for i in used):raise TowerError('Used filament slot is outside the project filament list.')
        materials={i:material_key(types[i-1]) for i in used}
        if any(not m or m in ('UNKNOWN','OTHER') for m in materials.values()):
            raise TowerError('Assign explicit filament material types in the original 3MF.')
        frame=choose_frame(sorted(used),materials)
        # Group identical types, keeping frame material last for legacy layouts.
        order=sorted(used,key=lambda i:(materials[i]==materials[frame],materials[i],i))
        return order,frame

    def place(self,b,origin=None,clearance=8.):
        bed=self.settings.get('printable_area',[])
        try:poly=[tuple(map(float,str(x).split('x'))) for x in bed]
        except ValueError:raise TowerError('Invalid printable area.')
        if len(poly)<4:raise TowerError('A rectangular printable_area is required.')
        minx,maxx=min(x for x,y in poly),max(x for x,y in poly);miny,maxy=min(y for x,y in poly),max(y for x,y in poly)
        if any(abs(x-minx)>.001 and abs(x-maxx)>.001 or abs(y-miny)>.001 and abs(y-maxy)>.001 for x,y in poly):
            raise TowerError('Only rectangular beds are supported in this version.')
        rb=self.reference_bounds; margin=3.
        def okay(x,y):
            tb=[x+b[0],y+b[1],x+b[3],y+b[4]]
            if not (minx+margin<=tb[0] and tb[2]<=maxx-margin and miny+margin<=tb[1] and tb[3]<=maxy-margin):return False
            if not (tb[2]+clearance<=rb[0] or tb[0]-clearance>=rb[3] or tb[3]+clearance<=rb[1] or tb[1]-clearance>=rb[4]):return False
            ex=self.settings.get('bed_exclude_area',[])
            if ex:
                try:ep=[tuple(map(float,str(v).split('x'))) for v in ex]
                except ValueError:raise TowerError('Invalid bed exclusion area.')
                eb=[min(v[0] for v in ep),min(v[1] for v in ep),max(v[0] for v in ep),max(v[1] for v in ep)]
                if not (tb[2]<eb[0] or tb[0]>eb[2] or tb[3]<eb[1] or tb[1]>eb[3]):return False
            return True
        if origin:
            if not okay(*origin):raise TowerError('Requested tower position is outside the bed or too close to reference/excluded area.')
            return origin
        for y in range(math.ceil(miny+margin-b[1]),math.floor(maxy-margin-b[4])+1,5):
            for x in range(math.ceil(minx+margin-b[0]),math.floor(maxx-margin-b[3])+1,5):
                if okay(x,y):return (float(x),float(y))
        raise TowerError('No clear tower position found. Move the reference or supply --origin X Y.')

    def generate(self,output,filaments=None,origin=None,clearance=8.,reference_gcode=None,width=30.,rib_size=5.,purge_volume=5.,support_slot=None,frame_slot=None,brim_width=5.):
        preserved_positions=[(node,'transform',node.get('transform')) for node in self.root.iter() if node.get('transform') is not None]
        preserved_positions += [(node,'value',node.get('value')) for node in self.config.iter('metadata') if node.get('key') in ('matrix','source_offset_z')]
        width=float(width);rib_size=float(rib_size);purge_volume=float(purge_volume);brim_width=float(brim_width)
        if not all(map(math.isfinite,(width,rib_size,purge_volume,brim_width))) or not 10<=width<=200 or not 5<=rib_size<=100 or not 0<purge_volume<=1000 or not 0<=brim_width<=100:
            raise TowerError('Tower width must be 10–200 mm, ribs 5–100 mm, purge volume greater than 0 and at most 1000 mm³, and brim width 0–100 mm.')
        layers=None
        if reference_gcode:
            types=self.settings.get('filament_type',[])
            if not isinstance(types,list):raise TowerError('Missing filament_type list.')
            layers=reference_layers(reference_gcode,set(range(min(MAX_TOOLS,len(types)))))
            detected={tool+1 for tools in layers.values() for tool in tools}
            if filaments is not None and set(filaments)!=detected:
                raise TowerError('--filaments must match the slots actually extruding in the reference G-code: '+','.join(map(str,sorted(detected))))
            filaments=sorted(detected)
        order,petg=self.used_filaments(filaments)
        if not math.isfinite(clearance) or clearance<0:raise TowerError('Clearance must be nonnegative.')
        materials={s:material_key(self.settings['filament_type'][s-1]) for s in order}
        petg=choose_frame(order,materials,int(frame_slot) if frame_slot else None)
        support_slots=sorted(t+1 for t in layers.support_tools) if layers is not None else []
        if support_slot:
            support_slot=int(support_slot)
            if support_slot not in order:raise TowerError('Selected support slot is not used in the reference slice.')
            if support_slots and support_slot not in support_slots:raise TowerError('Selected support slot does not print supports in the reference. Change the original support assignment and reslice.')
        height=self.reference_bounds[5]
        schedule=None
        step=float(self.settings.get('layer_height',.2));first=float(self.settings.get('initial_layer_print_height',step))
        if layers is not None:
            if abs(max(layers)-self.reference_bounds[5])>max(step,first)+.001:
                raise TowerError('Reference G-code height does not match this project.')
            height=max(layers.pickup_layers) if layers.pickup_layers else min(layers)
            schedule=donor_schedule(layers,order,petg,height,materials=materials)
            first=schedule[0]['z']
            if abs(first-float(self.settings.get('initial_layer_print_height',step)))>.001:
                raise TowerError('Reference first layer differs from project settings. Save matching settings and regenerate.')
        maximum=float(self.settings.get('printable_height','100000'))
        if height>maximum:raise TowerError('Required priming height exceeds printable height.')
        min_height=min(r['max_z']-r['min_z'] for r in schedule) if schedule else min(step,first)
        if min_height<=0:raise TowerError('Invalid layer height.')
        if schedule:
            for slot in order:
                def setting_value(key,default):
                    value=self.settings.get(key,default)
                    if isinstance(value,list):value=value[min(slot-1,len(value)-1)] if value else default
                    return float(value)
                nozzle=setting_value('nozzle_diameter',.4)
                minimum=setting_value('min_layer_height',.07) or .07
                maximum_layer=setting_value('max_layer_height',nozzle*.75) or nozzle*.75
                for row in schedule[1:]:
                    delta=round(row['max_z']-row['min_z'],5)
                    if delta<minimum-.00001 or delta>maximum_layer+.00001:
                        raise TowerError(f'Reference layer at Z={row["z"]:g} is {delta:g} mm; slot {slot} permits {minimum:g}–{maximum_layer:g} mm. Align the reference layer grids and regenerate.')
        lane_width,runs=lane_dimensions(width,purge_volume,min_height)
        frame,lanes=geometry(len(order),height,width,rib_size,lane_width,runs)
        # Frame and its material lane remain distinct volumes.
        b=bounds(frame);placement=list(b)
        for axis in (0,1):placement[axis]-=brim_width;placement[axis+3]+=brim_width
        xy=self.place(placement,origin,clearance)
        objid=max([int(x.get('id','0')) for x in self.resources]+[0])+1
        manifest_lanes=[]
        overrides={'wall_loops':'2','sparse_infill_density':'100%',
          'top_shell_layers':'0','bottom_shell_layers':'0',
          'top_shell_thickness':'0','bottom_shell_thickness':'0',
          'enable_support':'0','brim_type':'outer_only','brim_width':f'{brim_width:g}','brim_object_gap':'0','ironing_type':'no ironing',
          'flush_into_infill':'0','flush_into_objects':'0','flush_into_support':'0'}
        name='IPT_ASSEMBLY'
        topid=objid;objid+=1
        top=ET.Element(Q('object'),{'id':str(topid),'type':'model','name':name})
        components=ET.SubElement(top,Q('components'))
        cfg=ET.Element('object',{'id':str(topid)})
        for key,value in {'name':name,'extruder':str(petg),**overrides,**{key:'0' for key in FEATURE_KEYS}}.items():set_meta(cfg,key,value)
        # Each material part contains all the vertical spans assigned to that
        # tool. This preserves donor assignments inside one movable object.
        material_meshes={slot:[] for slot in order}
        for k,slot in enumerate(order):
            lane_name=f'IPT_F{slot:02d}_'+materials[slot]
            if schedule:
                spans=[]
                for row in schedule:
                    owner=row['tools'][k]+1;lo=row['min_z'];hi=min(height,row['max_z'])
                    if hi<=lo:continue
                    if spans and spans[-1][0]==owner:spans[-1][2]=hi
                    else:spans.append([owner,lo,hi])
                for owner,lo,hi in spans:material_meshes[owner]+=lane_slab(lanes[k],height,lo,hi)
            else:material_meshes[slot]+=lanes[k]
            manifest_lanes.append({'filament_slot':slot,'tool':slot-1,'material':materials[slot],
                'name':lane_name,'bounds':[xy[0]+WALL,xy[1]+WALL+k*lane_width,xy[0]+WALL+width,xy[1]+WALL+(k+1)*lane_width]})
        meshes=[(f'IPT_F{slot:02d}_material',slot,material_meshes[slot]) for slot in order]
        meshes.append(('IPT_'+materials[petg]+'_frame',petg,frame))
        for part_name,slot,triangles in meshes:
            if not triangles:continue
            meshid=objid;objid+=1
            meshobj=ET.SubElement(self.resources,Q('object'),{'id':str(meshid),'type':'model'})
            mesh_xml(meshobj,triangles)
            ET.SubElement(components,Q('component'),{'objectid':str(meshid)})
            part=ET.SubElement(cfg,'part',{'id':str(meshid),'subtype':'normal_part'})
            for key,value in {'name':part_name,'extruder':str(slot),**{key:str(slot) for key in FEATURE_KEYS}}.items():set_meta(part,key,value)
            ET.SubElement(part,'mesh_stat',{'face_count':str(len(triangles))})
        self.resources.append(top)
        item=ET.Element(Q('item'),{'objectid':str(topid),'transform':f'1 0 0 0 1 0 0 0 1 {xy[0]+self.plate_offset[0]} {xy[1]+self.plate_offset[1]} 0','printable':'1'})
        self.build.insert(0,item);self.config.insert(0,cfg)
        instance=ET.SubElement(self.plate,'model_instance')
        for key,value in [('object_id',topid),('instance_id',0),('identify_id',topid+100000)]:set_meta(instance,key,value)
        # Orca indexes these files by build-object order, not 3MF resource ID.
        range_path='Metadata/layer_config_ranges.xml'
        ranges=ET.fromstring(self.files[range_path]) if range_path in self.files else ET.Element('objects')
        for obj in ranges.findall('object'):obj.set('id',str(int(obj.get('id'))+1))
        for path in ('Metadata/layer_heights_profile.txt','Metadata/brim_ear_points.txt'):
            if path in self.files:
                self.files[path]=re.sub(r'object_id=(\d+)',lambda m:'object_id='+str(int(m[1])+1),self.files[path].decode()).encode()
        if schedule:
            profile_path='Metadata/layer_heights_profile.txt'
            self.files[profile_path]=(layer_profile(schedule)+self.files.get(profile_path,b'').decode()).encode()
        cut_path='Metadata/cut_information.xml'
        if cut_path in self.files:
            cut=ET.fromstring(self.files[cut_path])
            for obj in cut.findall('object'):obj.set('id',str(int(obj.get('id'))+1))
            self.files[cut_path]=xml_bytes(cut)
        if len(ranges):self.files[range_path]=xml_bytes(ranges)
        for key,value in [('print_sequence','by layer'),('print_order','as_obj_list'),
                          ('flush_into_objects','0'),('flush_into_infill','0'),('flush_into_support','0'),('exclude_object','1')]:
            self.settings[key]=value
        for key in ('print_sequence','print_order'):
            set_meta(self.plate,key,self.settings[key])
        set_meta(self.plate,'enable_prime_tower','0')
        # Discard stale preview/toolpath references; do not retain old sliced output.
        for child in list(self.plate):
            if child.tag=='metadata' and child.get('key') in ('gcode_file','thumbnail_file','thumbnail_no_light_file','top_file','pick_file','first_layer_print_sequence','other_layers_print_sequence','other_layers_print_sequence_nums'):
                self.plate.remove(child)
        if any(node.get(attribute)!=value for node,attribute,value in preserved_positions):
            raise TowerError('Internal error: an original object position changed while adding the tower.')
        self.files[MODEL]=xml_bytes(self.root);self.files[SETTINGS]=json.dumps(self.settings,indent=2).encode();self.files[MODEL_SETTINGS]=xml_bytes(self.config)
        manifest={'format_version':3,'assembly_name':'IPT_ASSEMBLY','status':'requires_exported_gcode_audit','geometry':'v10_multimaterial_variable_layers','reference_height':self.reference_bounds[5],
            'outer_box_dimensions':[width+2*WALL,len(order)*lane_width+2*WALL],
            'width':width,'rib_size':rib_size,'purge_volume':purge_volume,'serpentine_runs':runs,
            'frame_slot':petg,'frame_tool':petg-1,'frame_material':materials[petg],
            'support_slot':support_slot,'detected_support_slots':support_slots,
            'brim_width':brim_width,'brim_gap':0.,'first_layer_height':first,
            'plate_number':self.plate_number,'plate_offset':self.plate_offset,
            'tower_height':height,'origin':list(xy),'petg_slot':petg,'petg_tool':petg-1,
            'lane_width':lane_width,'lanes':manifest_lanes,'tower_bounds':[xy[0]+b[0],xy[1]+b[1],xy[0]+b[3],xy[1]+b[4]],
            'every_filament_every_layer':schedule is None,'every_section_every_layer':True,
            'reference_layer_tools':{str(z):tools for z,tools in layers.items()} if layers is not None else None,
            'filament_diameters':self.settings.get('filament_diameter',['1.75']*MAX_TOOLS),
            'layer_schedule':schedule,'tool_mapping':'Orca slot n -> T(n-1); verify against printer profile',
            'limitations':['No native plugin API in Orca 2.4.2.','Requires G-code audit before use as prime tower replacement.',
              'A later pickup on the same layer may lack another section pass; audit rejects it.',
              'Tower is ordinary geometry; no automatic adaptive per-pickup volume or scheduling.']}
        self.files[MANIFEST]=json.dumps(manifest,indent=2).encode()
        output=Path(output)
        if output.exists():raise TowerError('Output already exists; choose a new filename.')
        sidecar=Path(str(output)+'.ipt.json')
        if sidecar.exists():raise TowerError('Audit metadata file already exists; choose a new output filename.')
        output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED) as z:
            for name,data in self.files.items():
                if name.lower().endswith(('.gcode','.gcode.3mf')) or name=='Metadata/slice_info.config':continue
                z.writestr(name,data)
        with sidecar.open('x') as stream:json.dump(manifest,stream,indent=2)
        return manifest


def load_manifest(path):
    with zipfile.ZipFile(path) as z:
        try:manifest=json.loads(z.read(MANIFEST))
        except KeyError:
            sidecar=Path(str(path)+'.ipt.json')
            if not sidecar.exists():raise TowerError('Project has no isolated tower manifest or companion .3mf.ipt.json file. Use the original utility-generated project, or regenerate from the original reference project with a new output filename.')
            manifest=json.loads(sidecar.read_text())
        if not manifest.get('assembly_name'):return manifest
        if manifest.get('format_version',0)>=3:
            types=json.loads(z.read(SETTINGS)).get('filament_type',[])
            for lane in manifest['lanes']:
                slot=lane['filament_slot']
                if slot>len(types) or material_key(types[slot-1])!=lane['material']:
                    raise TowerError('Filament material types changed; regenerate the tower.')
        config=ET.fromstring(z.read(MODEL_SETTINGS));root=ET.fromstring(z.read(MODEL))
        objects={o.get('id'):o for o in config.findall('object')}
        ids=[oid for oid,o in objects.items() if metadata(o).get('name')==manifest['assembly_name']]
        items=[i for i in root.findall('./'+Q('build')+'/'+Q('item')) if i.get('objectid') in ids]
        if len(ids)!=1 or len(items)!=1:raise TowerError('Keep exactly one IPT_ASSEMBLY object with its original name.')
        matrix=affine(items[0].get('transform'))
        if any(abs(matrix[i][j]-(1 if i==j else 0))>1e-6 for i in range(3) for j in range(3)):
            raise TowerError('The audit supports moving the assembly in XY, not rotating or scaling it.')
        reader=Project.__new__(Project);reader.objects=objects
        reader.models={n:{o.get('id'):o for o in ET.fromstring(z.read(n)).findall('./'+Q('resources')+'/'+Q('object'))} for n in z.namelist() if n.endswith('.model')}
        points=reader.vertices(MODEL,items[0].get('objectid'),matrix,set())
        offset=manifest.get('plate_offset',[0.,0.])
        points=[(x-offset[0],y-offset[1],z) for x,y,z in points]
        if not points:raise TowerError('Tower assembly has no geometry.')
        b=[min(p[i] for p in points) for i in range(3)]+[max(p[i] for p in points) for i in range(3)]
        old=manifest['tower_bounds']
        if abs(b[3]-b[0]-(old[2]-old[0]))>.01 or abs(b[4]-b[1]-(old[3]-old[1]))>.01 or abs(b[2])>.01 or abs(b[5]-manifest['tower_height'])>.01:
            raise TowerError('Tower dimensions/height changed; regenerate rather than editing its parts.')
        dx,dy=round(b[0]-old[0],5),round(b[1]-old[1],5)
        for lane in manifest['lanes']:
            lane['bounds']=[v+(dx if i%2==0 else dy) for i,v in enumerate(lane['bounds'])]
        manifest['tower_bounds']=[v+(dx if i%2==0 else dy) for i,v in enumerate(old)]
        manifest['origin']=[manifest['origin'][0]+dx,manifest['origin'][1]+dy]
        return manifest

def audit(gcode,manifest,min_volume=None):
    """Conservative checker for plain Orca G-code and literal T0..T11 selections.

    No motion/macro commands are inserted or changed. Unknown/unsupported motion
    is rejected rather than silently declared safe. This is not collision simulation.
    """
    min_volume=manifest.get('purge_volume',5.) if min_volume is None else min_volume
    if not math.isfinite(min_volume) or min_volume<=0:raise TowerError('Minimum priming volume must be positive.')
    lanes=manifest['lanes'];allowed={x['tool'] for x in lanes};petg=manifest.get('frame_tool',manifest.get('petg_tool'));tb=manifest['tower_bounds']
    errors=[];warnings=[];layer=None;seen={};layers=set();xy=None;z=0.;tool=None;epos=0.;relative=False;absolute_xyz=True;primed=0.;pending=False
    max_tower_z=0.;checks=0;toolchanges=0;reference_seen={};frame_seen=set()
    strict=manifest.get('format_version',0)>=3
    first_xy_pending=False;first_extrusion_pending=False;pickup_line=None
    first_xy_checks=0;first_extrusion_checks=0;feature=None
    brim=manifest.get("brim_width",0.)
    brim_bounds=[tb[0]-brim,tb[1]-brim,tb[2]+brim,tb[3]+brim]
    def brim_layer():return brim>0 and layer is not None and layer<=manifest.get("first_layer_height",.2)+.001
    def destination_bounds():return brim_bounds if brim_layer() and tool==petg else tb
    schedule={round(row['z'],5):row for row in manifest.get('layer_schedule') or []}
    def fail(line,msg):
        if len(errors)<100:errors.append(f'Line {line}: {msg}')
    def inside(p,b,tol=.03):return b[0]-tol<=p[0]<=b[2]+tol and b[1]-tol<=p[1]<=b[3]+tol
    def crosses_interior(p,q,b):
        low,high=0.,1.
        for axis in (0,1):
            lo=b[axis]+.05;hi=b[axis+2]-.05;delta=q[axis]-p[axis]
            if abs(delta)<1e-12:
                if not lo<p[axis]<hi:return False
            else:
                aa,bb=sorted(((lo-p[axis])/delta,(hi-p[axis])/delta))
                low=max(low,aa);high=min(high,bb)
                if high<=low:return False
        return True
    diameters=manifest.get('filament_diameters',['1.75']*MAX_TOOLS)
    if not isinstance(diameters,list):diameters=[diameters]*MAX_TOOLS
    def filament_area(tool):
        diameter=float(diameters[min(tool,len(diameters)-1)])
        if not math.isfinite(diameter) or diameter<=0:raise TowerError('Invalid filament diameter in manifest.')
        return math.pi*(diameter/2)**2
    with open(gcode,encoding='utf-8',errors='replace') as stream:
        for lineno,line in enumerate(stream,1):
            if line.startswith('; Z:') or line.startswith(';Z:'):
                try:
                    layer=round(float(line.split(':',1)[1].strip()),5)
                    if layer<=manifest['tower_height']+.025:layers.add(layer)
                except ValueError:fail(lineno,'Unrecognized layer-height comment.')
            tag=re.match(r'\s*;\s*(?:FEATURE|TYPE)\s*:\s*(.*?)\s*$',line,re.IGNORECASE)
            if tag:feature=tag[1].lower()
            code=line.split(';',1)[0].strip()
            if not code:continue
            match=re.fullmatch(r'T(\d+)',code)
            if match:
                new=int(match.group(1))
                if new not in allowed:fail(lineno,f'T{new} has no assigned tower section.')
                if new!=tool:
                    if strict and tool is not None and primed<min_volume:fail(lineno,f'T{tool} was dropped after only {primed:.2f} mm³ tower priming; need {min_volume:.2f}.')
                    if first_xy_pending or first_extrusion_pending:
                        fail(lineno,f'Pickup at line {pickup_line} was not followed by a verified tower visit and extrusion before the next pickup.')
                    toolchanges+=1;primed=0.;pending=True
                    first_xy_pending=True;first_extrusion_pending=True;pickup_line=lineno
                tool=new;continue
            command=code.split()[0]
            if command=='EXCLUDE_OBJECT_START' and first_extrusion_pending:
                obj=re.search(r'\bNAME=([^\s]+)',code)
                if obj and not obj[1].strip('"').startswith('IPT_'):
                    fail(lineno,f'Model/support object selected before tower extrusion after pickup at line {pickup_line}.')
            if command=='M82':relative=False;continue
            if command=='M83':relative=True;continue
            if command=='G90':absolute_xyz=True;continue
            if command=='G91':absolute_xyz=False;continue
            args={k:float(v) for k,v in re.findall(r'([XYZEF])\s*(-?(?:\d+(?:\.\d*)?|\.\d+))',code)}
            if command=='G92':
                if 'E' in args:epos=args['E']
                if 'X' in args or 'Y' in args:fail(lineno,'XY coordinate reset is unsupported.')
                continue
            if command in ('G2','G3'):
                fail(lineno,'Arc moves are not audited; disable arc fitting before export.');continue
            if command not in ('G0','G1'):continue
            if not absolute_xyz:fail(lineno,'Relative XYZ motion is unsupported.');continue
            previous=xy
            if 'X' in args or 'Y' in args:
                xy=(args.get('X',xy[0] if xy else 0.),args.get('Y',xy[1] if xy else 0.))
                if first_xy_pending:
                    first_xy_checks+=1;first_xy_pending=False
                    if not inside(xy,destination_bounds()):fail(lineno,f'First XY destination after T{tool} pickup at line {pickup_line} is outside the prime tower.')
            z=args.get('Z',z)
            e=args.get('E',0 if relative else epos);de=e if relative else e-epos
            if 'E' in args:epos=e if not relative else epos+e
            if de>0 and previous is not None and xy is not None and math.dist(previous,xy)>1e-7 and first_extrusion_pending:
                first_extrusion_checks+=1;first_extrusion_pending=False
                if not (inside(previous,destination_bounds()) and inside(xy,destination_bounds())):
                    fail(lineno,f'First extrusion path after T{tool} pickup at line {pickup_line} is outside the prime tower (model/support or other path first).')
            if de<=0 or previous is None or xy is None or math.dist(previous,xy)<1e-7 or layer is None:continue
            if tool is None:fail(lineno,'Extrusion before an explicit Tn tool selection.');continue
            volume=de*filament_area(tool)
            if brim_layer() and feature in ("brim","skirt/brim") and inside(previous,brim_bounds) and inside(xy,brim_bounds):
                if tool!=petg:fail(lineno,"Tower outer brim must use the assigned frame tool.")
                if any(crosses_interior(previous,xy,lane["bounds"]) for lane in lanes):fail(lineno,"Outer brim crosses a tower section interior.")
                primed+=volume
                continue
            touched=[lane for lane in lanes if inside(previous,lane['bounds']) or inside(xy,lane['bounds'])]
            whole=next((lane for lane in lanes if inside(previous,lane['bounds']) and inside(xy,lane['bounds'])),None)
            in_tower=inside(previous,tb) and inside(xy,tb)
            if in_tower:
                row=schedule.get(layer)
                if schedule and row is None:fail(lineno,'Tower layer does not match the donor schedule; regenerate from the current reference slice.')
                expected=row['tools'] if row else [lane['tool'] for lane in lanes]
                for index,lane in enumerate(lanes):
                    if expected[index]!=tool and crosses_interior(previous,xy,lane['bounds']):
                        fail(lineno,f'T{tool} crosses the interior of section for T{lane["tool"]}.')
                if whole:
                    index=lanes.index(whole)
                    if expected[index]!=tool:fail(lineno,f'T{tool} extrudes in section for T{whole["tool"]}; expected T{expected[index]}.')
                    else:seen.setdefault(layer,set()).add(index)
                elif touched and tool!=petg:
                    # Adjacent donor sections may be sliced as one continuous
                    # region. Accept only a rectangle wholly owned by this tool.
                    groups=[]
                    for index,lane in sorted(enumerate(lanes),key=lambda pair:pair[1]['bounds'][1]):
                        if expected[index]!=tool:continue
                        b=lane['bounds']
                        if groups and all(abs(groups[-1][0][a]-b[a])<1e-6 for a in (0,2)) and abs(groups[-1][0][3]-b[1])<1e-6:
                            groups[-1][0][3]=b[3];groups[-1][1].append(index)
                        else:groups.append((list(b),[index]))
                    group=next((g for g in groups if inside(previous,g[0]) and inside(xy,g[0])),None)
                    if group:
                        for index in group[1]:
                            if crosses_interior(previous,xy,lanes[index]['bounds']):seen.setdefault(layer,set()).add(index)
                    else:fail(lineno,'Material extrusion crosses outside sections assigned to this tool.')
                elif tool!=petg:fail(lineno,'Outer frame/connections must use the assigned frame tool.')
                elif not touched:frame_seen.add(layer)
                if layer>manifest['tower_height']+.001:fail(lineno,'Tower extends beyond the last required priming layer.')
                primed+=volume;max_tower_z=max(max_tower_z,layer)
            else:
                if feature not in ('prime tower','wipe tower','skirt','brim','skirt/brim','custom'):
                    reference_seen.setdefault(layer,set()).add(tool)
                if pending:
                    checks+=1
                    if primed<min_volume:fail(lineno,f'T{tool} reaches the reference after only {primed:.2f} mm³ priming; need {min_volume:.2f}.')
                    pending=False
    if strict and tool is not None and primed<min_volume:
        errors.append(f'Final pickup received only {primed:.2f} mm³ tower priming; need {min_volume:.2f}.')
    if first_xy_pending or first_extrusion_pending:
        errors.append(f'Final pickup at line {pickup_line} has no verified tower visit and extrusion.')
    if checks==0:errors.append('No post-pickup reference extrusion was checked.')
    if not seen:errors.append('No tower-section extrusion detected; require standard Orca ; Z: layer comments.')
    # The last planned span may extend past the mesh to cover its partial top.
    # It is not itself evidence that Orca will emit an additional print layer.
    required_schedule={zz for zz in schedule if zz<=manifest['tower_height']+.025}
    for zz in sorted(layers | required_schedule):
        missing=set(range(len(lanes)))-seen.get(zz,set())
        if missing:errors.append(f'Layer Z={zz}: missing tower sections for '+', '.join('T'+str(lanes[x]['tool']) for x in sorted(missing)))
    if max_tower_z+0.025<manifest['tower_height']:
        errors.append(f'Tower reaches Z={max_tower_z:.3f}, below requested {manifest["tower_height"]:.3f}.')
    if strict:
        for zz in sorted(required_schedule-frame_seen):errors.append(f'Layer Z={zz}: missing outer-frame extrusion.')
        expected_reference=manifest.get('reference_layer_tools')
        if expected_reference is not None:
            expected={round(float(z),5):set(ts) for z,ts in expected_reference.items() if ts}
            if expected!=reference_seen:errors.append('Model/support layer heights or tool assignments changed from the reference slice. Regenerate the tower.')
    warnings.append('Checks explicit Tn commands and configured filament diameters. Does not evaluate firmware macro effects, collision clearance, or physical extrusion quality.')
    return {'passed':not errors,'errors':errors,'warnings':warnings,'layers_with_sections':len(seen),
            'tool_changes':toolchanges,'pickup_checks':checks,
            'first_destination_checks':first_xy_checks,'first_extrusion_checks':first_extrusion_checks,
            'minimum_prime_volume_mm3':min_volume,'max_tower_z':max_tower_z}

def gui():
    from tower_wizard import serve
    serve()

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command')
    g=sub.add_parser('generate',help='Add an adaptive tower to a COPY of a saved Orca project')
    g.add_argument('project');g.add_argument('--output',required=True);g.add_argument('--filaments',type=lambda s:list(map(int,s.split(','))))
    g.add_argument('--origin',type=float,nargs=2);g.add_argument('--clearance',type=float,default=8)
    g.add_argument('--reference-gcode',help='Original reference-only slice; enables same-material donors and matching layers')
    g.add_argument('--plate',type=int,help='1-based plate number in a multi-plate project')
    g.add_argument('--width',type=float,default=30.);g.add_argument('--rib-size',type=float,default=5.)
    g.add_argument('--purge-volume',type=float,default=5.);g.add_argument('--brim-width',type=float,default=5.)
    g.add_argument('--support-slot',type=int);g.add_argument('--frame-slot',type=int)
    a=sub.add_parser('audit',help='Check exported G-code without changing it')
    a.add_argument('--project',required=True);a.add_argument('--min-volume',type=float);a.add_argument('--report');a.add_argument('gcode')
    sub.add_parser('gui',help='Choose a project and output using file dialogs')
    args=parser.parse_args(argv)
    try:
        if args.command in (None,'gui'):gui();return 0
        if args.command=='generate':
            if Path(args.project).resolve()==Path(args.output).resolve():raise TowerError('The original project cannot be overwritten.')
            result=Project(args.project,args.plate).generate(args.output,args.filaments,args.origin,args.clearance,args.reference_gcode,args.width,args.rib_size,args.purge_volume,args.support_slot,args.frame_slot,args.brim_width)
            print(json.dumps(result,indent=2));return 0
        result=audit(args.gcode,load_manifest(args.project),args.min_volume)
        if args.report:Path(args.report).write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2));return 0 if result['passed'] else 2
    except (TowerError,OSError,ValueError,ET.ParseError,zipfile.BadZipFile) as e:
        print('Isolated Prime Tower: '+str(e),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
