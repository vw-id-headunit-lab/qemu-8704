from pathlib import Path
import argparse,ast,json,os,signal,subprocess,sys,tempfile,time,types
from unittest.mock import patch
r=Path(__file__).resolve().parents[1];source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'tools/macos_hvf.py';report={'source':str(source),'input':'Two owned processes acknowledge quit but exit 350ms later; immediate restart must not reuse either process'}
children=[]
try:
 tree=ast.parse(source.read_bytes());names={'main','process_identity','stop_owners','wait_stopped'};body=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in names]
 with tempfile.TemporaryDirectory(prefix='stop-completion-')as tmp:
  root=Path(tmp);hvf=root/'var/hvf';hvf.mkdir(parents=True);session=hvf/'hmi-original';session.mkdir()
  code="import signal,time,sys;signal.signal(signal.SIGTERM,lambda a,b:(time.sleep(.35),sys.exit(0)));print('READY',flush=True);time.sleep(30)"
  for role in ['linux','android']:
   p=subprocess.Popen([sys.executable,'-B','-c',code,str(root),role],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);children.append(p);assert p.stdout.readline()=='READY\n'
  linux,android=children;state={'state':'running','qemu_pid':linux.pid,'ports':{'qmp_port':1,'debug_port':3}}
  (hvf/'current.json').write_text(json.dumps({'session':str(session),'worker_pid':linux.pid}));(session/'session.json').write_text(json.dumps(state))
  aj={'session':str(hvf/'android-original'),'worker_pid':android.pid,'qemu_pid':android.pid,'qmp_port':2};(hvf/'android-current.json').write_text(json.dumps(aj))
  events=[]
  def module(name,**members):
   m=types.ModuleType(name);m.__dict__.update(members);return m
  def quit_guest(port,operation):
   assert operation=='quit';child=linux if port==1 else android;os.kill(child.pid,signal.SIGTERM);events.append({'port':port,'alive_after_quit_ack':child.poll()is None})
  mods={'hvf_network':module('hvf_network',stop=lambda:None),'hvf_qnx':module('hvf_qnx',stop=lambda:None),'hvf_guest_command':module('hvf_guest_command',session=lambda name:(Path(aj['session']),aj,4)),'hmi_bench.launcher':module('hmi_bench.launcher',qmp=quit_guest)}
  env=dict(ROOT=root,CONFIG=root/'config.json',Config=types.SimpleNamespace(load=lambda p:{'output':hvf}),argparse=argparse,json=json,os=os,subprocess=subprocess,sys=sys,time=time,Path=Path,__file__=str(source))
  exec(compile(ast.Module(body=body,type_ignores=[]),str(source),'exec'),env)
  with patch.dict(sys.modules,mods),patch.object(sys,'argv',[str(source),'stop']):
   start=time.monotonic();status=env['main']();elapsed=time.monotonic()-start
  alive=[p.poll()is None for p in children];report.update(stop_exit=status,quit_events=events,alive_on_stop_return=alive,elapsed_seconds=elapsed)
  assert status==0 and len(events)==2 and all(x['alive_after_quit_ack']for x in events)and alive==[False,False]
  changed=[]
  if 'wait_stopped'in env:
   with patch.dict(env,process_identity=lambda pid:'replacement owner',time=types.SimpleNamespace(monotonic=time.monotonic,sleep=lambda x:changed.append(x))):env['wait_stopped']({linux.pid:'original owner'},timeout=0)
   assert not changed
 report['exit']=0
except Exception as e:report.update(exit=2,error=repr(e))
finally:
 for p in children:
  if p.poll()is None:p.terminate()
  p.wait(timeout=3)
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
