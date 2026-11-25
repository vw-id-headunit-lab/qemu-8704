from pathlib import Path
import importlib.util,json,subprocess,sys,tempfile
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'tools'));p=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'tools/native_guest_command.py';report={'source':str(p),'scope':'Host UART protocol fixture only, not original guest acceptance','input':'Android ticket has no END; Linux independently executes printf without newline and exit7; retain Android ticket without resend'}
try:
 spec=importlib.util.spec_from_file_location('native_isolation_under_test',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 with tempfile.TemporaryDirectory(prefix='native-isolation-')as tmp:
  root=Path(tmp);android=root/'exynos-uart.bin';android.write_text('MEBB_1111111111111111\n');ticket=dict(guest='android',capture=android,start=0,begin='MEBB_1111111111111111',end='MEBE_1111111111111111',command='retained fixture only',session=str(root),qemu_pid=42)
  m._epoch=(str(root),42)
  if isinstance(m._pending,dict):m._pending['android']=ticket
  else:m._pending=ticket
  class Wire:
   session=root
   calls=[]
   def focus(self):return {'fixture':True}
   def serial(self,guest,line):
    assert guest=='linux';self.calls.append((guest,line));a=subprocess.run(['/bin/sh','-c',line],capture_output=True);report['host_shell']=dict(stdout=a.stdout.decode(),stderr=a.stderr.decode(),exit=a.returncode)
    with(root/'linux-usi04-uart.bin').open('ab')as f:f.write(a.stdout)
    return {'fixture_serial':True}
  wire=Wire();reply=m._exchange(wire,'linux',"printf FIXTURE_NO_NEWLINE; exit 7");report['reply']=reply;report['serial_calls']=len(wire.calls)
  assert reply['stdout']=='FIXTURE_NO_NEWLINE'and reply['exit']==7 and len(wire.calls)==1
  assert m._pending.get('android')is ticket and 'linux'not in m._pending
  assert m._receipt(ticket)is None
  report['android_original_ticket_retained']=True
 report['exit']=0
except Exception as e:report.update(exit=2,error=repr(e))
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
