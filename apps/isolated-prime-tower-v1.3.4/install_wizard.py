"""User-run macOS installation and Dock shortcut setup."""
from pathlib import Path
import os,plistlib,shutil,subprocess,sys,time,tempfile
source=Path(__file__).resolve().parent
# Prompt before creating or changing any installed files.
default_parent=Path.home()/'Applications'
print('Choose the parent folder for installation. The application will use the same installation folder for future upgrades.')
try:
    answer=input(f'Install folder [{default_parent}] (q to cancel): ').strip()
except (EOFError, KeyboardInterrupt):
    print('\nInstallation cancelled.');sys.exit(0)
if answer.lower() in ('q','quit','cancel'):
    print('Installation cancelled.');sys.exit(0)
if len(answer)>1 and answer[0]==answer[-1] and answer[0] in ('"', "'"):
    answer=answer[1:-1]
parent=Path(answer).expanduser().resolve() if answer else default_parent
if parent.exists() and not parent.is_dir():
    print('Installation folder must be a directory.');sys.exit(1)
target=parent/'Tower_Companion'
print('Installing into:',target)
# Detect only wizard installations in the selected parent, not source archives.
previous=[p for p in [target,*sorted(parent.glob('Tower_Companion_v*'))]
          if (p/'Prime Tower Wizard.app').is_dir()]
if target.exists() and target not in previous:
    print('The destination exists but is not a recognized wizard installation. Choose another folder.');sys.exit(1)
if previous:
    print('Existing installation(s):\n'+'\n'.join(str(p) for p in previous))
    print('Quit the running wizard before replacing it. Previous installations will be moved to a backup folder.')
    try:replace=input('Replace the previous installation(s)? [y/N]: ').strip().lower()
    except (EOFError,KeyboardInterrupt):replace='n'
    if replace not in ('y','yes'):
        print('Installation cancelled. Existing applications were not changed.');sys.exit(0)
parent.mkdir(parents=True,exist_ok=True)
from mac_launcher import build_app
# Build the complete replacement before moving any existing application.
with tempfile.TemporaryDirectory(prefix='.wizard-install-',dir=parent) as temporary:
    staged=Path(temporary)/'Tower_Companion';staged.mkdir()
    for item in source.iterdir():
        if item.is_file():shutil.copy2(item,staged/item.name)
    build_app(staged,Path(sys.executable).resolve(),runtime_target=target)
    moved=[]
    try:
        if previous:
            backup_root=parent/'Prime Tower Wizard Backups';backup_root.mkdir(exist_ok=True)
            backup=Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d_%H%M%S_'),dir=backup_root))
            for old in previous:
                saved=backup/old.name;shutil.move(str(old),str(saved));moved.append((old,saved))
        shutil.move(str(staged),str(target))
    except Exception:
        for old,saved in reversed(moved):
            if not old.exists():shutil.move(str(saved),str(old))
        raise
app=target/'Prime Tower Wizard.app'
try:
    exported=subprocess.run(['defaults','export','com.apple.dock','-'],check=True,capture_output=True).stdout
    dock=plistlib.loads(exported);url=app.as_uri()+'/'
    # Repoint old wizard shortcuts after an approved replacement.
    old_urls={(p/'Prime Tower Wizard.app').as_uri()+'/' for p in previous}
    changed=False
    for entry in dock.get('persistent-apps',[]):
        data=entry.get('tile-data',{});file_data=data.get('file-data',{})
        if file_data.get('_CFURLString') in old_urls:
            file_data['_CFURLString']=url;changed=True
    if changed:
        (target/'dock-before-install.plist').write_bytes(exported)
        updated=target/'dock-updated.plist';updated.write_bytes(plistlib.dumps(dock))
        subprocess.run(['defaults','import','com.apple.dock',str(updated)],check=True)
        updated.unlink()
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
