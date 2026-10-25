import json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET
import isolated_prime_tower as ipt
import tower_wizard as wizard
import test_isolated_prime_tower as core_tests

class PlateTests(unittest.TestCase):
 setUp=core_tests.ProjectTests.setUp
 tearDown=core_tests.ProjectTests.tearDown
 fixture=core_tests.ProjectTests.fixture
 def test_second_plate_keeps_first_plate_and_uses_local_coordinates(self):
  source,files=self.fixture(2)
  root=ET.fromstring(files[ipt.MODEL]);cfg=ET.fromstring(files[ipt.MODEL_SETTINGS])
  first=cfg.find('plate');second=ET.SubElement(cfg,'plate');ipt.set_meta(second,'plater_id',2)
  inst=first.findall('model_instance')[1];first.remove(inst);second.append(inst)
  build=root.find(ipt.Q('build'));before=ET.tostring(build[0]);v=list(map(float,build[1].get('transform').split()));v[9]+=420
  build[1].set('transform',' '.join(map(str,v)));files[ipt.MODEL]=ipt.xml_bytes(root);files[ipt.MODEL_SETTINGS]=ipt.xml_bytes(cfg)
  with zipfile.ZipFile(source,'w') as z:
   for n,d in files.items():z.writestr(n,d)
  with self.assertRaisesRegex(ipt.TowerError,'plate number'):ipt.Project(source)
  project=ipt.Project(source,2);self.assertAlmostEqual(180,project.reference_bounds[0]);self.assertEqual([420,0],project.plate_offset)
  out=self.folder/'plate2.3mf';m=project.generate(out,filaments=[2,3])
  self.assertEqual(2,m['plate_number']);self.assertEqual(m,ipt.load_manifest(out))
  with zipfile.ZipFile(out) as z:
   rr=ET.fromstring(z.read(ipt.MODEL));cc=ET.fromstring(z.read(ipt.MODEL_SETTINGS))
   self.assertEqual(before,ET.tostring(rr.find(ipt.Q('build'))[1]))
   self.assertEqual(1,len(cc.findall('plate')[0].findall('model_instance')))
   self.assertEqual(2,len(cc.findall('plate')[1].findall('model_instance')))
  self.assertEqual([1,2],[p['number'] for p in wizard.plates(source)])

class WorkflowTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_extracts_only_selected_plate_without_path_traversal(self):
  with zipfile.ZipFile(self.folder/'sliced.3mf','w') as z:
   z.writestr('Metadata/plate_1.gcode','one');z.writestr('Metadata/plate_2.gcode','two')
  p=wizard.extract_gcode(self.folder,2);self.assertEqual('two',p.read_text())
 def test_ambiguous_export_rejected(self):
  with zipfile.ZipFile(self.folder/'sliced.3mf','w') as z:
   z.writestr('a.gcode','one');z.writestr('b.gcode','two')
  with self.assertRaises(ipt.TowerError):wizard.extract_gcode(self.folder,2)
 def test_failed_slice_does_not_generate_or_open(self):
  w=wizard.Workflow()
  with patch.object(w,'setup'),patch.object(w,'slice',side_effect=ipt.TowerError('slice failed')),patch.object(w,'generate') as generate,patch.object(w,'open_project') as opened:
   with self.assertRaisesRegex(ipt.TowerError,'slice failed'):w.automatic({})
   generate.assert_not_called();opened.assert_not_called()
 def test_auto_runs_both_slices_and_audit_before_open(self):
  w=wizard.Workflow();w.source=self.folder/'source.3mf';w.output=self.folder/'tower.3mf';calls=[]
  def verify(path):calls.append(('verify',str(path)));w.state['stage']='passed'
  def sliced(project,tag):calls.append(('slice',tag));return self.folder/(tag+'.gcode')
  with patch.object(w,'setup'),patch.object(w,'slice',side_effect=sliced),patch.object(w,'generate',side_effect=lambda p:calls.append(('generate',str(p)))),patch.object(w,'verify',side_effect=verify),patch.object(w,'open_project',side_effect=lambda:calls.append(('open',''))):
   w.automatic({'open_after':True})
  self.assertEqual(['slice','generate','slice','verify','open'],[c[0] for c in calls])
 def test_failed_audit_does_not_auto_open(self):
  w=wizard.Workflow();w.source=self.folder/'source.3mf';w.output=self.folder/'tower.3mf'
  with patch.object(w,'setup'),patch.object(w,'slice',return_value=self.folder/'a.gcode'),patch.object(w,'generate'),patch.object(w,'verify',side_effect=lambda p:w.update('failed','failed')),patch.object(w,'open_project') as opened:
   w.automatic({});opened.assert_not_called()

if __name__=='__main__':unittest.main()

class OrcaLocationTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def executable(self,name):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('test');p.chmod(0o755);return p
 def test_custom_mac_bundle_and_saved_location(self):
  import plistlib
  p=self.executable('Custom Orca.app/Contents/MacOS/custom-orca')
  (p.parent.parent/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'custom-orca'}))
  with patch.object(wizard,'settings_file',return_value=self.root/'prefs/settings.json'):
   value=wizard.save_orca(str(p.parent.parent.parent))
   self.assertEqual(str(p.resolve()),value);self.assertEqual(value,wizard.load_settings()['orca'])
 def test_windows_executable_with_spaces(self):
  p=self.executable('Program Files/Orca Slicer/orca-slicer.exe')
  with patch.object(wizard.sys,'platform','win32'):self.assertEqual(p.resolve(),wizard.resolve_orca(str(p)))
 def test_linux_appimage_requires_execute_permission(self):
  p=self.executable('OrcaSlicer.AppImage')
  with patch.object(wizard.sys,'platform','linux'):
   self.assertEqual(p.resolve(),wizard.resolve_orca(str(p)));p.chmod(0o644)
   with self.assertRaisesRegex(ipt.TowerError,'not executable'):wizard.resolve_orca(str(p))
 def test_open_uses_configured_executable_on_linux(self):
  p=self.executable('Other Orca.AppImage');project=self.root/'tower.3mf';project.touch()
  w=wizard.Workflow();w.orca=str(p);w.output=project
  with patch.object(wizard.sys,'platform','linux'),patch.object(wizard.subprocess,'Popen') as launch:
   w.open_project();self.assertEqual([str(p.resolve()),str(project)],launch.call_args.args[0])
