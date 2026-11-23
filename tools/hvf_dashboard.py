from pathlib import Path
import sys,os,json,secrets,subprocess,time,asyncio,signal
from aiohttp import web
from hvf_console import ROOT
from hvf_guest_command import run,session
STATE=ROOT/'var/hvf/dashboard.json'
PAGE=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>8704 中控与双指令台</title><style>
*{box-sizing:border-box}body{margin:0;background:#0d1420;color:#e9f0fa;font:14px system-ui}header{padding:12px 18px;background:#192638;display:flex;flex-wrap:wrap;gap:12px;align-items:center}header strong{font-size:18px}#state{font-size:12px;color:#b9cce1}main{max-width:1800px;margin:auto;padding:12px;display:grid;gap:12px}.display{background:#111c2b;border:1px solid #30465f;border-radius:10px;overflow:hidden}h2{font-size:14px;margin:0;padding:10px 12px;background:#1b2a3e}.display iframe{border:0;width:100%;height:clamp(340px,calc(56.25vw + 100px),1080px);min-height:340px;display:block}.pending{display:grid;grid-template-columns:1fr 1fr;gap:12px}.pending div{border:1px dashed #42566e;border-radius:8px;padding:12px;color:#a8bad0}.consoles{display:grid;grid-template-columns:1fr 1fr;gap:12px}section{background:#152235;border:1px solid #30465f;border-radius:8px;overflow:hidden}form{padding:12px;display:grid;gap:8px}label{font-weight:600}textarea{width:100%;min-height:72px;resize:vertical;background:#08111e;color:#deebfa;border:1px solid #4d6685;border-radius:4px;padding:9px;font:13px ui-monospace,monospace}button{justify-self:start;background:#2d88ba;color:white;border:0;border-radius:4px;padding:8px 15px;cursor:pointer}button:disabled{opacity:.5}pre{margin:0 12px 12px;min-height:130px;max-height:360px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#08111e;padding:10px;border-radius:4px;font:12px ui-monospace,monospace}.sub{font-size:12px;color:#b0c1d5;font-weight:400}@media(max-width:760px){.consoles{grid-template-columns:1fr}}
</style><header><strong>8704 原中控 · HVF</strong><span id="state">连接运行会话…</span></header><main><div class="display"><h2>原 HMI 实时画面 · 完整等比显示</h2><iframe title="原中控实时画面" src="__CENTER__"></iframe></div><div class="pending"><div>IC 仪表：QNX 画面待接入</div><div>HUD：QNX 画面待接入</div></div><div class="consoles"><section><h2>Linux 指令台 <span class="sub">原 8704 用户空间 · UID 65534</span></h2><form data-guest="linux"><label for="linux-command">Linux 命令</label><textarea id="linux-command" spellcheck="false">id && uname -m</textarea><button type="submit">执行 Linux</button></form><pre id="linux-output" role="log">等待命令</pre></section><section><h2>Android 11 指令台 <span class="sub">原 8704 用户空间 · UID 2000；框架启动待接续</span></h2><form data-guest="android"><label for="android-command">Android 命令</label><textarea id="android-command" spellcheck="false">id && uname -m</textarea><button type="submit">执行 Android</button></form><pre id="android-output" role="log">等待命令</pre></section></div></main><script>
const token='__TOKEN__';for(const form of document.querySelectorAll('form'))form.addEventListener('submit',async e=>{e.preventDefault();const guest=form.dataset.guest,button=form.querySelector('button'),out=document.getElementById(guest+'-output'),command=form.querySelector('textarea').value;button.disabled=true;out.textContent='正在执行…';try{const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify({guest,command})});const data=await r.json();if(!r.ok)throw Error(data.error);out.textContent='$ '+command+'\n'+data.stdout+'\n[exit='+data.exit+' · uid='+data.uid+' · QEMU '+data.qemu_pid+']'}catch(e){out.textContent='执行失败：'+e.message}finally{button.disabled=false}});
async function status(){try{const r=await fetch('/api/status',{cache:'no-store'}),s=await r.json();document.getElementById('state').textContent='中控 '+(s.linux.live?'运行':'未运行')+' · Android 原指令台 '+(s.android.live?'运行':'未运行')+' · 固件 8704'}catch(e){document.getElementById('state').textContent='连接异常：'+e.message}}status();setInterval(status,5000);
</script></html>'''
def descriptor():
 return json.loads(STATE.read_bytes())if STATE.exists()else None
def ensure():
 old=descriptor()
 if old:
  try:
   os.kill(old['pid'],0);ps=subprocess.run(['ps','-p',str(old['pid']),'-o','command='],capture_output=True,text=True).stdout
   if str(Path(__file__).resolve())in ps:return old['url']
  except ProcessLookupError:pass
 root=ROOT/'var/hvf';root.mkdir(exist_ok=True)
 with(root/'dashboard.stdout').open('ab')as out,(root/'dashboard.stderr').open('ab')as err:child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker'],stdout=out,stderr=err,cwd=ROOT,start_new_session=True)
 for _ in range(100):
  d=descriptor()
  if d and d['pid']==child.pid:return d['url']
  if child.poll()is not None:raise RuntimeError('Dashboard worker exited; inspect var/hvf/dashboard.stderr')
  time.sleep(.1)
 raise TimeoutError('Dashboard preparation observation expired; worker retained')
async def worker():
 token=secrets.token_hex(32);locks={n:asyncio.Lock()for n in('linux','android')};port=None
 @web.middleware
 async def local(request,handler):
  if request.host not in{f'127.0.0.1:{port}',f'localhost:{port}'}:raise web.HTTPForbidden(text='Local host required')
  if request.method=='POST':
   if request.headers.get('Origin')not in(None,f'http://127.0.0.1:{port}',f'http://localhost:{port}')or request.headers.get('X-Console-Token')!=token:raise web.HTTPForbidden(text='Local session required')
  response=await handler(request);response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer';return response
 async def index(request):
  cur=json.loads((ROOT/'var/hvf/current.json').read_bytes());s=Path(cur['session']);j=json.loads((s/'session.json').read_bytes());center=j['viewer_url'];assert center.startswith('http://127.0.0.1:');return web.Response(text=PAGE.replace('__CENTER__',center.replace('&','&amp;')).replace('__TOKEN__',token),content_type='text/html')
 async def status(request):
  result={}
  for name in locks:
   try:s,j,p=session(name);result[name]=dict(live=True,qemu_pid=j['qemu_pid'],session=str(s))
   except(Exception,)as error:result[name]=dict(live=False,error=str(error))
  result['android']['framework_boot_completed']=False;return web.json_response(result)
 async def command(request):
  try:
   body=await request.json();guest=body['guest'];cmd=body['command']
   if set(body)!={'guest','command'}or guest not in locks:raise ValueError('Expected guest and command')
   async with locks[guest]:result=await asyncio.to_thread(run,guest,cmd)
   with(ROOT/'var/hvf/dashboard-command-ledger.jsonl').open('a')as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
   return web.json_response(result)
  except(ValueError,KeyError,TypeError,json.JSONDecodeError)as e:return web.json_response(dict(error=str(e)),status=400)
  except Exception as e:return web.json_response(dict(error=str(e)),status=503)
 app=web.Application(middlewares=[local],client_max_size=16384);app.router.add_get('/',index);app.router.add_get('/api/status',status);app.router.add_post('/api/command',command);runner=web.AppRunner(app);await runner.setup();previous=descriptor();preferred=previous['port']if previous else 0;site=web.TCPSite(runner,'127.0.0.1',preferred);await site.start();port=site._server.sockets[0].getsockname()[1];STATE.write_text(json.dumps(dict(pid=os.getpid(),port=port,url=f'http://127.0.0.1:{port}/'),indent=2)+'\n');stop=asyncio.Event();loop=asyncio.get_running_loop()
 for sig in(signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
 await stop.wait();await runner.cleanup()
if __name__=='__main__':
 if '--worker'in sys.argv:asyncio.run(worker())
 else:print(ensure())
