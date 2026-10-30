import json, math, tempfile, unittest, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import isolated_prime_tower as ipt
import tower_wizard as wizard
import test_isolated_prime_tower as core

class MultiMaterialTests(unittest.TestCase):
 setUp=core.ProjectTests.setUp
 tearDown=core.ProjectTests.tearDown
 fixture=core.ProjectTests.fixture
 def reference(self,tools):
  path=self.folder/'reference.gcode';lines=['M83','G0 X100 Y100']
  for z,used in tools:
   lines.append(f'; Z: {z}')
   for t in used:
    lines += [f'T{t}',';TYPE:Support interface' if t==2 else ';TYPE:Outer wall','G0 X100 Y100','G1 X110 E1']
  path.write_text('\n'.join(lines));return path
 def test_adaptive_profile_shortest_tower_and_original_profile_preserved(self):
  source,_=self.fixture();project=ipt.Project(source)
  original='object_id=1|0;0.2;10;0.2;10;0.2\n'
  project.files['Metadata/layer_heights_profile.txt']=original.encode()
  reference=self.reference([(.2,[0,1,2]),(.35,[0]),(.6,[1])]+[(round(.6+i*.2,5),[1]) for i in range(1,48)])
  output=self.folder/'adaptive.3mf';m=project.generate(output,reference_gcode=reference,support_slot=3,width=35,rib_size=20,purge_volume=8)
  self.assertEqual(.6,m['tower_height']);self.assertEqual([.2,.35,.6],[r['z'] for r in m['layer_schedule']])
  self.assertEqual(3,m['frame_slot']);self.assertEqual(3,m['support_slot']);self.assertEqual(36.6,m['outer_box_dimensions'][0])
  with zipfile.ZipFile(output) as z:
   profile=z.read('Metadata/layer_heights_profile.txt').decode()
   self.assertIn(original.replace('object_id=1','object_id=2'),profile)
   values=list(map(float,profile.splitlines()[0].split('|')[1].split(';')))
  # Reproduce Orca's sampling at print_z + half minimum layer height.
  printed=.2;zs=[printed];idx=0
  while printed+.035<m['tower_height']:
   sample=printed+.035
   while idx+2<len(values) and sample>=values[idx+2]:idx+=2
   h=values[idx+1]
   if idx+2<len(values):h+=(values[idx+3]-h)*(sample-values[idx])/(values[idx+2]-values[idx])
   printed=round(printed+h,5);zs.append(printed)
  self.assertEqual([.2,.35,.6],zs)
 def test_no_cross_material_donors_with_twelve_tools(self):
  mats=['PLA','PLA','PETG','ASA','ABS','NYLON','PPS','PVA','BVOH','PLA-CF','PA6-CF','Support for PLA']
  materials={i+1:ipt.material_key(v) for i,v in enumerate(mats)}
  rows=ipt.donor_schedule({.2:list(range(12)),.35:[1,4],.55:[6]},list(range(1,13)),7,.55,materials=materials)
  self.assertEqual(1,rows[1]['tools'][0])
  for row in rows:
   for slot,owner in enumerate(row['tools'],1):self.assertEqual(materials[slot],materials[owner+1])
  self.assertEqual(7,ipt.choose_frame(list(materials),materials))
  with self.assertRaises(ipt.TowerError):ipt.choose_frame(list(materials),materials,8)
 def test_twelve_material_generation_and_saved_type_change_rejected(self):
  source,_=self.fixture(11);project=ipt.Project(source)
  project.settings['filament_type']=['PLA','PLA','PETG','ASA','ABS','NYLON','PPS','PVA','BVOH','PLA-CF','PA6-CF','Support for PLA']
  ref=self.reference([(.2,list(range(12))),(.4,[0])]+[(round(.4+i*.2,5),[0]) for i in range(1,49)])
  output=self.folder/'twelve.3mf';m=project.generate(output,reference_gcode=ref)
  self.assertEqual(12,len(m['lanes']));self.assertEqual(7,m['frame_slot']);self.assertEqual(.4,m['tower_height'])
  self.assertEqual(m,ipt.load_manifest(output))
  with zipfile.ZipFile(output) as z:files={n:z.read(n) for n in z.namelist()}
  settings=json.loads(files[ipt.SETTINGS]);settings['filament_type'][0]='ABS';files[ipt.SETTINGS]=json.dumps(settings).encode()
  with zipfile.ZipFile(output,'w') as z:
   for n,data in files.items():z.writestr(n,data)
  with self.assertRaisesRegex(ipt.TowerError,'material types changed'):ipt.load_manifest(output)
 def test_one_layer_profile_is_importable(self):
  profile=ipt.layer_profile([{'min_z':0,'max_z':.2}])
  values=profile.strip().split('|')[1].split(';')
  self.assertGreater(len(values),4);self.assertEqual(0,len(values)%2)
 def test_volume_sizes_path_and_ribs_stay_separate(self):
  small,n=ipt.lane_dimensions(30,5,.2);large,m=ipt.lane_dimensions(30,40,.1)
  self.assertGreater(large,small);self.assertGreater(m,n)
  frame,lanes=ipt.geometry(2,.4,20,40,large,m)
  self.assertAlmostEqual(.4,ipt.bounds(lanes[0])[5]);self.assertAlmostEqual(20,ipt.bounds(lanes[0])[3]-ipt.bounds(lanes[0])[0])
  for count,lane in ((2,large),(1,20)):
   span=2*((20-3)*math.sqrt(2)+3)
   separation=2*ipt.WALL+count*lane+2*19.6/math.sqrt(2)+2*ipt.rib_outward_shift(count,lane,40)
   self.assertGreaterEqual(separation-span,1-1e-8)
 def test_reject_unprintably_small_union_layer(self):
  source,_=self.fixture();p=ipt.Project(source);p.settings['min_layer_height']=['.1']*3
  reference=self.reference([(.2,[0,1,2]),(.25,[0]),(10,[1])])
  with self.assertRaisesRegex(ipt.TowerError,'slot 1 permits'):p.generate(self.folder/'bad.3mf',reference_gcode=reference)
 def test_material_picker_and_options(self):
  source,_=self.fixture();self.assertEqual('PETG',wizard.project_materials(source)[2]['material'])
  self.assertEqual({'width':40.,'rib_size':15.,'brim_width':9.,'purge_volume':12.,'support_slot':3,'frame_slot':1},wizard.tower_options(dict(width='40',rib_size='15',brim_width='9',purge_volume='12',support_slot='3',frame_slot='1')))
 def test_five_mm_ribs_are_supported(self):
  source,_=self.fixture();output=self.folder/'five-mm-ribs.3mf'
  manifest=ipt.Project(source).generate(output,rib_size=5)
  self.assertEqual(5,manifest['rib_size']);self.assertTrue(output.exists())
 def test_custom_outer_brim_width_is_supported(self):
  source,_=self.fixture();output=self.folder/'custom-brim.3mf'
  project=ipt.Project(source);project.settings.update(brim_type='inner_and_outer',brim_width='17',brim_object_gap='.2')
  original=project.config.findall('object')[0];ipt.set_meta(original,'brim_type','no_brim');ipt.set_meta(original,'brim_width','3')
  manifest=project.generate(output,brim_width=12.5)
  with zipfile.ZipFile(output) as archive:
   cfg=ET.fromstring(archive.read(ipt.MODEL_SETTINGS));settings=json.loads(archive.read(ipt.SETTINGS))
  objects=cfg.findall('object');tower=ipt.metadata(objects[0]);original=next(ipt.metadata(o) for o in objects if ipt.metadata(o).get('name')=='reference')
  self.assertEqual(12.5,manifest['brim_width']);self.assertEqual('12.5',tower['brim_width'])
  self.assertEqual(('inner_and_outer','17','.2'),(settings['brim_type'],settings['brim_width'],settings['brim_object_gap']))
  self.assertEqual(('no_brim','3'),(original['brim_type'],original['brim_width']))
 def test_support_selection_must_match_reference(self):
  source,_=self.fixture();p=ipt.Project(source);ref=self.reference([(.2,[0,1,2]),(10,[0])])
  with self.assertRaisesRegex(ipt.TowerError,'does not print supports'):p.generate(self.folder/'bad.3mf',reference_gcode=ref,support_slot=1)
 def test_invalid_options_fail_before_writing(self):
  source,_=self.fixture()
  for kwargs in ({'width':float('nan')},{'rib_size':2},{'purge_volume':0},{'brim_width':-1},{'brim_width':101}):
   with self.assertRaises(ipt.TowerError):ipt.Project(source).generate(self.folder/'bad.3mf',**kwargs)
  self.assertFalse((self.folder/'bad.3mf').exists())

