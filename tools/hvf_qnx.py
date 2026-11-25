from pathlib import Path
import hashlib,json,os,shlex,socket,struct,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[1]
CURRENT=ROOT/'var/hvf/qnx-current.json'
RUNTIME=ROOT/'runtime/darwin/qemu-system-aarch64'
RUNNER=ROOT/'src/runner/trace-offline-live-darwin.py'
SHAPES={'ic':(800,400),'hud':(1120,780)}
DPU_SHA='54b40286c32a225bbbab83f218978847170257cc9645cfe509f3f268886821f4'

def process(pid,member):
 if not isinstance(pid,int)or pid<=1:raise ValueError('Invalid QNX process')
 os.kill(pid,0)
 result=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True,check=True)
 args=shlex.split(result.stdout)
 if str(member)not in args:raise ValueError('QNX process identity changed')
 return args

def descriptor():
 j=json.loads(CURRENT.read_bytes());args=process(j['runner_pid'],RUNNER)
 s=Path(j['session']).resolve(strict=True)
 if s.parent!=ROOT/'var/hvf'or args[args.index('--output')+1]!=str(s):raise ValueError('QNX session owner changed')
 return s,j

def completed_android_seed(previous,_checkpoint=True):
 s=Path(previous['session']).resolve(strict=True)
 if s.parent!=ROOT/'var/hvf':raise ValueError('Previous native seed outside carrier')
 args=previous['argv']
 if str(RUNNER)not in args or args[args.index('--output')+1]!=str(s):raise ValueError('Previous native seed owner differs')
 marker=s/'integrity.json'
 if not marker.exists():
  if not _checkpoint:raise ValueError('Recorded native seed has no completed integrity')
  recorded=previous.get('android_seed')
  if not recorded:return None
  if any((s/name).exists()for name in('launch.json','native-runtime-state.json','android-ufs-overlay.json')):
   raise ValueError('Incomplete native execution cannot reuse a preparation checkpoint')
  seed=Path(recorded).resolve(strict=True)
  if seed==s:raise ValueError('Native preparation seed cannot select itself')
  return completed_android_seed(dict(session=str(seed),argv=[str(RUNNER),'--output',str(seed)]),False)
 integrity=json.loads(marker.read_bytes());base=json.loads((s/'ufs-lun0-base-integrity.json').read_bytes());origin=json.loads((s/'android-ufs-overlay.json').read_bytes())
 if integrity.get('exit_code')!=0 or integrity.get('original_unchanged')is not True or base.get('unchanged_stat')is not True:
  raise ValueError('Previous native private data did not end cleanly')
 image=s/'ufs-vh2-lun0-overlay.qcow2'
 if origin.get('owner')!='VM4 / UFS VH2'or origin.get('base_read_only')is not True or origin.get('path')!=str(image)or not image.is_file():
  raise ValueError('Previous original Android private image differs')
 return s

def ensure():
 previous_seed=None
 if CURRENT.exists():
  j=json.loads(CURRENT.read_bytes())
  try:process(j['runner_pid'],RUNNER)
  except(ProcessLookupError,subprocess.CalledProcessError,ValueError):previous_seed=completed_android_seed(j)
  else:return j
 from macos_center import profile
 from hvf_qnx_gl_forward import ensure as forward
 forward()
 args,env=profile();env.pop('OEM_8704_QNX_HUD_STARTUP',None);env['MEB_QNX_GL_FORWARD']='original-gtf-v1';env.setdefault('MEB_QNX_NATIVE_OBSERVER','original-control-v1')
 env['OEM_8704_NATIVE_LIFETIME']='until-stop-v1'
 if previous_seed is not None:env['OEM_8704_OFFLINE_ANDROID_SEED']=str(previous_seed)
 with socket.socket() as reserved:
  reserved.bind(('127.0.0.1',0));network_port=reserved.getsockname()[1]
 env['MEB_QNX_NETWORK_PORT']=str(network_port)
 with socket.socket() as reserved:
  reserved.bind(('127.0.0.1',0));network_qmp_port=reserved.getsockname()[1]
 env['MEB_QNX_NETWORK_QMP_PORT']=str(network_qmp_port)
 with socket.socket() as reserved:
  reserved.bind(('127.0.0.1',0));mcu_uart_port=reserved.getsockname()[1]
 env['MEB_QNX_MCU_UART_PORT']=str(mcu_uart_port)
 s=ROOT/'var/hvf'/time.strftime('qnx-%Y%m%dT%H%M%SZ',time.gmtime())
 if s.exists():raise FileExistsError('QNX session timestamp collision')
 cmd=[sys.executable,'-B',str(RUNNER),'probe','--output',str(s),'--seconds','240','--qemu',str(RUNTIME),*args]
 with Path(str(s)+'.stdout').open('xb')as out,Path(str(s)+'.stderr').open('xb')as err:
  child=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=out,stderr=err,start_new_session=True)
 j=dict(runner_pid=child.pid,session=str(s),argv=cmd,android_seed=env.get('OEM_8704_OFFLINE_ANDROID_SEED'),firmware='8704',display_profile='iid-arhud',original_hud_adapter=True,network_port=network_port,network_qmp_port=network_qmp_port,network_backend='native-EQOS-Ethernet-socket',mcu_uart_port=mcu_uart_port)
 CURRENT.write_text(json.dumps(j,indent=2)+'\n');return j

