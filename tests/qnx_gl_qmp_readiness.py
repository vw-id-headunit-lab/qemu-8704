from pathlib import Path
import importlib.util,sys,json
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'tools'));sys.path.insert(0,str(r/'src/emu'));source=Path(sys.argv[1]).resolve();spec=importlib.util.spec_from_file_location('readiness_copy',source);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);real=mod.QMP;original_session=mod.session;calls=0;report={'source':str(source),'input':'Three initial QMP connection refusals then real current Linux QMP handshake; same actual Linux process validated every retry'}
def delayed(port):
 global calls
 calls+=1
 if calls<=3:raise ConnectionRefusedError(61,'controlled socket-listen delay')
 return real(port)
mod.QMP=delayed
try:
 s,j,debug,q=mod.owned()
 try:state=q.execute('query-status')
 finally:q.close()
 assert calls==4 and state['running'];report.update(exit=0,calls=calls,session=str(s),qemu_pid=j['qemu_pid'],actual_status=state)
except ConnectionRefusedError:report.update(exit=2,calls=calls)
if report['exit']==0:
 owner_calls=0;socket_calls=0
 def replaced(guest):
  global owner_calls
  owner_calls+=1;s,j,debug=original_session(guest)
  if owner_calls>1:j=dict(j,qemu_pid=j['qemu_pid']+1)
  return s,j,debug
 def refused(port):
  global socket_calls
  socket_calls+=1;raise ConnectionRefusedError(61,'controlled refusal before owner transition')
 mod.session=replaced;mod.QMP=refused
 try:mod.owned();raise AssertionError('owner change was accepted')
 except ValueError as e:assert 'owner changed' in str(e) and socket_calls==1;report['changed_owner_rejected']=True
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
