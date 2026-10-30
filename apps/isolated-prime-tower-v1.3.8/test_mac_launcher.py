import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from mac_launcher import build_app

class LauncherTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='darwin','macOS compiler required')
    def test_native_bundle_and_reinstall_without_backup(self):
        with tempfile.TemporaryDirectory(prefix="wizard's app ") as folder:
            target=Path(folder)
            shutil.copy2(Path(__file__).with_name('TowerIcon.icns'),target/'TowerIcon.icns')
            app=build_app(target,sys.executable)
            info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
            self.assertEqual(info['CFBundleExecutable'],'applet')
            self.assertEqual(info['CFBundleVersion'],'1.3.8')
            self.assertEqual(info['CFBundleIconFile'],'TowerIcon.icns')
            self.assertNotIn('CFBundleIconName',info)
            subprocess.run(['/usr/bin/codesign','--verify','--deep',str(app)],check=True)
            build_app(target,sys.executable)
            self.assertFalse((target/'launcher-backups').exists())
            self.assertTrue(app.exists())

    def test_progress_and_recovery_are_initially_hidden_without_generation_log(self):
        class Parser(HTMLParser):
            def __init__(self):super().__init__();self.ids={};self.open_details=[];self.sections=0
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='section':self.sections+=1
                if attrs.get('id'):self.ids[attrs['id']]=attrs
                if tag=='details' and 'open' in attrs:self.open_details.append(attrs.get('id'))
        html=Path(__file__).with_name('wizard.html').read_text();parser=Parser();parser.feed(html)
        self.assertEqual(1,parser.sections)
        self.assertIn('hidden',parser.ids['progress']);self.assertIn('hidden',parser.ids['manualRecovery'])
        self.assertNotIn('liveLog',parser.ids);self.assertIn('auditDetails',parser.open_details)
        self.assertNotIn('final',parser.ids);self.assertNotIn('steps',parser.ids)
        self.assertIn('hidden',parser.ids['open']);self.assertIn('hidden',parser.ids['anotherProject'])
        self.assertIn("s.open_after===true?' If necessary click ':' '",html)
        self.assertIn('function anotherProject()',html);self.assertIn('resetFlowView(false)',html)
        self.assertIn("if(folder!==restartSourceFolder){el('output').value='';outputCustomized=false}",html)
