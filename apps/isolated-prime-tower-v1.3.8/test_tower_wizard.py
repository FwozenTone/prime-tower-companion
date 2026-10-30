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
  first=cfg.find('plate');ipt.set_meta(first,'enable_prime_tower','1');second=ET.SubElement(cfg,'plate');ipt.set_meta(second,'plater_id',2);ipt.set_meta(second,'enable_prime_tower','1')
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
   self.assertEqual('1',ipt.metadata(cc.findall('plate')[0])['enable_prime_tower'])
   self.assertEqual('0',ipt.metadata(cc.findall('plate')[1])['enable_prime_tower'])
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
 def test_file_picker_limits_projects_and_gcode_by_extension(self):
  for name in ('model.3mf','reference.gcode','notes.txt'):(self.folder/name).touch()
  source={e['name'] for e in wizard.browse_files(self.folder,'source')['entries'] if not e['directory']}
  gcode={e['name'] for e in wizard.browse_files(self.folder,'reference')['entries'] if not e['directory']}
  self.assertEqual({'model.3mf'},source);self.assertEqual({'reference.gcode'},gcode)
 def test_manual_gcode_path_requires_gcode_file(self):
  wrong=self.folder/'reference.txt';wrong.touch()
  with self.assertRaisesRegex(ipt.TowerError,'Reference G-code .gcode'):wizard.selected_gcode(wrong)
 def test_failed_slice_does_not_generate_or_open(self):
  w=wizard.Workflow()
  w.source=self.folder/'source.3mf'
  selections={'source':str(w.source),'output':str(self.folder),'plate':'2','width':'42','rib_size':'8','brim_width':'9','purge_volume':'6','support_slot':'3','frame_slot':'2','run_validation':False,'open_after':True}
  with patch.object(w,'setup'),patch.object(w,'slice',side_effect=ipt.TowerError('slice failed')),patch.object(w,'generate') as generate,patch.object(w,'open_in_orca') as opened:
   w.automatic(selections)
  generate.assert_not_called();opened.assert_called_once_with(w.source);self.assertEqual('manual_reference',w.state['stage']);self.assertEqual(selections,w.state['inputs'])
 def test_auto_runs_both_slices_and_audit_before_open(self):
  w=wizard.Workflow();w.source=self.folder/'source.3mf';w.output=self.folder/'tower.3mf';calls=[]
  def verify(path):calls.append(('verify',str(path)));w.state['stage']='passed'
  def sliced(project,tag):calls.append(('slice',tag));return self.folder/(tag+'.gcode')
  with patch.object(w,'setup'),patch.object(w,'slice',side_effect=sliced),patch.object(w,'generate',side_effect=lambda p:calls.append(('generate',str(p)))),patch.object(w,'verify',side_effect=verify),patch.object(w,'open_project',side_effect=lambda:calls.append(('open',''))):
   w.automatic({'run_validation':True,'open_after':True})
  self.assertEqual(['slice','generate','slice','verify','open'],[c[0] for c in calls])
 def test_auto_skips_post_tower_slice_and_audit_by_default(self):
  w=wizard.Workflow();w.source=self.folder/'source.3mf';w.output=self.folder/'tower.3mf';calls=[]
  def sliced(project,tag):calls.append(('slice',tag));return self.folder/(tag+'.gcode')
  with patch.object(w,'setup'),patch.object(w,'slice',side_effect=sliced),patch.object(w,'generate',side_effect=lambda p:calls.append(('generate',str(p)))),patch.object(w,'verify') as verify,patch.object(w,'open_project',side_effect=lambda:calls.append(('open',''))):
   w.automatic({'open_after':True})
  self.assertEqual(['slice','generate','open'],[c[0] for c in calls]);verify.assert_not_called()
  self.assertEqual('unverified',w.state['stage']);self.assertTrue(w.state['result']['validation_skipped']);self.assertEqual('Tower generated and added to project plate. Plate should already be open in Orca and is ready for slicing and printing.',w.state['message']);self.assertTrue(w.state['open_after'])
 def test_completed_project_without_auto_open_requests_button(self):
  w=wizard.Workflow();w.source=self.folder/'source.3mf';w.output=self.folder/'tower.3mf'
  with patch.object(w,'setup'),patch.object(w,'slice',return_value=self.folder/'reference.gcode'),patch.object(w,'generate',side_effect=lambda p:w.update('tower_ready',w.completion_message(),project=str(w.output),open_after=w.open_after)),patch.object(w,'open_project') as opened:
   w.open_after=False;w.automatic({'open_after':False})
  opened.assert_not_called();self.assertEqual('Tower generated and added to project plate.',w.state['message']);self.assertFalse(w.state['open_after'])
 def test_manual_recovery_opens_tower_using_original_selection(self):
  w=wizard.Workflow();w.source=(self.folder/'source.3mf').resolve();w.output=self.folder/'tower.3mf';w.plate=1;w.open_after=True
  reference=self.folder/'reference.gcode';reference.touch();data={'source':str(w.source),'plate':'1','reference':str(reference),'rib_size':'17'}
  with patch.object(w,'generate'),patch.object(w,'open_project') as opened:
   w.manual_generate(data)
  opened.assert_called_once_with();self.assertEqual(17,w.options['rib_size'])
 def test_failed_audit_does_not_auto_open(self):
  w=wizard.Workflow();w.source=self.folder/'source.3mf';w.output=self.folder/'tower.3mf'
  with patch.object(w,'setup'),patch.object(w,'slice',return_value=self.folder/'a.gcode'),patch.object(w,'generate'),patch.object(w,'verify',side_effect=lambda p:w.update('failed','failed')),patch.object(w,'open_project') as opened:
   w.automatic({'run_validation':True});opened.assert_not_called()
 def test_new_source_reset_returns_to_initial_flow(self):
  w=wizard.Workflow();w.source=self.folder/'old.3mf';w.output=self.folder/'old-tower.3mf';w.state.update(stage='unverified',project=str(w.output),folder=str(self.folder),events=['done'],inputs={'source':str(w.source)})
  w.reset()
  self.assertEqual('ready',w.state['stage']);self.assertEqual([],w.state['events']);self.assertEqual({},w.state['inputs']);self.assertIsNone(w.source);self.assertIsNone(w.output)

 def test_project_summary_defaults_output_to_source_folder(self):
  source=self.folder/'source.3mf';source.touch()
  with patch.object(wizard,'plates',return_value=[{'number':1,'name':'Plate 1'}]),patch.object(wizard,'project_materials',return_value=[]):
   self.assertEqual(str(self.folder.resolve()),wizard.project_summary(source)['folder'])

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
 def test_previous_source_folder_is_saved_without_losing_orca(self):
  source=self.root/'projects'/'model.3mf';source.parent.mkdir();source.touch()
  with patch.object(wizard,'settings_file',return_value=self.root/'prefs/settings.json'):
   path=wizard.settings_file();path.parent.mkdir(parents=True);path.write_text('{"orca":"saved"}')
   wizard.save_source_folder(source);saved=wizard.load_settings()
  self.assertEqual(str(source.parent.resolve()),saved['source_folder']);self.assertEqual('saved',saved['orca'])
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
 def test_open_reuses_or_launches_configured_mac_app(self):
  p=self.executable('Other Orca.app/Contents/MacOS/OrcaSlicer');project=self.root/'tower.3mf';project.touch()
  w=wizard.Workflow();w.orca=str(p);w.output=project
  with patch.object(wizard.sys,'platform','darwin'),patch.object(wizard.subprocess,'run') as launch:
   w.open_project();launch.assert_called_once_with(['/usr/bin/open','-a',str((self.root/'Other Orca.app').resolve()),str(project)],check=True)
