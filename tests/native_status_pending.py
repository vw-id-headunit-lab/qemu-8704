from pathlib import Path
import ast,asyncio,json,sys,tempfile,threading,time
from aiohttp import web

r=Path.cwd();source=Path(sys.argv[1])if len(sys.argv)>1 else r/'tools/hvf_dashboard.py';report={'scope':'Real status handler with blocked host UART fixture; not original OS boot acceptance','input':'Status returns pending promptly, then reports ready only after same-epoch probe completes'};release=threading.Event()
try:
 worker=next(n for n in ast.parse(source.read_bytes()).body if isinstance(n,ast.AsyncFunctionDef)and n.name=='worker');nodes=[next(n for n in worker.body if isinstance(n,ast.AsyncFunctionDef)and n.name==name)for name in ['native_services','status']];factory=ast.parse('async def factory():\n native_probe=None\n native_probe_epoch=None\n native_probe_started=0\n return status\n').body[0];factory.body[-1:-1]=nodes
 with tempfile.TemporaryDirectory(prefix='native-status-')as tmp:
  d=Path(tmp);calls=[]
  def owner(guest):return d,{'qemu_pid':42},0
  def run(*args,**kwargs):
   calls.append(args);release.wait(2);return {'session':str(d),'qemu_pid':42,'framework_boot_completed':True,'stdout':'Service activity: found\nService window: found\nService package: found\n'}
  env=dict(asyncio=asyncio,time=time,web=web,render_session=owner,session=owner,run=run,locks={n:asyncio.Lock()for n in ['linux','android']},qnx_status=lambda:{'live':True,'session':str(d),'qemu_pid':42})
  exec(compile(ast.Module(body=[factory],type_ignores=[]),str(source),'exec'),env)
  async def go():
   try:
    status=await env['factory']();start=time.monotonic();first=await asyncio.wait_for(status(None),.2);elapsed=time.monotonic()-start;pending=json.loads(first.body);assert pending['android'].get('framework_pending')is True and pending['android']['framework_services_ready']is False
    release.set();await asyncio.sleep(.08);second=json.loads((await asyncio.wait_for(status(None),.2)).body);assert second['android']['framework_services_ready']is True and len(calls)==1;return elapsed,pending,second
   finally:release.set();await asyncio.sleep(.03)
  elapsed,pending,completed=asyncio.run(go());report.update(first_response_seconds=elapsed,first=pending,completed=completed,probe_calls=len(calls),exit=0)
except Exception as error:release.set();report.update(exit=2,error=repr(error))
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
