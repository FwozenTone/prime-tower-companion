"""Install the independent export script; does not modify the tower wizard or Orca presets."""
from pathlib import Path
import shutil, time, sys
source=Path(__file__).resolve().parent
# Prompt before creating or changing any installed files.
default_parent=Path.home()/'Applications'
print('Choose the parent folder for installation. A versioned application folder will be created inside it.')
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
target=parent/'Tower_Speed_Ramp_v1.0.1'
print('Installing into:',target)
target.mkdir(parents=True,exist_ok=True)
backup=target/('backup_'+time.strftime('%Y%m%d_%H%M%S'))
for name in ('orca_tower_ramp.py','ramp_engine.py','tower_project.py','README.md'):
    src=source/name;dst=target/name
    if src.resolve()==dst.resolve():continue
    if dst.exists():
        backup.mkdir(exist_ok=True);shutil.copy2(dst,backup/name)
    shutil.copy2(src,dst)
print('Installed independent post-processor:',target)
print('In Orca > Process > Others > Post Processing Scripts, add this as the LAST script.')
print('Replace the project path with the saved tower project for this plate:')
print(f'"{Path(sys.executable).resolve()}" "{target / "orca_tower_ramp.py"}" --project "/FULL/PATH/TO/tower.3mf" --start-percent 25 --volume 5')
print('Save the tower project after moving the tower. Orca appends the G-code path automatically.')
