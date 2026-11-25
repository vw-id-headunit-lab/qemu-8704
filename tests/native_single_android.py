from pathlib import Path
import argparse,ast,contextlib,io,json,os,subprocess,sys,tempfile,time,types
from unittest.mock import patch
root=Path(__file__).resolve().parents[1];source=Path(sys.argv[1])if len(sys.argv)>1 else root/'tools/macos_hvf.py';rows=[]
try:
 tree=ast.parse(source.read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='main');unit=ast.Module(body=[fn],type_ignores=[])
 for action,existing in [('serve',True),('start',True),('start',False)]:
  with tempfile.TemporaryDirectory(prefix='native-route-fixture-')as tmp:
   base=Path(tmp);out=base/'var/hvf';out.mkdir(parents=True);calls=[]
   if existing:
    session=out/'existing';session.mkdir();(session/'session.json').write_text(json.dumps({'state':'running','qemu_pid':99}));(out/'current.json').write_text(json.dumps({'session':str(session)}))
   class Child:
    pid=42
    def __init__(self,argv,**kw):
     session=Path(argv[argv.index('--session')+1]);(session/'session.json').write_text(json.dumps({'state':'running','qemu_pid':99}));calls.append('center-worker')
    def poll(self):return None
   class Configuration:
    def __getitem__(self,key):return out if key=='output'else None
    def validate(self,kind):assert kind=='hmi'
   conf=types.SimpleNamespace(load=lambda p:Configuration())
   modules={}
   for name,label in [('hvf_android','auxiliary-generic-Android'),('hvf_qnx','original-native-Android'),('hvf_network','network'),('hvf_dashboard','dashboard')]:
    mod=types.ModuleType(name)
    def ensure(label=label):calls.append(label);return 'http://127.0.0.1:1234/'
    mod.ensure=ensure;modules[name]=mod
   guest=types.ModuleType('hvf_guest_command');guest.session=lambda name:(None,None,None);modules['hvf_guest_command']=guest
   env=dict(Path=Path,argparse=argparse,json=json,os=os,sys=sys,time=time,subprocess=types.SimpleNamespace(Popen=Child,CalledProcessError=subprocess.CalledProcessError),ROOT=base,CONFIG=base/'config.json',Config=conf,__file__=str(base/'tools/macos_hvf.py'))
   exec(compile(unit,str(source),'exec'),env);output=io.StringIO()
   with patch.dict(sys.modules,modules),patch.object(sys,'argv',['MEB-EMU',action]),contextlib.redirect_stdout(output):rc=env['main']()
   expected=([]if existing or action=='serve'else['center-worker'])+['original-native-Android','network','dashboard'];row=dict(action=action,existing_center=existing,calls=calls,stdout=output.getvalue(),exit=rc);rows.append(row);assert rc==0 and calls==expected,row
 result=dict(scope='Executed launch-routing fixture, not guest readiness',input='serve, start with live center, cold start; one original native Android, no auxiliary generic renderer',rows=rows,exit=0)
except Exception as error:result=dict(rows=rows,error=repr(error),exit=2)
print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(result['exit'])
