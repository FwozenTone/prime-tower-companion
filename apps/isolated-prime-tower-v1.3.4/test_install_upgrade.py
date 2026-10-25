import contextlib
import io
import plistlib
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import mac_launcher

class UpgradeTests(unittest.TestCase):
    def run_installer(self,parent,answer=None,fail=False):
        answers=[str(parent)]+([answer] if answer is not None else [])
        def build(staged,python,runtime_target=None):
            self.assertEqual(runtime_target,(parent/'Tower_Companion').resolve())
            if fail:raise RuntimeError('build failed')
            app=staged/'Prime Tower Wizard.app';app.mkdir();return app
        def command(args,**kwargs):
            return subprocess.CompletedProcess(args,0,plistlib.dumps({'persistent-apps':[]}),b'')
        with patch('builtins.input',side_effect=answers),patch.object(mac_launcher,'build_app',side_effect=build),patch('subprocess.run',side_effect=command),contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(Path(__file__).with_name('install_wizard.py')),run_name='__main__')

    def previous(self,parent):
        old=parent/'Tower_Companion_v1.3.3';(old/'Prime Tower Wizard.app').mkdir(parents=True)
        (old/'sentinel').write_text('original');return old

    def test_fresh_install_uses_stable_folder(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.run_installer(p)
            self.assertTrue((p/'Tower_Companion/Prime Tower Wizard.app').is_dir())

    def test_declining_preserves_previous(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);old=self.previous(p)
            with self.assertRaises(SystemExit):self.run_installer(p,'n')
            self.assertEqual((old/'sentinel').read_text(),'original')
            self.assertFalse((p/'Tower_Companion').exists())

    def test_approved_upgrade_backs_up_complete_previous(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);old=self.previous(p);self.run_installer(p,'yes')
            self.assertFalse(old.exists())
            self.assertTrue((p/'Tower_Companion/Prime Tower Wizard.app').exists())
            files=list((p/'Prime Tower Wizard Backups').glob('*/Tower_Companion_v1.3.3/sentinel'))
            self.assertEqual(len(files),1);self.assertEqual(files[0].read_text(),'original')

    def test_build_failure_leaves_previous_intact(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);old=self.previous(p)
            with self.assertRaisesRegex(RuntimeError,'build failed'):self.run_installer(p,'yes',fail=True)
            self.assertEqual((old/'sentinel').read_text(),'original')