class NewAuditTests(unittest.TestCase):
 setUp=core.AuditTests.setUp
 tearDown=core.AuditTests.tearDown
 code=core.AuditTests.code
 check_code=core.AuditTests.check_code
 def strict_code(self):
  self.manifest.update(format_version=3,purge_volume=5,frame_tool=1,reference_layer_tools={'0.2':[0,1],'0.4':[0,1]},layer_schedule=[{'z':z,'tools':[0,1]} for z in (.2,.4)])
  return self.code().replace('G1 X20 Y6 E3','G1 X20 Y6 E3\nG0 X35 Y15\nG1 X36 Y15 E1')
 def test_strict_audit_passes_and_enforces_saved_volume(self):
  code=self.strict_code();self.assertTrue(self.check_code(code)['passed'])
  self.manifest['purge_volume']=20
  self.assertFalse(self.check_code(code)['passed'])
 def test_reference_grid_change_and_overheight_rejected(self):
  code=self.strict_code()
  self.assertFalse(self.check_code(code.replace('; Z: 0.4','; Z: 0.45'))['passed'])
 def test_abs_cannot_print_asa_section(self):
  code=self.strict_code();self.manifest['lanes'][0]['material']='ABS';self.manifest['lanes'][1]['material']='ASA'
  bad=code.replace('G0 X2 Y2\nG1 X20 Y2','G0 X2 Y6\nG1 X20 Y6',1)
  self.assertFalse(self.check_code(bad)['passed'])
 def test_missing_frame_rejected(self):
  self.strict_code();self.assertTrue(any('missing outer-frame' in e for e in self.check_code(self.code())['errors']))

if __name__=='__main__':unittest.main()
