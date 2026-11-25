from pathlib import Path
import sys,os,json,secrets,subprocess,time,asyncio,signal,hashlib,struct
from aiohttp import web
from hvf_console import ROOT
from hvf_guest_command import session as render_session
from native_guest_command import run,session,close as close_native
from hvf_qnx import frame_info as qnx_frame_info,status as qnx_status,owner as qnx_owner
from native_hmi_frame import sample as native_hmi_sample,close as close_hmi_frame
from native_touch import emit as native_touch_emit
from native_android_frame import sample as native_android_sample
STATE=ROOT/'var/hvf/dashboard.json'
PAGE=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>8704 中控与双指令台</title><style>
*{box-sizing:border-box}body{margin:0;background:#0d1420;color:#e9f0fa;font:14px system-ui}header{padding:12px 18px;background:#192638;display:flex;flex-wrap:wrap;gap:12px;align-items:center}header strong{font-size:18px}#state{font-size:12px;color:#b9cce1}main{max-width:1800px;margin:auto;padding:12px;display:grid;gap:12px}.display{background:#111c2b;border:1px solid #30465f;border-radius:10px;overflow:hidden}h2{font-size:14px;margin:0;padding:10px 12px;background:#1b2a3e}.display iframe{border:0;width:100%;height:clamp(340px,calc(56.25vw + 100px),1080px);min-height:340px;display:block}.display img{display:block;width:100%;max-width:1080px;height:auto;margin:auto}.display img[hidden]{display:none}#android-frame-note{padding:12px;color:#b0c1d5}.qnx-displays{display:grid;grid-template-columns:1fr 1fr;gap:12px}.qnx-displays p{padding:12px;color:#b0c1d5}.qnx-displays img{width:100%;height:auto;display:block}.qnx-displays img[hidden]{display:none}.consoles{display:grid;grid-template-columns:1fr 1fr;gap:12px}section{background:#152235;border:1px solid #30465f;border-radius:8px;overflow:hidden}form{padding:12px;display:grid;gap:8px}label{font-weight:600}textarea{width:100%;min-height:72px;resize:vertical;background:#08111e;color:#deebfa;border:1px solid #4d6685;border-radius:4px;padding:9px;font:13px ui-monospace,monospace}button{justify-self:start;background:#2d88ba;color:white;border:0;border-radius:4px;padding:8px 15px;cursor:pointer}button:disabled{opacity:.5}pre{margin:0 12px 12px;min-height:130px;max-height:360px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#08111e;padding:10px;border-radius:4px;font:12px ui-monospace,monospace}.sub{font-size:12px;color:#b0c1d5;font-weight:400}@media(max-width:760px){.consoles{grid-template-columns:1fr}}
</style><header><strong>8704 原中控 · HVF</strong><span id="state">连接运行会话…</span></header><main><div class="display"><h2>Linux 中控 · 当前导航实例</h2><iframe title="原中控实时画面" src="__CENTER__"></iframe></div><div class="qnx-displays"><section><h2>IC 仪表 · 原 QNX 图形帧</h2><p id="ic-note">等待原 QNX IC 实时图形输出</p><img id="ic-frame" alt="原 QNX IC 当前图形帧" hidden></section><section><h2>HUD · 原 GTF HUD 流（局部）</h2><p id="hud-note">等待原 HUD 流有效像素</p><img id="hud-frame" alt="原 GTF HUD 当前局部画面" hidden></section></div><div class="display"><h2>Android 独立画面 · 原厂 VM4 / SurfaceFlinger</h2><p id="android-frame-note">等待 Android 独立图形帧（VM4）</p><img id="android-frame" alt="原 8704 Android 实时画面" hidden></div><div class="consoles"><section><h2>Linux 原生指令台 <span class="sub">原厂内核 · UID 65534</span></h2><form data-guest="linux"><label for="linux-command">Linux 命令</label><textarea id="linux-command" spellcheck="false">id && uname -r</textarea><button type="submit">执行 Linux</button></form><pre id="linux-output" role="log">等待命令</pre></section><section><h2>Android 11 原生指令台 <span class="sub">原厂内核 · UID 2000</span></h2><form data-guest="android"><label for="android-command">Android 命令</label><textarea id="android-command" spellcheck="false">id && uname -r</textarea><button type="submit">执行 Android</button></form><pre id="android-output" role="log">等待命令</pre></section></div></main><script>
const token='__TOKEN__';for(const form of document.querySelectorAll('form'))form.addEventListener('submit',async e=>{e.preventDefault();const guest=form.dataset.guest,button=form.querySelector('button'),out=document.getElementById(guest+'-output'),command=form.querySelector('textarea').value;button.disabled=true;out.textContent='正在执行…';try{const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify({guest,command})});const data=await r.json();if(!r.ok)throw Error(data.error);out.textContent='$ '+command+'\n'+data.stdout+'\n[exit='+data.exit+' · uid='+data.uid+' · QEMU '+data.qemu_pid+' · '+data.kernel+']'}catch(e){out.textContent='执行失败：'+e.message}finally{button.disabled=false}});
let qnxEpoch=null;const qnxViews=[];
async function status(){try{const r=await fetch('/api/status',{cache:'no-store'}),s=await r.json();const epoch=s.qnx.live?s.qnx.session+':'+s.qnx.qemu_pid:null;if(epoch!==qnxEpoch){qnxEpoch=epoch;for(const view of qnxViews)view.reset()}document.getElementById('state').textContent='中控 '+(s.center.live?'运行':'未运行')+' · Linux 原生指令台 '+(s.linux.live?'运行':'未运行')+' · Android 原生指令台 '+(s.android.live?'运行':'未运行')+' · 框架 '+(s.android.framework_services_ready?'已启动':'启动中')+' · QNX '+(s.qnx.live?'运行':'启动中')+' · 固件 8704'}catch(e){document.getElementById('state').textContent='连接异常：'+e.message}}status();setInterval(status,5000);
const nativeFrame=document.getElementById('android-frame'),frameNote=document.getElementById('android-frame-note');let androidFrameUrl=null,androidFrameBusy=false;function clearAndroidFrame(message){nativeFrame.hidden=true;nativeFrame.removeAttribute('src');if(androidFrameUrl)URL.revokeObjectURL(androidFrameUrl);androidFrameUrl=null;frameNote.hidden=false;frameNote.textContent=message}async function refreshFrame(){if(androidFrameBusy)return;androidFrameBusy=true;try{const r=await fetch('/api/android-frame.png?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('尚未收到 Android 独立画面');const epoch=r.headers.get('X-Android-Epoch'),source=r.headers.get('X-Android-Source');if(!epoch||epoch!==qnxEpoch||r.headers.get('X-Android-VMID')!=='4'||source!=='original_android_surfaceflinger_screencap')throw Error('Android 画面来源不匹配');const blob=await r.blob();if(epoch!==qnxEpoch)throw Error('Android 会话已改变');const next=URL.createObjectURL(blob),old=androidFrameUrl;androidFrameUrl=next;nativeFrame.src=next;nativeFrame.hidden=false;frameNote.hidden=false;frameNote.textContent='原生 Android VM4 · SurfaceFlinger · 帧龄 '+Math.floor(Number(r.headers.get('X-Android-Frame-Age')))+' 秒';if(old)URL.revokeObjectURL(old)}catch(e){clearAndroidFrame(e.message+'；不使用 Linux 画面代替')}finally{androidFrameBusy=false}}refreshFrame();setInterval(refreshFrame,2000);
function nativeFrameView(role){const image=document.getElementById(role+'-frame'),note=document.getElementById(role+'-note');let url=null,captured=0,busy=false;const reset=()=>{if(url)URL.revokeObjectURL(url);url=null;captured=0;image.removeAttribute('src');image.hidden=true;note.hidden=false;note.textContent='等待原厂 '+role.toUpperCase()+' 图形帧'};const refresh=async()=>{if(busy)return;busy=true;try{const response=await fetch('/api/qnx-frame/'+role+'.png?t='+Date.now(),{cache:'no-store'});if(!response.ok)return;const epoch=response.headers.get('X-QNX-Epoch'),age=Number(response.headers.get('X-QNX-Frame-Age'));if(!epoch||epoch!==qnxEpoch||!Number.isFinite(age)||age<0)return;const data=await response.blob();if(epoch!==qnxEpoch)return;const next=URL.createObjectURL(data),previous=url;image.src=next;image.hidden=false;url=next;captured=Date.now()-age*1000;if(previous)URL.revokeObjectURL(previous)}catch(error){}finally{busy=false;if(captured){note.hidden=false;note.textContent='最近原厂帧 · '+Math.max(0,Math.floor((Date.now()-captured)/1000))+' 秒前'}}};return{reset,refresh}}
for(const role of ['ic','hud']){const view=nativeFrameView(role);qnxViews.push(view);view.refresh();setInterval(view.refresh,2000)}
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
def recorded_frame_info(role):
 if role not in ('ic','hud'):raise ValueError('Unknown original display role')
 s,pid=qnx_owner();info=json.loads((s/('native-forward-'+role+'.json')).read_bytes())
 shape=(800,400)if role=='ic'else(1140,114)
 source='original_qnx_gles_forward'if role=='ic'else'original_qnx_gl_pbo_readback'
 p=s/('native-forward-'+role+'.png');data=p.read_bytes()
 if info['session']!=str(s)or info['qemu_pid']!=pid or info['path']!=str(p)or info['frame_source']!=source or(info['width'],info['height'])!=shape:raise ValueError('Recorded frame owner or role differs')
 if data[:8]!=b'\x89PNG\r\n\x1a\n'or struct.unpack('>II',data[16:24])!=shape or hashlib.sha256(data).hexdigest()!=info['sha256']:raise ValueError('Recorded native pixels differ')
 private=json.loads((s/'qnx-gl-forward-private.json').read_bytes());state=json.loads((ROOT/'var/hvf/qnx-gl-forward.json').read_bytes());ident=info['channel_id'];channel=state['channels'].get(str(ident))
 if private['profile']!='original-gtf-v1'or state['pid']!=private['relay_owner']or state['linux_qemu_pid']!=private['linux_qemu_pid']or ident<=private['channel_floor']or not channel or channel['qnx_pid']!=info['original_pid']or channel['host_pid']!=info['host_pid']:raise ValueError('Recorded native channel epoch differs')
 age=time.time()-info['observed_unix']+info['frame_age_seconds']
 if age<0:raise ValueError('Recorded frame timestamp differs')
 return dict(info,frame_age_seconds=age,recorded=True)

async def maintain_native_frames(stop):
 while not stop.is_set():
  try:await asyncio.to_thread(qnx_status)
  except Exception:pass
  try:await asyncio.wait_for(stop.wait(),2)
  except TimeoutError:pass

async def worker():
 token=secrets.token_hex(32);locks={n:asyncio.Lock()for n in('linux','android')};port=None;native_probe=None;native_probe_epoch=None;native_probe_started=0
 @web.middleware
 async def local(request,handler):
  if request.host not in{f'127.0.0.1:{port}',f'localhost:{port}'}:raise web.HTTPForbidden(text='Local host required')
  if request.method=='POST':
   if request.headers.get('Origin')not in(None,f'http://127.0.0.1:{port}',f'http://localhost:{port}')or request.headers.get('X-Console-Token')!=token:raise web.HTTPForbidden(text='Local session required')
  response=await handler(request);response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer';return response
 async def index(request):
  cur=json.loads((ROOT/'var/hvf/current.json').read_bytes());s=Path(cur['session']);j=json.loads((s/'session.json').read_bytes());center=j['viewer_url'];assert center.startswith('http://127.0.0.1:');return web.Response(text=PAGE.replace('__CENTER__',center.replace('&','&amp;')).replace('__TOKEN__',token),content_type='text/html')
 async def native_services():
  async with locks['android']:observed=await asyncio.to_thread(run,'android','service check activity; service check window; service check package',observe_framework=True)
  return time.monotonic(),observed
 async def status(request):
  nonlocal native_probe,native_probe_epoch,native_probe_started
  result={}
  try:s,j,p=render_session('linux');result['center']=dict(live=True,qemu_pid=j['qemu_pid'],session=str(s))
  except Exception as error:result['center']=dict(live=False,error=str(error))
  for name in locks:
   try:s,j,p=session(name);result[name]=dict(live=True,qemu_pid=j['qemu_pid'],session=str(s))
   except(Exception,)as error:result[name]=dict(live=False,error=str(error))
  result['android'].update(framework_boot_completed=None,framework_services_ready=False)
  if result['android']['live']:
   try:
    epoch=(result['android']['session'],result['android']['qemu_pid']);finished=native_probe_started
    if native_probe is not None and native_probe.done():
     try:finished=native_probe.result()[0]
     except Exception:pass
    if native_probe is None or native_probe_epoch!=epoch or(native_probe.done()and time.monotonic()-finished>=15):
     native_probe=asyncio.create_task(native_services());native_probe_epoch=epoch;native_probe_started=time.monotonic()
    if not native_probe.done():result['android']['framework_pending']=True
    else:
     observed_at,observed=native_probe.result()
     if(observed['session'],observed['qemu_pid'])!=epoch:raise ValueError('Original native command owner changed')
     result['android']['framework_observed_monotonic']=observed_at
     result['android']['framework_boot_completed']=observed['framework_boot_completed']
     result['android']['framework_services_ready']=observed['framework_boot_completed'] is True and all('Service '+name+': found'in observed['stdout']for name in ['activity','window','package'])
   except Exception as error:result['android']['framework_error']=str(error)
  result['qnx']=await asyncio.to_thread(qnx_status)
  return web.json_response(result)
 async def qnx_frame(request):
  try:info=await asyncio.to_thread(qnx_frame_info,request.match_info['role'])
  except Exception:
   try:info=await asyncio.to_thread(recorded_frame_info,request.match_info['role'])
   except Exception as error:raise web.HTTPServiceUnavailable(text=str(error))
  return web.FileResponse(info['path'],headers={'X-QNX-Epoch':info['session']+':'+str(info['qemu_pid']),'X-QNX-Frame-Age':str(info['frame_age_seconds']),'X-QNX-Frame-State':'recorded'if info.get('recorded')else'fresh'})
 async def native_linux_frame(request):
  try:png,proof=await asyncio.to_thread(native_hmi_sample)
  except Exception as error:raise web.HTTPServiceUnavailable(text=str(error))
  return web.Response(body=png,content_type='image/png',headers={'X-Native-Epoch':proof['session']+':'+str(proof['qemu_pid']),'X-Native-Guest-Pid':str(proof['guest_pid']),'X-Native-Frame-Age':str(proof['frame_age_seconds']),'X-Native-Source':proof['frame_source']})
 async def native_touch(request):
  try:
   body=await request.json()
   if set(body)!={'events','epoch'}:raise ValueError('Expected native touch events and epoch')
   result=await asyncio.to_thread(native_touch_emit,body['events'],body['epoch'])
   return web.json_response(result)
  except(ValueError,KeyError,TypeError,json.JSONDecodeError)as error:return web.json_response(dict(error=str(error)),status=400)
  except Exception as error:return web.json_response(dict(error=str(error)),status=503)
 async def android_frame(request):
  try:png,proof=await asyncio.to_thread(native_android_sample)
  except Exception as error:raise web.HTTPServiceUnavailable(text='Android VM4 pixels unavailable: '+str(error))
  return web.Response(body=png,content_type='image/png',headers={'X-Android-Epoch':proof['session']+':'+str(proof['qemu_pid']),'X-Android-VMID':'4','X-Android-Source':proof['frame_source'],'X-Android-Frame-Age':str(proof['frame_age_seconds'])})
 async def command(request):
  try:
   body=await request.json();guest=body['guest'];cmd=body['command']
   if set(body)!={'guest','command'}or guest not in locks:raise ValueError('Expected guest and command')
   async with locks[guest]:result=await asyncio.to_thread(run,guest,cmd)
   with(ROOT/'var/hvf/dashboard-command-ledger.jsonl').open('a')as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
   return web.json_response(result)
  except(ValueError,KeyError,TypeError,json.JSONDecodeError)as e:return web.json_response(dict(error=str(e)),status=400)
  except Exception as e:return web.json_response(dict(error=str(e)),status=503)
 app=web.Application(middlewares=[local],client_max_size=16384);app.router.add_get('/',index);app.router.add_get('/api/status',status);app.router.add_get('/api/native-linux-frame.png',native_linux_frame);app.router.add_get('/api/android-frame.png',android_frame);app.router.add_get('/api/qnx-frame/{role}.png',qnx_frame);app.router.add_post('/api/command',command);app.router.add_post('/api/native-touch',native_touch);runner=web.AppRunner(app);await runner.setup();previous=descriptor();preferred=previous['port']if previous else 0;site=web.TCPSite(runner,'127.0.0.1',preferred);await site.start();port=site._server.sockets[0].getsockname()[1];STATE.write_text(json.dumps(dict(pid=os.getpid(),port=port,url=f'http://127.0.0.1:{port}/'),indent=2)+'\n');stop=asyncio.Event();loop=asyncio.get_running_loop()
 for sig in(signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
 frames=asyncio.create_task(maintain_native_frames(stop))
 try:await stop.wait()
 finally:
  stop.set();await frames;await runner.cleanup();close_native();close_hmi_frame()
if __name__=='__main__':
 if '--worker'in sys.argv:asyncio.run(worker())
 else:print(ensure())
