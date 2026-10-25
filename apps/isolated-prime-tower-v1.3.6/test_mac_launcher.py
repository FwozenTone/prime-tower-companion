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
            self.assertEqual(info['CFBundleVersion'],'1.3.6')
            subprocess.run(['/usr/bin/codesign','--verify','--deep',str(app)],check=True)
            build_app(target,sys.executable)
            self.assertFalse((target/'launcher-backups').exists())
            self.assertTrue(app.exists())

    def test_generation_log_is_in_collapsed_details(self):
        class Parser(HTMLParser):
            def __init__(self):super().__init__();self.stack=[];self.log_parent=None
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='details':self.stack.append(attrs)
                if attrs.get('id')=='liveLog':self.log_parent=list(self.stack)
            def handle_endtag(self,tag):
                if tag=='details':self.stack.pop()
        parser=Parser();parser.feed(Path(__file__).with_name('wizard.html').read_text())
        self.assertEqual(parser.log_parent,[{'id':'generationLog'}])
