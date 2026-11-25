from pathlib import Path
import ast,asyncio,importlib.util,inspect,json,sys,tempfile,threading,time

r=Path.cwd();base=Path(sys.argv[1])if len(sys.argv)>1 else r;sys.path.insert(0,str(r/'tools'));report={'scope':'Host protocol/background scheduling fixtures only; no original OS acceptance','input':'Resume completed identity nonce without a new UART line; collect strict native frames with zero browser requests'}
try:
 p=base/'tools/native_guest_command.py';spec=importlib.util.spec_from_file_location('identity_resume_under_test',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 with tempfile.TemporaryDirectory(prefix='identity-resume-')as tmp:
  d=Path(tmp);capture=d/'exynos-uart.bin';begin='MEBB_0123456789abcdef';end='MEBE_0123456789abcdef';payload='2000\n5.4.131-android11-2-ab151\n';capture.write_text(begin+'\n'+payload+'\n'+end+':0\n');ticket=dict(guest='android',capture=capture,start=0,begin=begin,end=end,command='id -u; uname -r',session=str(d),qemu_pid=42,framing=2);other={'nonce':'LINUX_KEPT'};m._pending={'android':ticket,'linux':other};m._epoch=(str(d),42)
  class Bridge:
   session=d
   serial_calls=0
   def focus(self):return {'running':True}
   def serial(self,*args):self.serial_calls+=1;raise AssertionError('Completed identity was sent again')
  bridge=Bridge();kwargs={'reuse_completed':True}if 'reuse_completed'in inspect.signature(m._exchange).parameters else {};reply=m._exchange(bridge,'android','id -u; uname -r',**kwargs);assert reply['stdout']==payload and reply['exit']==0 and reply['reused_completed_nonce']==begin and bridge.serial_calls==0 and m._pending.get('linux')is other
  report['completed_identity_reused']=reply;report['uart_lines_sent']=bridge.serial_calls
  capture.write_text(begin+'\n');m._pending['android']=ticket
  try:m._exchange(bridge,'android','id -u; uname -r',timeout=.1,**kwargs);raise AssertionError('Pending identity unexpectedly completed')
  except (RuntimeError,TimeoutError):pass
  assert m._pending['android']is ticket and bridge.serial_calls==0;report['incomplete_identity_retained']=True
 source=base/'tools/hvf_dashboard.py';node=next(x for x in ast.parse(source.read_bytes()).body if isinstance(x,ast.AsyncFunctionDef)and x.name=='maintain_native_frames');calls=[]
 def status():calls.append({'thread':threading.current_thread().name,'at':time.monotonic()});return {'strict_fixture':True}
 env=dict(asyncio=asyncio,qnx_status=status);exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
 async def check():
  stop=asyncio.Event();task=asyncio.create_task(env['maintain_native_frames'](stop));await asyncio.sleep(2.1);stop.set();await asyncio.wait_for(task,1)
 asyncio.run(check());assert len(calls)>=2;report.update(browser_requests=0,strict_frame_collector_calls=len(calls),collector_threads=[x['thread']for x in calls],exit=0)
except Exception as error:report.update(exit=2,error=repr(error))
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
