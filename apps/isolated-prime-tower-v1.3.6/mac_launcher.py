"""Build a native, stay-open macOS applet for the browser-based wizard."""
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import tempfile


def apple_string(value):
    return '"'+value.replace('\\','\\\\').replace('"','\\"')+'"'


def launcher_source(target, python):
    target=Path(target)
    log=target/'wizard.log'
    start=(f'cd {shlex.quote(str(target))} || exit 1; '
           f'{shlex.quote(str(python))} {shlex.quote(str(target/"tower_wizard.py"))}'
           f' > {shlex.quote(str(log))} 2>&1 < /dev/null & echo $!')
    # Read only the URL emitted by this launch, never a shell command from the log.
    code=('import pathlib,re,subprocess; '
          f'text=pathlib.Path({str(log)!r}).read_text(); '
          'match=re.search(r"^Prime Tower Wizard: (http://127\\.0\\.0\\.1:[0-9]+/)$",text,re.M); '
          'subprocess.run(["/usr/bin/open",match.group(1)],check=True,timeout=15) if match else None')
    reopen=f'{shlex.quote(str(python))} -c {shlex.quote(code)}'
    # Raw Apple event spelling of Standard Additions “do shell script” avoids
    # relying on dictionary discovery in the installer environment.
    return f'''property serverPID : ""
on run
    set serverPID to «event sysoexec» {apple_string(start)}
end run
on reopen
    try
        «event sysoexec» {apple_string(reopen)}
    end try
end reopen
on idle
    return 30
end idle
on quit
    if serverPID is not "" then
        try
            «event sysoexec» "/bin/kill -TERM " & quoted form of serverPID
        end try
        set serverPID to ""
    end if
    continue quit
end quit
'''


def build_app(target, python, runtime_target=None):
    target=Path(target)
    app=target/'Prime Tower Wizard.app'
    with tempfile.TemporaryDirectory(dir=target) as temp:
        temp=Path(temp);source=temp/'launcher.applescript'
        source.write_text(launcher_source(runtime_target or target,python))
        built=temp/app.name
        subprocess.run(['/usr/bin/osacompile','-s','-o',str(built),str(source)],check=True)
        contents=built/'Contents';info=contents/'Info.plist'
        data=plistlib.loads(info.read_bytes())
        data.update(CFBundleName='Prime Tower Wizard',CFBundleDisplayName='Prime Tower Wizard',
                    CFBundleIdentifier='local.tonyo.primetowerwizard',CFBundleVersion='1.3.6',
                    CFBundleShortVersionString='1.3.6',CFBundleIconFile='TowerIcon.icns',
                    NSHighResolutionCapable=True)
        info.write_bytes(plistlib.dumps(data))
        shutil.copy2(target/'TowerIcon.icns',contents/'Resources/TowerIcon.icns')
        subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(built)],check=True)
        previous=temp/'previous.app'
        if app.exists():shutil.move(str(app),str(previous))
        try:shutil.move(str(built),str(app))
        except Exception:
            if previous.exists() and not app.exists():shutil.move(str(previous),str(app))
            raise
    return app
