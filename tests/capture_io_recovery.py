from pathlib import Path
import ast,errno,json,subprocess,sys,tempfile,threading,time,types
r=Path(__file__).resolve().parents[1];source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'src/hvf_bench/src/hmi_bench/launcher.py';report={'source':str(source),'input':'Actual child pipe writes 8192 bytes, sleeps 1.2s, then writes 2MiB plus END; first log write raises ENOSPC; later writes available','scope':'Host log failure injection; actual child pipe backpressure, not OEM boot acceptance'};child=None
try:
 tree=ast.parse(source.read_bytes());nodes=[x for x in ast.walk(tree)if isinstance(x,ast.FunctionDef)and x.name=='capture'];assert len(nodes)==1
 node=nodes[0]
 for n in ast.walk(node):
  if isinstance(n,ast.ImportFrom):n.level=0;n.module='fixture_progress'
 progress=types.ModuleType('fixture_progress');progress.MARKERS=[];sys.modules['fixture_progress']=progress
 with tempfile.TemporaryDirectory(prefix='capture-io-')as tmp:
  path=Path(tmp);calls=[];errors=[]
  class Sink:
   def write(self,data):raise OSError(errno.ENOSPC,'fixture disk full')
   def close(self):pass
   def __enter__(self):return self
   def __exit__(self,*args):self.close()
  class Output:
   def __truediv__(self,name):
    class File:
     def open(self,*args,**kwargs):
      calls.append(name)
      return Sink()if len(calls)==1 else(path/name).open(*args,**kwargs)
    return File()
  child=subprocess.Popen([sys.executable,'-B','-c',"import os,time;os.write(1,b'A'*8192);time.sleep(1.2);b=b'B'*(2*1024*1024)+b'END\\n';n=0\nwhile n<len(b):n+=os.write(1,b[n:])"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  record={'milestone_wall_seconds':{}};env=dict(output=Output(),process=child,record=record,record_lock=threading.Lock(),measurement=None,started=time.monotonic(),time=time,threading=threading,session_path=path/'session.json',write_json=lambda p,j:p.write_text(json.dumps(j)))
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
  def worker():
   try:env['capture']('stdout')
   except Exception as error:errors.append(repr(error))
  thread=threading.Thread(target=worker,daemon=True);thread.start()
  try:rc=child.wait(timeout=5)
  except subprocess.TimeoutExpired:rc=None
  thread.join(.1);logged=(path/'stdout.txt').read_bytes()if(path/'stdout.txt').exists()else b''
  report.update(child_exit=rc,reader_alive=thread.is_alive(),reader_errors=errors,log_bytes=len(logged),log_ends_END=logged.endswith(b'END\n'),record=record,open_calls=len(calls))
  assert rc==0 and not thread.is_alive()and not errors and logged==b'B'*(2*1024*1024)+b'END\n'
  state=record['capture_io']['stdout'];assert state['errno']==28 and state['dropped_bytes']==8192 and 'active_error'not in state and 'recovered_wall_seconds'in state
 report['exit']=0
except Exception as error:report.update(exit=2,error=repr(error))
finally:
 if child is not None:
  if child.poll()is None:child.kill()
  child.wait();report['child_stderr']=child.stderr.read().decode();child.stdout.close();child.stderr.close()
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