def owner():
 s,j=descriptor();native=json.loads((s/'native-runtime-state.json').read_bytes())
 if native.get('running')is not True or not 0<=time.time()-native['observed_at_unix']<15:raise ValueError('QNX owner is not reporting live execution')
 args=process(native['qemu_pid'],RUNTIME)
 launch=json.loads((s/'launch.json').read_bytes())
 if not any(str(s)in word for word in args)or '-qmp'not in args or args[args.index('-qmp')+1]!=f'tcp:127.0.0.1:{launch["qmp_port"]},server=on,wait=off':raise ValueError('QNX runtime session changed')
 return s,native['qemu_pid']

def frame_info(role,owned=None):
 if role not in SHAPES:raise ValueError('Unknown QNX display role')
 s,pid=owned or owner()
 if (s/'qnx-gl-forward-private.json').is_file():
  from hvf_qnx_gl_frame import frame_info as forward_frame
  return forward_frame(role,s,pid)
 meta=json.loads((s/f'native-{role}-frame.json').read_bytes())
 if meta.get('status')!='active_qnx_source_verified'or meta.get('frame_source')!='original_vm2_dpum'or meta.get('dpu_sha256')!=DPU_SHA:raise ValueError('Original QNX plane has not produced verified pixels')
 if not 0<=time.monotonic()-meta['live_proof_monotonic']<15:raise ValueError('Original QNX plane observation expired')
 if (meta['width'],meta['height'])!=SHAPES[role]or not meta.get('scanned_frames')or not meta.get('source_writes')or not meta.get('nonzero'):raise ValueError('Original QNX scanout is incomplete')
 name=meta['png'];digest=meta['png_sha256']
 if name!=f'native-{role}-{digest}.png'or len(digest)!=64:raise ValueError('QNX image identity changed')
 p=s/name;data=p.read_bytes()
 if hashlib.sha256(data).hexdigest()!=digest or data[:8]!=b'\x89PNG\r\n\x1a\n'or struct.unpack('>II',data[16:24])!=SHAPES[role]:raise ValueError('QNX image hash or dimensions changed')
 return dict(path=str(p),qemu_pid=pid,session=str(s),sha256=digest,width=meta['width'],height=meta['height'],scanned_frames=meta['scanned_frames'],source_writes=meta['source_writes'],live_proof_monotonic=meta['live_proof_monotonic'])

def status():
 try:s,pid=owner()
 except Exception as e:return dict(live=False,error=str(e))
 result=dict(live=True,qemu_pid=pid,session=str(s),frames={})
 for role in SHAPES:
  try:result['frames'][role]=dict(available=True,**frame_info(role,(s,pid)))
  except Exception as e:result['frames'][role]=dict(available=False,error=str(e))
 return result

def stop():
 if not CURRENT.exists():return
 try:
  j=json.loads(CURRENT.read_bytes());args=process(j['runner_pid'],RUNNER)
  s=Path(j['session']).resolve()
  if s.parent!=ROOT/'var/hvf'or args[args.index('--output')+1]!=str(s):raise ValueError('QNX stop owner changed')
 except(ProcessLookupError,subprocess.CalledProcessError,FileNotFoundError):return
 s.mkdir(parents=True,exist_ok=True)
 (s/'native-stop.request').write_text('main-carrier-stop\n')

if __name__=='__main__':
 action=sys.argv[1]if len(sys.argv)>1 else'status'
 if action=='start':print(json.dumps(ensure(),indent=2))
 elif action=='stop':stop()
 elif action=='status':print(json.dumps(status(),indent=2))
 else:raise SystemExit(2)
