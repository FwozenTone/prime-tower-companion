import contextlib, io, json, os, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import orca_tower_ramp as hook
import ramp_engine as ramp
import tower_project as tower

class ExportTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'Orca temporary file.gcode.pp';self.project=self.root/'tower project.3mf'
  self.manifest={'format_version':3,'lanes':[{'tool':0,'bounds':[1,1,31,5]},{'tool':1,'bounds':[1,5,31,9]}],
   'frame_tool':1,'tower_bounds':[0,0,40,20],'tower_height':.2,'purge_volume':5,'layer_schedule':[{'z':.2,'tools':[0,1]}]}
  with zipfile.ZipFile(self.project,'w') as z:z.writestr(tower.MANIFEST,json.dumps(self.manifest))
  self.code=('G90\nM83\n; Z: .2\nT0\nG0 X2 Y2 F6000\nG1 X20 Y2 E3 F1200\nG0 X100 Y100\nG1 X110 E1 F1800\n'
   'T1\nG0 X2 Y6 F6000\nG1 X20 Y6 E3 F1200\nG0 X35 Y15 F6000\nG1 X36 Y15 E3 F1200\nG0 X100 Y100\nG1 X110 E1 F1800\n')
  self.source.write_text(self.code)
 def tearDown(self):self.temp.cleanup()
 def run_process(self,**kw):return hook.process(self.source,self.project,**kw)
 def test_in_place_success_preserves_name_and_model_commands(self):
  original_project=self.project.read_bytes();result=self.run_process()
  self.assertTrue(result['audit']['passed']);self.assertEqual(2,result['speed_ramp']['completed_ramps'])
  text=self.source.read_text();self.assertTrue(text.startswith(ramp.MARKER))
  self.assertIn('G1 X110 E1 F1800',text);self.assertIn('G1 F1200 ; IPT restore sliced feed rate\nG0 X100 Y100',text)
  self.assertEqual(original_project,self.project.read_bytes());self.assertEqual([],list(self.root.glob('.orca-tower-ramp-*')))
 def test_repeated_invocation_fails_without_second_ramp(self):
  self.run_process();before=self.source.read_bytes()
  with self.assertRaisesRegex(ValueError,'already contains'):self.run_process()
  self.assertEqual(before,self.source.read_bytes())
 def test_pre_audit_failure_keeps_input_unchanged(self):
  self.source.write_text(self.code.replace('E3 F1200','E.01 F1200'));before=self.source.read_bytes()
  with self.assertRaisesRegex(ValueError,'audit failed'):self.run_process()
  self.assertEqual(before,self.source.read_bytes())
 def test_post_audit_failure_keeps_input_unchanged(self):
  before=self.source.read_bytes()
  with patch.object(tower,'audit',side_effect=[{'passed':True},{'passed':False,'errors':['injected final failure']}]):
   with self.assertRaisesRegex(ValueError,'Ramped export audit failed'):self.run_process()
  self.assertEqual(before,self.source.read_bytes());self.assertEqual([],list(self.root.glob('.orca-tower-ramp-*')))
 def test_rewrite_failure_keeps_input_and_removes_temp(self):
  self.source.write_text('G20\n'+self.code);before=self.source.read_bytes()
  with self.assertRaisesRegex(ValueError,'Unsupported'):self.run_process()
  self.assertEqual(before,self.source.read_bytes());self.assertEqual([],list(self.root.glob('.orca-tower-ramp-*')))
 def test_retraction_repayment_is_not_priming(self):
  self.source.write_text(self.code.replace('G1 X20 Y2 E3 F1200','G1 E-2 F1800\nG1 X20 Y2 E3 F1200'))
  before=self.source.read_bytes()
  with self.assertRaises(ValueError):self.run_process()
  self.assertEqual(before,self.source.read_bytes())
 def test_volume_and_endpoints_preserved(self):
  self.run_process();text=self.source.read_text()
  def extrusion(code):return sum(float(dict(ramp.WORDS.findall(l)).get('E',0)) for l in code.splitlines() if l.startswith('G1'))
  self.assertAlmostEqual(extrusion(self.code),extrusion(text),places=8);self.assertIn('X20 Y2',text)
 def test_absolute_e_reset(self):
  self.source.write_text(self.code.replace('M83','M82').replace('E3 F1200','E3 F1200\nG92 E0').replace('E1 F1800','E1 F1800\nG92 E0'))
  result=self.run_process();self.assertTrue(result['audit']['passed']);self.assertEqual(5,self.source.read_text().count('G92 E0'))
 def test_speed_increases_and_resets_each_pickup(self):
  self.run_process();text=self.source.read_text()
  for part in (text.split('T0\n')[1].split('T1\n')[0],text.split('T1\n')[1]):
   lines=[l for l in part.split('G1 F1200 ; IPT restore')[0].splitlines() if l.startswith('G1') and 'E' in l]
   speeds=[float(dict(ramp.WORDS.findall(l))['F']) for l in lines]
   self.assertEqual(300,speeds[0]);self.assertEqual(1200,speeds[-1]);self.assertEqual(sorted(speeds),speeds)
 def test_disabled_slowing_keeps_commands(self):
  self.run_process(start_percent=100);self.assertEqual(self.code,self.source.read_text().split('\n',1)[1])
 def test_large_volume_rejected(self):
  before=self.source.read_bytes()
  with self.assertRaises(ValueError):self.run_process(volume=100)
  self.assertEqual(before,self.source.read_bytes())
 def test_command_line_matches_orca_appended_argument(self):
  result=subprocess.run([sys.executable,str(Path(hook.__file__)), '--project',str(self.project),'--start-percent','25','--volume','5',str(self.source)],capture_output=True,text=True)
  self.assertEqual(0,result.returncode,result.stderr)
  again=subprocess.run([sys.executable,str(Path(hook.__file__)),'--project',str(self.project),str(self.source)],capture_output=True,text=True)
  self.assertEqual(2,again.returncode);self.assertIn('already contains',again.stderr)
 def test_saved_assembly_movement_is_read_for_export(self):
  import xml.etree.ElementTree as ET
  m=dict(self.manifest,assembly_name='IPT_ASSEMBLY',origin=[0,0])
  m['lanes']=[dict(lane,filament_slot=lane['tool']+1,material=['PLA','PETG'][lane['tool']]) for lane in m['lanes']]
  root=ET.Element(tower.Q('model'));resources=ET.SubElement(root,tower.Q('resources'))
  obj=ET.SubElement(resources,tower.Q('object'),{'id':'1'})
  tower.mesh_xml(obj,tower.prism([(0,0),(40,0),(40,20),(0,20)],0,.2))
  build=ET.SubElement(root,tower.Q('build'));ET.SubElement(build,tower.Q('item'),{'objectid':'1','transform':'1 0 0 0 1 0 0 0 1 5 6 0'})
  cfg=ET.Element('config');co=ET.SubElement(cfg,'object',{'id':'1'});tower.set_meta(co,'name','IPT_ASSEMBLY')
  with zipfile.ZipFile(self.project,'w') as z:
   z.writestr(tower.MANIFEST,json.dumps(m));z.writestr(tower.MODEL,tower.xml_bytes(root));z.writestr(tower.MODEL_SETTINGS,tower.xml_bytes(cfg));z.writestr(tower.SETTINGS,json.dumps({'filament_type':['PLA','PETG']}))
  import re
  shifted=re.sub(r'([XY])([-+]?\d+(?:\.\d*)?)',lambda match:match[1]+str(float(match[2])+(5 if match[1]=='X' else 6)),self.code)
  self.source.write_text(shifted);self.assertTrue(self.run_process()['audit']['passed'])
 def test_missing_project_failure_exit_keeps_input(self):
  before=self.source.read_bytes()
  with contextlib.redirect_stderr(io.StringIO()):rc=hook.main(['--project',str(self.root/'missing.3mf'),str(self.source)])
  self.assertEqual(2,rc);self.assertEqual(before,self.source.read_bytes())

if __name__=='__main__':unittest.main()
