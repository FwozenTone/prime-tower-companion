"""User-run macOS installation and Dock shortcut setup."""
from pathlib import Path
import os,plistlib,shutil,subprocess,sys,time
source=Path(__file__).resolve().parent
target=Path.home()/'Documents/voron/projects/Tower_Companion_v1.3'
target.mkdir(parents=True,exist_ok=True)
backup=target/('backup_'+time.strftime('%Y%m%d_%H%M%S'))
for item in source.iterdir():
    if not item.is_file():continue
    dest=target/item.name
    if item.resolve()==dest.resolve():continue
    if dest.exists():
        backup.mkdir(exist_ok=True);shutil.copy2(dest,backup/item.name)
    shutil.copy2(item,dest)
# Retire files from the integrated ramp version without deleting the backup.
for name in ('tower_ramp.py','test_tower_ramp.py'):
    old=target/name
    if old.exists():
        backup.mkdir(exist_ok=True);shutil.move(str(old),str(backup/name))
app=target/'Prime Tower Wizard.app';contents=app/'Contents';macos=contents/'MacOS';macos.mkdir(parents=True,exist_ok=True)
resources=contents/'Resources';resources.mkdir(exist_ok=True)
shutil.copy2(target/'TowerIcon.icns',resources/'TowerIcon.icns')
plist={'CFBundleName':'Prime Tower Wizard','CFBundleDisplayName':'Prime Tower Wizard','CFBundleIdentifier':'local.tonyo.primetowerwizard','CFBundleExecutable':'PrimeTowerWizard','CFBundlePackageType':'APPL','CFBundleVersion':'1.3','CFBundleShortVersionString':'1.3','CFBundleIconFile':'TowerIcon.icns','NSHighResolutionCapable':True}
(contents/'Info.plist').write_bytes(plistlib.dumps(plist))
python=Path(sys.executable).resolve()
launcher=macos/'PrimeTowerWizard'
# Quote paths as shell literals, including apostrophes.
import shlex
launcher.write_text('#!/bin/zsh\ncd -- '+shlex.quote(str(target))+'\nexec '+shlex.quote(str(python))+' '+shlex.quote(str(target/'tower_wizard.py'))+' >> '+shlex.quote(str(target/'wizard.log'))+' 2>&1\n')
launcher.chmod(0o755)
try:
    exported=subprocess.run(['defaults','export','com.apple.dock','-'],check=True,capture_output=True).stdout
    dock=plistlib.loads(exported);url=app.as_uri()+'/'
    found=any(x.get('tile-data',{}).get('file-data',{}).get('_CFURLString')==url for x in dock.get('persistent-apps',[]))
    if not found:
        (target/'dock-before-install.plist').write_bytes(exported)
        tile={'tile-type':'file-tile','tile-data':{'file-data':{'_CFURLString':url,'_CFURLStringType':15},'file-label':'Prime Tower Wizard','bundle-identifier':'local.tonyo.primetowerwizard','file-type':41}}
        subprocess.run(['defaults','write','com.apple.dock','persistent-apps','-array-add',plistlib.dumps(tile).decode()],check=True)
    # Refresh an existing shortcut too, so upgrades pick up the bundled icon.
    os.utime(app,None)
    subprocess.run(['killall','Dock'],check=False)
    print('Installed and added to Dock:',app)
except Exception as error:
    print('Installed. Dock shortcut could not be added:',error)
    print('Drag this application to your Dock:',app)
subprocess.run(['open',str(app)],check=True)
