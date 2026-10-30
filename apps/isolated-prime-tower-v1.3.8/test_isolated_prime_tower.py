"""Run: python3 -m unittest discover -s . -v"""
import json, math, tempfile, unittest, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import isolated_prime_tower as ipt

class ProjectTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def fixture(self,count=2,external=False):
  root=ET.Element(ipt.Q('model'),{'unit':'millimeter'})
  resources=ET.SubElement(root,ipt.Q('resources'));build=ET.SubElement(root,ipt.Q('build'))
  cfg=ET.Element('config');plate=ET.SubElement(cfg,'plate');ipt.set_meta(plate,'plater_id',1);extra={}
  for i in range(1,count+1):
   obj=ET.SubElement(resources,ipt.Q('object'),{'id':str(i),'type':'model'})
   triangles=ipt.prism([(0,0),(10,0),(10,10),(0,10)],0,10)
   if external:
    subroot=ET.Element(ipt.Q('model'),{'unit':'millimeter'});rs=ET.SubElement(subroot,ipt.Q('resources'))
    mesh=ET.SubElement(rs,ipt.Q('object'),{'id':'1','type':'model'});ipt.mesh_xml(mesh,triangles)
    path=f'3D/Objects/reference_{i}.model';extra[path]=ipt.xml_bytes(subroot)
    comps=ET.SubElement(obj,ipt.Q('components'))
    ET.SubElement(comps,ipt.Q('component'),{'objectid':'1','{'+ipt.PROD+'}path':'/'+path,'transform':'1 0 0 0 1 0 0 0 2 0 0 0'})
   else:ipt.mesh_xml(obj,triangles)
   ET.SubElement(build,ipt.Q('item'),{'objectid':str(i),'transform':f'1 0 0 0 1 0 0 0 1 {150+i*15} 150 0'})
   md=ET.SubElement(cfg,'object',{'id':str(i)});ipt.set_meta(md,'name','reference');ipt.set_meta(md,'extruder',i)
   instance=ET.SubElement(plate,'model_instance');ipt.set_meta(instance,'object_id',i);ipt.set_meta(instance,'instance_id',0)
  settings={'single_extruder_multi_material':'0','print_sequence':'by layer','filament_type':['PLA']*count+['PETG'],
   'enable_support':'1','support_interface_filament':str(count+1),'support_filament':'0',
   'printable_area':['0x0','350x0','350x350','0x350'],'printable_height':'350',
   'change_filament_gcode':'T{next_extruder}\n; EXISTING PICKUP TEMPLATE','machine_start_gcode':'; ORIGINAL START','enable_prime_tower':'1'}
  files={ipt.MODEL:ipt.xml_bytes(root),ipt.MODEL_SETTINGS:ipt.xml_bytes(cfg),ipt.SETTINGS:json.dumps(settings).encode(),
   '[Content_Types].xml':b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>',**extra}
  source=self.folder/'source.3mf'
  with zipfile.ZipFile(source,'w') as z:
   for name,data in files.items():z.writestr(name,data)
  return source,files
 def test_dynamic_counts_materials_and_original_unchanged(self):
  for count in range(1,12):
   source,files=self.fixture(count);original=source.read_bytes();output=self.folder/f'generated_{count}.3mf'
   manifest=ipt.Project(source).generate(output)
   self.assertEqual(count+1,len(manifest['lanes']));self.assertEqual(10,manifest['tower_height']);self.assertEqual(5,manifest['rib_size']);self.assertEqual(count+1,manifest['petg_slot']);self.assertEqual(original,source.read_bytes())
   with zipfile.ZipFile(output) as z:
    cfg=ET.fromstring(z.read(ipt.MODEL_SETTINGS));settings=json.loads(z.read(ipt.SETTINGS))
    self.assertEqual('by layer',settings['print_sequence']);self.assertEqual('as_obj_list',settings['print_order'])
    plate=ipt.metadata(cfg.find('plate'))
    self.assertEqual('by layer',plate['print_sequence']);self.assertEqual('as_obj_list',plate['print_order'])
    build=ET.fromstring(z.read(ipt.MODEL)).find(ipt.Q('build'))
    self.assertEqual(cfg.findall('object')[0].get('id'),build[0].get('objectid'))
    self.assertEqual(settings['change_filament_gcode'],'T{next_extruder}\n; EXISTING PICKUP TEMPLATE')
    self.assertEqual(settings['machine_start_gcode'],'; ORIGINAL START');self.assertEqual('1',settings['enable_prime_tower']);self.assertEqual('0',plate['enable_prime_tower'])
    objects=cfg.findall('object');self.assertEqual(count+1,len(objects))
    self.assertEqual('IPT_ASSEMBLY',ipt.metadata(objects[0])['name'])
    tower_settings=ipt.metadata(objects[0])
    self.assertEqual('outer_only',tower_settings['brim_type']);self.assertEqual('5',tower_settings['brim_width']);self.assertEqual('0',tower_settings['brim_object_gap']);self.assertEqual(5,manifest['brim_width'])
    parts=objects[0].findall('part')
    for lane,part in zip(manifest['lanes'],parts):
     md=ipt.metadata(part);self.assertEqual(str(lane['filament_slot']),md['extruder'])
     for key in ipt.FEATURE_KEYS:self.assertEqual(md[key],md['extruder'])
    self.assertEqual(count+2,len(parts))
    self.assertIn('_frame',ipt.metadata(parts[-1])['name'])
 def test_external_components_and_scaled_height(self):
  source,_=self.fixture(1,True);project=ipt.Project(source);self.assertEqual(project.reference_bounds[5],20)
  self.assertEqual(project.generate(self.folder/'external.3mf')['tower_height'],20)
 def test_intentionally_lowered_object_keeps_exact_z_position(self):
  source,files=self.fixture();root=ET.fromstring(files[ipt.MODEL]);items=root.find(ipt.Q('build')).findall(ipt.Q('item'))
  transform=items[0].get('transform').split();transform[11]='-2.5';items[0].set('transform',' '.join(transform));items[0].set('auto_drop','0')
  files[ipt.MODEL]=ipt.xml_bytes(root)
  with zipfile.ZipFile(source,'w') as z:
   for name,data in files.items():z.writestr(name,data)
  project=ipt.Project(source);self.assertEqual(-2.5,project.reference_bounds[2])
  output=self.folder/'lowered.3mf';project.generate(output)
  with zipfile.ZipFile(output) as z:generated=ET.fromstring(z.read(ipt.MODEL)).find(ipt.Q('build'))
  original={item.get('objectid'):dict(item.attrib) for item in items}
  preserved={item.get('objectid'):dict(item.attrib) for item in generated if item.get('objectid') in original}
  self.assertEqual(original,preserved)
 def test_audit_follows_saved_assembly_translation(self):
  source,_=self.fixture();output=self.folder/'moved.3mf'
  original=ipt.Project(source).generate(output)
  with zipfile.ZipFile(output) as z:files={n:z.read(n) for n in z.namelist() if n!=ipt.MANIFEST}
  root=ET.fromstring(files[ipt.MODEL]);item=root.find('./'+ipt.Q('build')+'/'+ipt.Q('item'))
  transform=list(map(float,item.get('transform').split()));transform[9]+=15;transform[10]+=25
  item.set('transform',' '.join(map(str,transform)));files[ipt.MODEL]=ipt.xml_bytes(root)
  with zipfile.ZipFile(output,'w') as z:
   for name,data in files.items():z.writestr(name,data)
  moved=ipt.load_manifest(output)
  for a,b in zip(original['lanes'],moved['lanes']):
   self.assertEqual([v+(15 if i%2==0 else 25) for i,v in enumerate(a['bounds'])],b['bounds'])
  transform[0]=0;transform[1]=1;transform[3]=-1;transform[4]=0
  item.set('transform',' '.join(map(str,transform)));files[ipt.MODEL]=ipt.xml_bytes(root)
  with zipfile.ZipFile(output,'w') as z:
   for name,data in files.items():z.writestr(name,data)
  with self.assertRaisesRegex(ipt.TowerError,'rotating'):ipt.load_manifest(output)
 def test_closed_slabs_preserve_base_and_serpentine_heights(self):
  _,lanes=ipt.geometry(3,11)
  for lo,hi in ((0,.2),(.8,1.2),(2,3)):
   slab=ipt.lane_slab(lanes[0],11,lo,hi);b=ipt.bounds(slab)
   self.assertEqual(lo,b[2]);self.assertEqual(hi,b[5])
   # Each triangle edge in the independent closed solids has a partner.
   from collections import Counter
   edges=Counter()
   for tri in slab:
    for i in range(3):edges[tuple(sorted((tri[i],tri[(i+1)%3])))]+=1
   self.assertTrue(all(n%2==0 for n in edges.values()))
   if lo>=1:self.assertEqual(len(lanes[0])-12,len(slab))
 def test_no_overwrite_or_recursive_tower(self):
  source,_=self.fixture();output=self.folder/'tower.3mf';ipt.Project(source).generate(output)
  with self.assertRaises(ipt.TowerError):ipt.Project(output)
  with self.assertRaises(ipt.TowerError):ipt.Project(source).generate(output)
 def test_manifest_survives_orca_removal_via_sidecar(self):
  source,_=self.fixture();output=self.folder/'tower.3mf'
  manifest=ipt.Project(source).generate(output)
  self.assertEqual(manifest,ipt.load_manifest(output))
  with zipfile.ZipFile(output) as z:files={n:z.read(n) for n in z.namelist() if n!=ipt.MANIFEST}
  with zipfile.ZipFile(output,'w') as z:
   for name,data in files.items():z.writestr(name,data)
  self.assertEqual(manifest,ipt.load_manifest(output))
  Path(str(output)+'.ipt.json').unlink()
  with self.assertRaisesRegex(ipt.TowerError,'regenerate'):ipt.load_manifest(output)
 def test_twelfth_tool_and_thirteenth_tool_limit(self):
  source,_=self.fixture(11);project=ipt.Project(source)
  order,petg=project.used_filaments();self.assertEqual(list(range(1,13)),order);self.assertEqual(12,petg)
  rows=ipt.donor_schedule({.2:[10,11]},order,petg,1.,.2,.2)
  self.assertEqual([10]*11+[11],rows[0]['tools'])
  ref=self.folder/'nine_tools.gcode';ref.write_text('M83\nT8\nG0 X1 Y1\n; Z: .2\n;TYPE:Support interface\nG1 X2 E1\n')
  self.assertEqual({.2:[8]},ipt.reference_layers(ref,set(range(9))))
  source,_=self.fixture(12)
  with self.assertRaises(ipt.TowerError):ipt.Project(source).used_filaments()
  with self.assertRaises(ipt.TowerError):ipt.geometry(13,11)
 def test_reject_wrong_materials(self):
  source,_=self.fixture();project=ipt.Project(source);project.settings['filament_type']=['PLA','ABS','PETG']
  order,frame=project.used_filaments();self.assertEqual(2,frame);self.assertEqual({1,2,3},set(order))
  project.settings['filament_type']=['PLA','PLA','PETG']
  self.assertEqual([1,2],project.used_filaments([1,2])[0])
  project.settings['filament_type']=['PVA','BVOH','Support for PLA']
  with self.assertRaises(ipt.TowerError):project.used_filaments()
 def test_model_placement_and_bed_bounds(self):
  source,_=self.fixture();project=ipt.Project(source)
  with self.assertRaises(ipt.TowerError):project.generate(self.folder/'invalid.3mf',origin=(-100,-100))
  manifest=project.generate(self.folder/'valid.3mf');b=manifest['tower_bounds'];self.assertGreaterEqual(min(b),3);self.assertLessEqual(max(b),347)
 def test_geometry_dimensions(self):
  for count in range(2,10):
   frame,lanes=ipt.geometry(count,21);self.assertEqual(len(lanes),count)
   for triangles in lanes:
    b=ipt.bounds(triangles);self.assertAlmostEqual(b[3]-b[0],30,places=5);self.assertAlmostEqual(b[4]-b[1],ipt.LANE,places=5);self.assertEqual(b[5],21)
    self.assertTrue(all(math.isfinite(v) for t in triangles for p in t for v in p))
   # Initial ring is the outer rectangle; the rest is ribs and connectors.
   box=ipt.bounds(frame[:32]);self.assertAlmostEqual(box[4]-box[1],count*ipt.LANE+2*ipt.WALL)
   span=2*((12.5-3)*math.sqrt(2)+3)
   separation=count*ipt.LANE+2*ipt.WALL+2*12.1/math.sqrt(2)+2*ipt.rib_outward_shift(count)
   self.assertGreaterEqual(separation-span,1.-1e-8)
  self.assertEqual(0,ipt.rib_outward_shift(6))
 def test_donor_ranges_and_original_range_index(self):
  source,_=self.fixture(2);project=ipt.Project(source)
  project.files['Metadata/layer_config_ranges.xml']=b'<objects><object id="1"><range min_z="0" max_z="10"><option opt_key="wall_loops">3</option></range></object></objects>'
  ref=self.folder/'reference.gcode'
  lines=['G90','M83','G0 X100 Y100']
  for i in range(1,51):
   lines += [f'; Z: {i*.2:.3f}','; FEATURE: Outer wall','T1','G0 X100 Y100','G1 X110 Y100 E1']
   if i==2:lines += ['T0','G0 X100 Y100','G1 X110 Y100 E1']
   lines += ['; FEATURE: Support interface','T2','G0 X100 Y100','G1 X110 Y100 E1']
  ref.write_text('\n'.join(lines));out=self.folder/'donor.3mf'
  manifest=project.generate(out,reference_gcode=ref)
  self.assertEqual([1,1,2],manifest['layer_schedule'][0]['tools'])
  self.assertEqual([1,1,2],manifest['layer_schedule'][-1]['tools'])
  self.assertFalse(manifest['every_filament_every_layer'])
  with zipfile.ZipFile(out) as z:
   ranges=ET.fromstring(z.read('Metadata/layer_config_ranges.xml'))
   self.assertEqual('2',ranges[0].get('id'))
   self.assertEqual(1,len(ranges))
   cfg=ET.fromstring(z.read(ipt.MODEL_SETTINGS));model=ET.fromstring(z.read(ipt.MODEL))
   parts=cfg.findall('object')[0].findall('part')
   owner=next(p for p in parts if ipt.metadata(p)['extruder']=='1')
   mesh=model.find('./'+ipt.Q('resources')+'/'+ipt.Q('object')+f"[@id='{owner.get('id')}']/"+ipt.Q('mesh'))
   zs=[float(v.get('z')) for v in mesh.findall('./'+ipt.Q('vertices')+'/'+ipt.Q('vertex'))]
   self.assertAlmostEqual(.2,min(zs));self.assertAlmostEqual(.4,max(zs))
 def test_painted_project_infers_nonconsecutive_slots_from_extrusion(self):
  source,_=self.fixture(5);project=ipt.Project(source)
  next(project.resources.iter(ipt.Q('triangle'))).set('paint_color','1')
  ref=self.folder/'painted.gcode'
  lines=['G90','M83','T0','G0 X100 Y100']
  for i in range(1,51):
   lines += [f'; Z: {i*.2:.3f}','; FEATURE: Prime tower','T2','G0 X100 Y100','G1 X110 Y100 E1']
   for tool in (1,4,5):
    lines += ['; FEATURE: Support interface' if tool==5 else '; FEATURE: Outer wall',f'T{tool}','G0 X100 Y100','G1 X110 Y100 E1']
  ref.write_text('\n'.join(lines))
  result=project.generate(self.folder/'painted.3mf',reference_gcode=ref)
  self.assertEqual([2,5,6],[lane['filament_slot'] for lane in result['lanes']])
  self.assertEqual([1,4,5],[lane['tool'] for lane in result['lanes']])
  with self.assertRaisesRegex(ipt.TowerError,'must match'):
   ipt.Project(source).generate(self.folder/'mismatch.3mf',filaments=[1,2,5,6],reference_gcode=ref)

class DonorTests(unittest.TestCase):
 def test_modal_features_and_comment_spacing(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'modal.gcode'
   for tag in ('; FEATURE: Outer wall',';TYPE: Outer wall','  ; TYPE : Outer wall',';FEATURE: Outer wall'):
    path.write_text('G90\nM83\nT0\nG0 X1 Y1\n; Z: .2\n'+tag+'\nG1 X2 E1\n; Z: .4\nG1 X3 E1\n; TYPE: Prime tower\nT1\nG1 X4 E1\n; Z: .6\nG1 X5 E1\n;FEATURE: Support interface\nG1 X6 E1\n')
    self.assertEqual({.2:[0],.4:[0],.6:[1]},ipt.reference_layers(path,{0,1}))
 def test_unclassified_extrusion_reports_line(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'unknown.gcode'
   path.write_text('M83\nT0\nG0 X1 Y1\n; Z: .2\nG1 X2 E1\n')
   with self.assertRaisesRegex(ipt.TowerError,'line 5'):
    ipt.reference_layers(path,{0})
 def test_active_owners_idle_donor_and_petg_only_fallback(self):
  rows=ipt.donor_schedule({.2:[1,2],.4:[0,1,2],.6:[2]},[1,2,3],3,1.,.2,.2)
  self.assertEqual([1,1,2],rows[0]['tools'])
  self.assertEqual([0,1,2],rows[1]['tools'])
  self.assertEqual([1,1,2],rows[2]['tools'])
  self.assertEqual([1,1,2],rows[-1]['tools'])
 def test_adaptive_grid(self):
  rows=ipt.donor_schedule({.2:[0],.35:[1]},[1,2],2,.35)
  self.assertEqual([.2,.35],[r['z'] for r in rows]);self.assertEqual(.2,rows[1]['min_z'])
 def test_reference_excludes_prime_tower(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'ref.gcode'
   path.write_text('G90\nM83\n; Z: .2\nT0\nG0 X1 Y1\n; FEATURE: Prime tower\nG1 X2 E1\nT1\n; FEATURE: Outer wall\nG1 X3 E1\n')
   self.assertEqual({.2:[1]},ipt.reference_layers(path,{0,1}))

class AuditTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'job.gcode'
  self.manifest={'lanes':[{'tool':0,'bounds':[1,1,31,5]},{'tool':1,'bounds':[1,5,31,9]}],'petg_tool':1,'tower_bounds':[0,0,40,20],'tower_height':.4}
 def tearDown(self):self.temp.cleanup()
 def code(self):
  lines=['G90','M83']
  for z in (.2,.4):
   lines += [f'; Z: {z}',f'G0 Z{z}']
   for tool,y in [(0,2),(1,6)]:lines += [f'T{tool}',f'G0 X2 Y{y}',f'G1 X20 Y{y} E3','G0 X100 Y100','G1 X110 Y100 E1']
  return '\n'.join(lines)+'\n'
 def check_code(self,code):
  self.path.write_text(code);before=self.path.read_bytes();result=ipt.audit(self.path,self.manifest);self.assertEqual(before,self.path.read_bytes());return result
 def test_petg_brim_counts_as_first_tower_visit(self):
  self.manifest.update(brim_width=15,first_layer_height=.2)
  code=self.code().replace('; Z: 0.2','; Z: 0.2\nT1\n;TYPE:Brim\nG0 X-2 Y-2\nG1 X-1 Y-2 E3\n;TYPE:Outer wall')
  self.assertTrue(self.check_code(code)['passed'])
  self.assertFalse(self.check_code(code.replace('G1 X-1 Y-2 E3','G1 X-30 Y-2 E3'))['passed'])
 def test_pass_and_no_mutation(self):
  r=self.check_code(self.code());self.assertTrue(r['passed'],r);self.assertEqual(r['pickup_checks'],4)
  self.assertEqual(4,r['first_destination_checks']);self.assertEqual(4,r['first_extrusion_checks'])
 def test_reject_model_detour_even_if_tower_primes_before_model(self):
  r=self.check_code(self.code().replace('T0\n','T0\nG0 X100 Y100\n',1))
  self.assertFalse(r['passed']);self.assertTrue(any('First XY destination' in e for e in r['errors']))
 def test_reject_model_or_support_extrusion_before_tower(self):
  r=self.check_code(self.code().replace('T1\n','T1\nG0 X100 Y100\nG1 X110 Y100 E1\n',1))
  self.assertFalse(r['passed']);self.assertTrue(any('First extrusion path' in e for e in r['errors']))
 def test_reject_object_marker_before_tower_even_inside_bounds(self):
  r=self.check_code(self.code().replace('T0\n','T0\nEXCLUDE_OBJECT_START NAME=reference_model\n',1))
  self.assertFalse(r['passed']);self.assertTrue(any('object selected' in e for e in r['errors']))
 def test_reject_pickup_without_subsequent_tower_pass(self):
  r=self.check_code(self.code()+'T0\n')
  self.assertFalse(r['passed']);self.assertTrue(any('Final pickup' in e for e in r['errors']))
 def test_z_lift_before_tower_is_allowed(self):
  r=self.check_code(self.code().replace('T0\n','T0\nG0 Z1\n',1))
  self.assertTrue(r['passed'],r)
 def test_reject_insufficient_prime(self):
  r=self.check_code(self.code().replace('G1 X20 Y2 E3','G1 X20 Y2 E.1',1));self.assertFalse(r['passed']);self.assertTrue(any('reaches the reference' in e for e in r['errors']))
 def test_reject_wrong_tool_in_lane(self):
  r=self.check_code(self.code().replace('G0 X2 Y2\nG1 X20 Y2','G0 X2 Y6\nG1 X20 Y6',1));self.assertFalse(r['passed']);self.assertTrue(any('section for' in e for e in r['errors']))
 def test_petg_cannot_cross_pla_lane(self):
  code=self.code()+'T1\nG0 X0 Y2\nG1 X35 Y2 E3\n'
  r=self.check_code(code);self.assertFalse(r['passed']);self.assertTrue(any('crosses the interior' in e for e in r['errors']))
 def test_reject_later_unprimed_pickup(self):
  r=self.check_code(self.code()+'T0\nG0 X100 Y100\nG1 X110 Y100 E1\n');self.assertFalse(r['passed']);self.assertTrue(any('only 0.00' in e for e in r['errors']))
 def test_reject_arcs(self):
  r=self.check_code(self.code()+'; Z: .6\nG2 X1 Y2 I3 J4 E1\n');self.assertFalse(r['passed']);self.assertTrue(any('Arc moves' in e for e in r['errors']))
 def test_missing_one_filament_on_layer(self):
  code=self.code();r=self.check_code(code[:code.rfind('T1')]);self.assertFalse(r['passed']);self.assertTrue(any('missing tower sections' in e for e in r['errors']))
 def test_absolute_extrusion(self):
  code=self.code().replace('M83','M82').replace('E3','E3\nG92 E0').replace('E1','E1\nG92 E0')
  r=self.check_code(code);self.assertTrue(r['passed'],r)
 def test_continuous_path_through_adjacent_donor_sections(self):
  self.manifest['lanes']=[{'tool':0,'bounds':[1,1,31,5]}, {'tool':2,'bounds':[1,5,31,9]}, {'tool':1,'bounds':[1,9,31,13]}]
  self.manifest['layer_schedule']=[{'z':z,'tools':[0,0,1]} for z in (.2,.4)]
  code=self.code().replace('G1 X20 Y2 E3','G1 X20 Y8 E3').replace('Y6','Y10')
  self.assertTrue(self.check_code(code)['passed'])
  self.assertFalse(self.check_code(code.replace('Y8','Y11'))['passed'])
  self.manifest['layer_schedule'][0]['tools']=[0,2,1]
  self.assertFalse(self.check_code(code)['passed'])
 def test_schedule_span_above_mesh_does_not_require_phantom_layer(self):
  self.manifest['layer_schedule']=[{'z':z,'tools':[0,1]} for z in (.2,.4,.6)]
  self.assertTrue(self.check_code(self.code())['passed'])
  self.assertFalse(self.check_code(self.code().split('; Z: 0.4')[0])['passed'])
 def test_donor_must_fill_each_lane_and_keep_petg_separate(self):
  self.manifest['lanes'].insert(1,{'tool':2,'bounds':[1,9,31,13]})
  self.manifest['layer_schedule']=[{'z':z,'tools':[0,0,1]} for z in (.2,.4)]
  # Add donor fill after the PLA pass on each layer, never after PETG.
  code=self.code().replace('G1 X20 Y2 E3','G1 X20 Y2 E3\nG0 X2 Y10\nG1 X20 Y10 E3')
  self.assertTrue(self.check_code(code)['passed'])
  self.assertFalse(self.check_code(self.code())['passed'])
  wrong=code.replace('G0 X2 Y10','T1\nG0 X2 Y10',1)
  self.assertFalse(self.check_code(wrong)['passed'])

if __name__=='__main__':unittest.main()
