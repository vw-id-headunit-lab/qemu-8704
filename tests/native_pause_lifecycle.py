from pathlib import Path
import ast,json,socket,subprocess,sys,tempfile,threading,time
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'tools'));from hvf_network import qmp
source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'src/emu/vlm_offline_live_darwin.py';report={'source':str(source),'input':'Actual QEMU paused, then external cont; persistent observer retains the same process; finite observation still ends'};process=None;out=err=None
def port():
 with socket.socket()as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
try:
 tree=ast.parse(source.read_bytes());probe=next(x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name=='probe');nodes=[x for x in ast.walk(probe)if isinstance(x,ast.If)and ast.unparse(x.test)=="not status_before_stop.get('running', False)"];assert len(nodes)==1
 code='def observe(read,deadline,lifetime):\n watch=panic_watch=abox_watch=None\n stop_reason=None;states=[]\n while time.monotonic()<deadline:\n  time.sleep(.02)\n  status_before_stop=read();states.append(status_before_stop[\'status\'])\n'+''.join('  '+line+'\n'for line in ast.unparse(nodes[0]).splitlines())+'  if status_before_stop.get(\'running\'):return dict(result=\'resumed\',states=states)\n return dict(result=\'ended\',reason=stop_reason,states=states)\n';env={'time':time};exec(code,env)
 with tempfile.TemporaryDirectory(prefix='native-pause-')as tmp:
  p1,p2=port(),port();cmd=[str(r/'runtime/darwin/qemu-system-aarch64'),'-machine','virt','-accel','qtest','-m','128M','-display','none','-serial','null','-nic','none','-S','-qmp',f'tcp:127.0.0.1:{p1},server=on,wait=off','-qmp',f'tcp:127.0.0.1:{p2},server=on,wait=off'];out=open(Path(tmp)/'stdout','w+b');err=open(Path(tmp)/'stderr','w+b');process=subprocess.Popen(cmd,stdout=out,stderr=err)
  deadline=time.monotonic()+5
  while True:
   try:qmp(p2,'query-status',{});break
   except(ConnectionError,OSError):
    if process.poll()is not None or time.monotonic()>deadline:raise
    time.sleep(.02)
  qmp(p2,'cont',{});qmp(p2,'stop',{});assert qmp(p2,'query-status',{})['status']=='paused'
  def resume():time.sleep(.3);qmp(p2,'cont',{})
  thread=threading.Thread(target=resume);thread.start();actual=env['observe'](lambda:qmp(p1,'query-status',{}),time.monotonic()+3,{'profile':'until-stop-v1'});thread.join();report['persistent']=actual;report['original_process_alive']=process.poll()is None;assert actual['result']=='resumed'and 'paused'in actual['states']and report['original_process_alive']
  qmp(p2,'stop',{});finite=env['observe'](lambda:qmp(p1,'query-status',{}),time.monotonic()+1,None);report['finite']=finite;assert finite['result']=='ended'and finite['reason']=='observed-vm-stop'
 report['exit']=0
except Exception as e:report.update(exit=2,error=repr(e))
finally:
 if process is not None:
  if process.poll()is None:process.terminate()
  process.wait(timeout=5);report['fixture_qemu_exit_after_cleanup']=process.returncode
 for stream,name in [(out,'native_stdout'),(err,'native_stderr')]:
  if stream is not None:stream.flush();stream.seek(0);report[name]=stream.read().decode('utf8','replace');stream.close()
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
