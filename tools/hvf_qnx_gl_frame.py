from pathlib import Path
import hashlib,io,json,os,shlex,subprocess,threading,time,mmap
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
LOCK=threading.Lock()

def validate_metadata(meta,channel,age):
 if meta.get('source')!='original_qnx_gles_forward' or meta.get('pid')!=channel['qnx_pid']:raise ValueError('Original QNX producer differs')
 if not 0<=age<15:raise ValueError('Original QNX frame expired')
 if (meta.get('width'),meta.get('height'),meta.get('window_id'))!=(800,400,3) or not meta.get('native_window') or not meta.get('tid') or not meta.get('swap_count'):raise ValueError('Original IC window differs')

def validate_hud_metadata(meta,channel,age):
 if meta.get('source')!='original_qnx_gl_pbo_readback' or meta.get('pid')!=channel['qnx_pid']:raise ValueError('Original HUD readback producer differs')
 if not 0<=age<15:raise ValueError('Original HUD readback expired')
 if (meta.get('width'),meta.get('height'))!=(1140,114)or not all(meta.get(k)for k in ['tid','context_id','pixel_pack_buffer','sequence','nonzero_rgb_pixels','color_variation']):raise ValueError('Original HUD readback geometry or pixels differ')

POST_PROOFS=set()
def verify_hud_stream(s,pid,record):
 if not any(x.get('member','').endswith('/libGtfRuntime.so')and x.get('modified_sha256')=='5154583d3efd95c4f95b034ef9f46cda32b1eef01da69659fd3655235d5c6afa'for x in record['changed']):raise ValueError('Original HUD Screen readback route differs')
 key=(str(s),pid)
 if key not in POST_PROOFS:
  with(s/'tcg.log').open('rb')as f:
   with mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ)as data:
    if data.find(b'original HUD producer: stage=gtf-stream-post-return result=0 client=gtf')<0:raise ValueError('Original HUD stream has not posted')
  POST_PROOFS.add(key)

def process(pid,path):
 os.kill(pid,0);p=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True,check=True)
 if str(path)not in shlex.split(p.stdout):raise ValueError('Forward process owner differs')

def frame_info(role,owned_session,qemu_pid):
 if role not in ('ic','hud'):raise ValueError('Unknown original forwarded role')
 s=Path(owned_session).resolve();current=json.loads((ROOT/'var/hvf/qnx-current.json').read_bytes())
 if s.parent!=ROOT/'var/hvf' or current['session']!=str(s):raise ValueError('Original QNX epoch differs')
 process(current['runner_pid'],ROOT/'src/runner/trace-offline-live-darwin.py');process(qemu_pid,ROOT/'runtime/darwin/qemu-system-aarch64')
 record=json.loads((s/'qnx-gl-forward-private.json').read_bytes());state=json.loads((ROOT/'var/hvf/qnx-gl-forward.json').read_bytes());floor=record['channel_floor']
 if record['profile']!='original-gtf-v1' or state['pid']!=record['relay_owner'] or state['linux_qemu_pid']!=record['linux_qemu_pid']:raise ValueError('Original forward epoch owner differs')
 process(state['pid'],ROOT/'tools/hvf_qnx_gl_forward.py');process(state['linux_qemu_pid'],ROOT/'runtime/darwin/qemu-system-aarch64')
 host=ROOT/'runtime/darwin/gl-host'
 if hashlib.sha256(host.read_bytes()).hexdigest()!=record['host_sha256']:raise ValueError('Original native host hash differs')
 for name,digest in record['assets'].items():
  if hashlib.sha256((ROOT/'runtime/guest/qnx-gl'/name).read_bytes()).hexdigest()!=digest:raise ValueError('Original native client hash differs')
 linux_session=Path(state['session']).resolve();candidates=[]
 for key,channel in state['channels'].items():
  if int(key)<=floor or 'exit'in channel:continue
  folder=Path(channel['folder']).resolve()
  if folder!=linux_session/'qnx-gl-forward'/('qnx-'+str(channel['qnx_pid'])+'-'+key):continue
  for path in folder.glob('*.ppm.json'):
   candidates.append((path.stat().st_mtime_ns,int(key),channel,path))
 with LOCK:
  failures=[]
  for _,ident,channel,path in sorted(candidates,reverse=True):
   try:
    process(channel['host_pid'],host);before=path.stat();meta=json.loads(path.read_bytes());frame_age=time.time()-before.st_mtime;age=frame_age;heartbeat=path.parent/'frame.ppm.heartbeat.json'
    live=None
    if role=='ic' and heartbeat.exists():
     live=json.loads(heartbeat.read_bytes());age=time.time()-heartbeat.stat().st_mtime
     if live.get('source')!='original_qnx_client_liveness' or live.get('pid')!=channel['qnx_pid'] or not live.get('tid') or not live.get('sequence'):raise ValueError('Original native process liveness differs')
    if role=='hud':validate_hud_metadata(meta,channel,age);verify_hud_stream(s,qemu_pid,record)
    else:validate_metadata(meta,channel,age)
    if path.stat().st_birthtime<(s/'qnx-gl-forward-private.bin').stat().st_mtime:raise ValueError('Forwarded window predates current epoch or native host')
    image=path.with_suffix('');first=image.stat();raw=image.read_bytes();last=image.stat();after=path.stat()
    if (first.st_ino,first.st_mtime_ns,first.st_size)!=(last.st_ino,last.st_mtime_ns,last.st_size) or before.st_mtime_ns!=after.st_mtime_ns or last.st_mtime_ns>before.st_mtime_ns:raise ValueError('Original frame changed during read')
    im=Image.open(io.BytesIO(raw));im.load()
    if im.format!='PPM' or im.size!=((800,400)if role=='ic'else(1140,114)):raise ValueError('Original display image dimensions differ')
    output=io.BytesIO();im.save(output,format='PNG');data=output.getvalue();target=s/('native-forward-'+role+'.png');temp=target.with_suffix('.tmp');temp.write_bytes(data);temp.replace(target);digest=hashlib.sha256(data).hexdigest()
    info=dict(path=str(target),qemu_pid=qemu_pid,session=str(s),sha256=digest,width=im.width,height=im.height,frame_source=meta['source'],channel_id=ident,host_pid=channel['host_pid'],original_pid=meta['pid'],native_window=meta.get('native_window'),swap_count=meta.get('swap_count'),readback_sequence=meta.get('sequence'),original_context=meta.get('context_id'),pixel_pack_buffer=meta.get('pixel_pack_buffer'),observed_unix=time.time(),live_proof_monotonic=time.monotonic(),frame_age_seconds=frame_age,native_process_liveness_sequence=live['sequence']if live else None)
    (s/('native-forward-'+role+'.json')).write_text(json.dumps(info,indent=2)+'\n');return info
   except(OSError,ValueError,KeyError,subprocess.CalledProcessError)as error:failures.append(str(error))
 raise ValueError('No fresh current original '+role+' frame: '+('; '.join(failures)if failures else'waiting for current native window'))
