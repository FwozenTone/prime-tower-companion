"""Install the independent export script; does not modify the tower wizard or Orca presets."""
from pathlib import Path
import shutil, time, sys
source=Path(__file__).resolve().parent
target=Path.home()/'Documents/voron/projects/Tower_Speed_Ramp_v1.0'
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
