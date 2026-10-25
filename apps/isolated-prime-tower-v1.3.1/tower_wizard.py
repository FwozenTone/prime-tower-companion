#!/usr/bin/env python3
"""Local, browser-based workflow; no Tk or external Python packages required."""
import json, os, plistlib, secrets, shutil, subprocess, sys, threading, time, webbrowser, zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import isolated_prime_tower as tower

def settings_file():
    if sys.platform=='win32':root=Path(os.environ.get('APPDATA',Path.home()/'AppData/Roaming'))
    elif sys.platform=='darwin':root=Path.home()/'Library/Application Support'
    else:root=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))
    return root/'PrimeTowerWizard'/'settings.json'

def default_orca():
    names=('orca-slicer','OrcaSlicer','orca-slicer.exe')
    for name in names:
        found=shutil.which(name)
        if found:return found
    if sys.platform=='darwin':return '/Applications/OrcaSlicer.app'
    if sys.platform=='win32':return str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'OrcaSlicer'/'orca-slicer.exe')
    return ''

def load_settings():
    try:return json.loads(settings_file().read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def resolve_orca(value):
    value=os.path.expandvars(str(value).strip().strip('"'))
    if not value:raise tower.TowerError('Choose the installed Orca application or executable.')
    executable=Path(value).expanduser()
    if executable.suffix.lower()=='.app':
        info=executable/'Contents/Info.plist'
        name='OrcaSlicer'
        if info.is_file():
            with info.open('rb') as stream:name=plistlib.load(stream).get('CFBundleExecutable',name)
        executable=executable/'Contents/MacOS'/name
    elif not executable.is_file():
        found=shutil.which(value)
        if found:executable=Path(found)
    if not executable.is_file():raise tower.TowerError('Orca executable not found: '+str(executable))
    if sys.platform!='win32' and not os.access(executable,os.X_OK):raise tower.TowerError('Orca is not executable. On Linux, enable Execute permission on the binary or AppImage.')
    return executable.resolve()

def save_orca(value):
    executable=resolve_orca(value)
    path=settings_file();path.parent.mkdir(parents=True,exist_ok=True)
    data=load_settings();data['orca']=str(executable)
    path.write_text(json.dumps(data,indent=2),encoding='utf-8')
    return str(executable)

DEFAULT_ORCA=default_orca()

def plates(path):
    with zipfile.ZipFile(path) as z:root=ET.fromstring(z.read(tower.MODEL_SETTINGS))
    return [{'number':i+1,'name':tower.metadata(p).get('plater_name','') or f'Plate {i+1}'} for i,p in enumerate(root.findall('plate'))] or [{'number':1,'name':'Plate 1'}]

def project_materials(path):
    with zipfile.ZipFile(path) as z:settings=json.loads(z.read(tower.SETTINGS))
    types=settings.get('filament_type',[])
    return [{'slot':i+1,'material':str(value),'rigid':tower.rigid_material(value)} for i,value in enumerate(types[:tower.MAX_TOOLS])]

def tower_options(data):
    return {'width':float(data.get('width',30)), 'rib_size':float(data.get('rib_size',25)),
            'purge_volume':float(data.get('purge_volume',5)),
            'support_slot':int(data['support_slot']) if data.get('support_slot') else None,
            'frame_slot':int(data['frame_slot']) if data.get('frame_slot') else None}


def extract_gcode(folder,plate):
    """Only inspect newly produced output, never stale G-code from a source ZIP."""
    candidates=list(folder.glob('*.gcode'))
    if len(candidates)==1:return candidates[0]
    for p in candidates:
        if p.name==f'plate_{plate}.gcode':return p
    archive=folder/'sliced.3mf'
    if archive.exists():
        with zipfile.ZipFile(archive) as z:
            names=[n for n in z.namelist() if n.lower().endswith('.gcode')]
            exact=[n for n in names if Path(n).name==f'plate_{plate}.gcode']
            chosen=exact or names
            if len(chosen)==1:
                target=folder/'exported.gcode'
                with z.open(chosen[0]) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
                return target
    raise tower.TowerError('Orca did not export a unique plain G-code file for the selected plate. Use manual export below.')

class Workflow:
    def __init__(self):
        self.stage_started=time.monotonic();self.started=time.monotonic();self.log_path=None
        self.lock=threading.Lock();self.cancelled=threading.Event();self.process=None
        self.state={'busy':False,'stage':'ready','message':'Choose the original 3MF and plate.','events':[],'project':'','result':None,'folder':''}
        self.options=tower_options({});self.source=None;self.output=None;self.plate=1;self.orca=load_settings().get("orca",DEFAULT_ORCA)
    def update(self,stage,message,**values):
        with self.lock:
            self.stage_started=time.monotonic()
            self.state.update(stage=stage,message=message,**values)
            self.state['events'].append(message)
    def snapshot(self):
        with self.lock:result=json.loads(json.dumps(self.state))
        result['stage_seconds']=int(time.monotonic()-self.stage_started)
        result['elapsed_seconds']=int(time.monotonic()-self.started)
        if result['busy'] and self.log_path and self.log_path.exists():
            with self.log_path.open('rb') as stream:
                stream.seek(max(0,self.log_path.stat().st_size-3000));result['live_log']=stream.read().decode(errors='replace')
        return result
    def check_cancel(self):
        if self.cancelled.is_set():raise tower.TowerError('Stopped. Existing output files are retained.')
    def launch(self,target,*args):
        with self.lock:
            if self.state['busy']:raise tower.TowerError('A step is already running.')
            self.state['busy']=True
            self.started=time.monotonic();self.stage_started=self.started;self.log_path=None
        self.cancelled.clear()
        def run():
            try:target(*args)
            except Exception as error:self.update('needs_attention',str(error))
            finally:
                with self.lock:self.state['busy']=False
        threading.Thread(target=run,daemon=True).start()
    def setup(self,data):
        source=Path(data['source']).expanduser().resolve()
        if source.suffix.lower()!='.3mf' or not source.is_file():raise tower.TowerError('Choose an existing original .3mf project.')
        with zipfile.ZipFile(source) as archive:
            config=ET.fromstring(archive.read(tower.MODEL_SETTINGS))
            if tower.MANIFEST in archive.namelist() or any(tower.metadata(o).get('name','').startswith('IPT_') for o in config.findall('object')):
                raise tower.TowerError('Choose the original reference project without an existing custom prime tower.')
        selected=int(data.get('plate',1))
        if selected not in [p['number'] for p in plates(source)]:raise tower.TowerError('Invalid plate number.')
        parent=Path(data.get('output') or source.parent).expanduser().resolve()
        parent.mkdir(parents=True,exist_ok=True)
        folder=parent/(source.stem+'_tower_'+time.strftime('%Y%m%d_%H%M%S')+'_'+secrets.token_hex(2));folder.mkdir()
        self.source=source;self.plate=selected;self.orca=save_orca(data.get('orca') or self.orca)
        self.options=tower_options(data)
        self.output=folder/'tower.3mf'
        self.update('ready','Output folder created. Original project will remain unchanged.',folder=str(folder),project='',result=None)
    def slice(self,project,tag):
        self.check_cancel()
        folder=self.output.parent/tag;folder.mkdir(exist_ok=False)
        executable=resolve_orca(self.orca)
        args=[str(executable),'--slice',str(self.plate),'--arrange','0','--orient','0','--outputdir',str(folder),'--export-3mf','sliced.3mf',str(project)]
        log=folder/'orca.log';self.log_path=log
        with log.open('wb') as stream:
            self.process=subprocess.Popen(args,stdout=stream,stderr=subprocess.STDOUT)
            try:
                while self.process.poll() is None:
                    if self.cancelled.wait(.25):
                        self.process.terminate()
                        try:self.process.wait(timeout=5)
                        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
                        self.check_cancel()
                code=self.process.returncode
            finally:self.process=None
        if code:raise tower.TowerError(f'Orca automatic slicing stopped (exit {code}). Log: {log}. Continue with the manual export step below.')
        return extract_gcode(folder,self.plate)
    def generate(self,reference):
        self.check_cancel();self.update('generate','Building the tower on the selected plate…')
        manifest=tower.Project(self.source,self.plate).generate(self.output,reference_gcode=reference,**self.options)
        self.update('generate',f"Frame: slot {manifest['frame_slot']} ({manifest['frame_material']}). {len(manifest['lanes'])} sections; height {manifest['tower_height']:.3f} mm; minimum priming {manifest['purge_volume']:g} mm³.")
        self.check_cancel();self.update('tower_ready','Tower project created. Next: slice its selected plate and verify the exported G-code.',project=str(self.output))
    def verify(self,gcode):
        self.check_cancel();self.update('audit','Checking material separation, section coverage, and every post-pickup tower visit…',result=None)
        result=tower.audit(gcode,tower.load_manifest(self.output))
        self.check_cancel()
        (self.output.parent/'audit.json').write_text(json.dumps(result,indent=2))
        if result['passed']:
            self.update('passed',f"Passed: {result['first_destination_checks']} tower-first checks. Open the project in Orca for review.",result=result)
        else:self.update('failed','Audit did not pass. Review the errors below; this output is not verified.',result=result)
    def automatic(self,data):
        self.setup(data);self.update('reference_slice',f'Slicing original plate {self.plate} in Orca…')
        ref=self.slice(self.source,'reference_slice');self.generate(ref)
        self.update('tower_slice','Slicing the tower project in Orca…')
        final=self.slice(self.output,'tower_slice');self.verify(final)
        if self.state['stage']=='passed' and data.get('open_after',True):self.open_project()
    def manual_generate(self,data):
        # Reuse an interrupted automatic job only if it has no generated tower.
        if not self.source or self.output.exists() or self.source!=Path(data['source']).expanduser().resolve() or self.plate!=int(data.get('plate',1)):self.setup(data)
        self.options=tower_options(data)
        self.generate(Path(data['reference']).expanduser())
    def open_project(self):
        if not self.output or not self.output.exists():raise tower.TowerError('Generate the tower project first.')
        executable=resolve_orca(self.orca)
        bundle=next((p for p in executable.parents if p.suffix=='.app'),None)
        if sys.platform=='darwin' and bundle:
            subprocess.run(['open','-a',str(bundle),str(self.output)],check=True)
        else:subprocess.Popen([str(executable),str(self.output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def browse_files(path,kind):
    folder=Path(path or Path.home()/'Documents').expanduser().resolve()
    if folder.is_file():folder=folder.parent
    extensions={'source':{'.3mf'},'reference':{'.gcode'},'final':{'.gcode'}}
    entries=[]
    try:
        for p in folder.iterdir():
            if p.name.startswith('.'):continue
            directory=p.is_dir();app=kind=='orca' and p.suffix=='.app'
            if directory or kind=='orca' or p.suffix.lower() in extensions.get(kind,set()):
                entries.append({'name':p.name,'path':str(p),'directory':directory and not app})
    except PermissionError:
        raise tower.TowerError('Access to this folder was blocked. Check folder permissions or paste the full file path in the main window.')
    return {'folder':str(folder),'parent':str(folder.parent),'entries':sorted(entries,key=lambda e:(not e['directory'],e['name'].lower()))}

def serve(open_browser=True):
    workflow=Workflow();token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,data,status=200,kind='application/json'):
            payload=data.encode() if isinstance(data,str) else json.dumps(data).encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(payload)
        def allowed(self):
            return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}' and self.headers.get('X-Tower-Token')==token and self.headers.get('Origin',f'http://127.0.0.1:{self.server.server_port}')==f'http://127.0.0.1:{self.server.server_port}'
        def do_GET(self):
            if self.path=='/tower-icon.png':
                if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':return self.send({'error':'Invalid host'},403)
                payload=Path(__file__).with_name('tower-icon.png').read_bytes()
                self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
            if self.path=='/':
                if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':return self.send({'error':'Invalid host'},403)
                return self.send((Path(__file__).with_name('wizard.html')).read_text().replace('__TOKEN__',token),kind='text/html; charset=utf-8')
            if not self.allowed():return self.send({'error':'Unauthorized'},403)
            if self.path=='/settings':return self.send({'orca':workflow.orca,'platform':sys.platform})
            if self.path=='/state':return self.send(workflow.snapshot())
            self.send({'error':'Not found'},404)
        def do_POST(self):
            if not self.allowed():return self.send({'error':'Unauthorized'},403)
            try:
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<100000:raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(size));action=self.path
                if action=='/settings':
                    if workflow.snapshot()['busy']:raise tower.TowerError('Wait for the current step to finish before changing Orca.')
                    workflow.orca=save_orca(data.get('orca',''));return self.send({'orca':workflow.orca})
                if action=='/browse':return self.send(browse_files(data.get('path'),data['kind']))
                if action=='/plates':return self.send({'plates':plates(data['source']),'materials':project_materials(data['source'])})
                if action=='/auto':workflow.launch(workflow.automatic,data)
                elif action=='/generate':workflow.launch(workflow.manual_generate,data)
                elif action=='/audit':
                    if not workflow.output:raise tower.TowerError('Generate a tower in this session first.')
                    workflow.launch(workflow.verify,Path(data['final']).expanduser())
                elif action=='/open':workflow.open_project()
                elif action=='/cancel':workflow.cancelled.set()
                else:return self.send({'error':'Not found'},404)
                self.send({'ok':True})
            except Exception as error:self.send({'error':str(error)},400)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    url=f'http://127.0.0.1:{server.server_port}/'
    print('Prime Tower Wizard: '+url,flush=True)
    print('Keep this window running. Press Control-C to close the wizard.',flush=True)
    if open_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:workflow.cancelled.set()
    finally:server.server_close()

if __name__=='__main__':serve('--no-browser' not in sys.argv)
