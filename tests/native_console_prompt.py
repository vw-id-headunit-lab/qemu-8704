from pathlib import Path
import ast,json,re,sys,tempfile,threading,types
r=Path(__file__).resolve().parents[1];source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'tools/native_guest_command.py';report={'source':str(source),'input':'Native board running before Android shell; do not transmit bootstrap until original :/ $ prompt is captured','scope':'Host startup ordering fixture, not native command execution acceptance'}
try:
 node=next(n for n in ast.parse(source.read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='run')
 with tempfile.TemporaryDirectory(prefix='console-prompt-')as tmp:
  d=Path(tmp);capture=d/'exynos-uart.bin';capture.write_bytes(b'OFFLINE PREINIT: configuration staged; exec original /init\r\n');calls=[]
  def exchange(bridge,guest,command,**kwargs):
   calls.append(command);return dict(stdout='2000\n5.4.131-android11-2-ab151\n'if command=='id -u; uname -r'else'PAYLOAD\n',exit=0,capture=str(capture),raw_start=0,raw_end=20,raw_sha256='fixture')
  env=dict(_source=lambda *args:None,_runs={'android':threading.RLock()},_identities={},_controller=lambda guest:(types.SimpleNamespace(session=d),d,{'qemu_pid':123}),_capture=lambda bridge,guest:capture,_exchange=exchange,hashlib=__import__('hashlib'),re=re,time=__import__('time'))
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
  try:env['run']('android','echo PAYLOAD')
  except RuntimeError as error:report['before_prompt_error']=str(error)
  report['transmitted_before_prompt']=list(calls);assert calls==[]
  capture.write_bytes(capture.read_bytes()+b':/ $ ');reply=env['run']('android','echo PAYLOAD');report.update(after_prompt=reply,transmitted_after_prompt=calls);assert reply['stdout']=='PAYLOAD\n'and reply['uid']==2000 and len(calls)==2
 report['exit']=0
except Exception as error:report.update(exit=2,error=repr(error))
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
