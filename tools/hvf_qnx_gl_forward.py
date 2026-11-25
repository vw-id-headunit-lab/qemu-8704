from pathlib import Path
import sys,os,json,asyncio,subprocess,secrets,time,base64,shlex,hashlib
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'var/hvf/qnx-gl-forward.json'
from hvf_qnx_gl_channel import create
from hvf_console import execute
from hvf_guest_command import session

def ensure():
 channel=create()
 if STATE.exists():
  j=json.loads(STATE.read_text())
  if j['linux_qemu_pid']==channel['qemu_pid']:
   p=subprocess.run(['ps','-p',str(j['pid']),'-o','command='],capture_output=True,text=True)
   if p.returncode==0 and str(Path(__file__).resolve())in p.stdout:
    for _ in range(1200):
     j=json.loads(STATE.read_text())
     if j.get('relay_port'):return j
     if subprocess.run(['ps','-p',str(j['pid']),'-o','command='],capture_output=True).returncode:raise RuntimeError('Same GL forward worker ended during preparation')
     time.sleep(.1)
    raise TimeoutError('Same owned GL forward worker still preparing; retained')
 with (ROOT/'var/hvf/qnx-gl-forward.stdout').open('ab')as out,(ROOT/'var/hvf/qnx-gl-forward.stderr').open('ab')as err:
  child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker'],cwd=ROOT,stdout=out,stderr=err,start_new_session=True)
 for _ in range(1200):
  if STATE.exists():
   j=json.loads(STATE.read_text())
   if j.get('pid')==child.pid and j.get('relay_port'):return j
  if child.poll()is not None:raise RuntimeError('GL forward worker ended; inspect qnx-gl-forward.stderr')
  time.sleep(.1)
 raise TimeoutError('Same owned GL worker preparing; retained')

async def worker():
 channel=create();token=secrets.token_hex(16);directory=Path(channel['session'])/'qnx-gl-forward';directory.mkdir(exist_ok=True);state=dict(pid=os.getpid(),linux_qemu_pid=channel['qemu_pid'],session=channel['session'],channel_host_port=channel['host_port'],token=token,channels={});children={};tasks={}
 def save():
  tmp=STATE.with_name(STATE.name+'.'+str(os.getpid())+'.tmp');tmp.write_text(json.dumps(state,indent=2)+'\n');tmp.chmod(0o600);tmp.replace(STATE)
 reader,writer=await asyncio.open_connection('127.0.0.1',channel['host_port']);lock=asyncio.Lock();save()
 _,_,debug=session('linux');raw=(ROOT/'src/hvf_bench/guest/qnx-gl-relay.py').read_bytes();encoded=base64.b64encode(raw).decode();await asyncio.to_thread(execute,'rm -f /mnt/agl/tmp/qnx-gl-relay-ready.json; : >/mnt/agl/tmp/qnx-gl-relay.b64',5,port=debug)
 for off in range(0,len(encoded),2500):await asyncio.to_thread(execute,"printf '%s' '"+encoded[off:off+2500]+"' >>/mnt/agl/tmp/qnx-gl-relay.b64",5,port=debug)
 command="/bin/busybox base64 -d /mnt/agl/tmp/qnx-gl-relay.b64 >/mnt/agl/tmp/qnx-gl-relay.py; /bin/busybox chroot /mnt/agl /usr/bin/python3 /tmp/qnx-gl-relay.py "+token+" </dev/null >/mnt/agl/tmp/qnx-gl-relay.log 2>&1 3>&- &"
 await asyncio.to_thread(execute,command,5,port=debug)
 for _ in range(80):
  out=await asyncio.to_thread(execute,'cat /mnt/agl/tmp/qnx-gl-relay-ready.json 2>/dev/null',3,port=debug)
  try:j=json.JSONDecoder().raw_decode(out)[0]
  except ValueError:await asyncio.sleep(.1);continue
  assert j['token_sha256']==hashlib.sha256(token.encode()).hexdigest();state['relay_port']=j['port'];state['relay_pid']=j['pid'];state['guest_device']=j['device'];save();break
 else:raise RuntimeError('Guest GL relay not ready')
 async def send(kind,ident,data=b''):
  async with lock:writer.write(struct.pack('>BII',kind,ident,len(data))+data);await writer.drain()
 async def replies(ident,child):
  while True:
   data=await child.stdout.read(65536)
   if not data:break
   state['channels'][str(ident)]['reply_bytes']+=len(data);await send(2,ident,data)
  state['channels'][str(ident)]['exit']=await child.wait();save();await send(4,ident)
 import struct
 while True:
  kind,ident,n=struct.unpack('>BII',await reader.readexactly(9))
  if kind not in [1,2,4]or n>65536 or(kind==1 and n!=48)or(kind==4 and n):raise ValueError('QNX relay frame invalid')
  data=await reader.readexactly(n)if n else b''
  if kind==1:
   magic,pid,version,key=struct.unpack('<8sII32s',data);assert magic==b'QNXGLV1\0'and version==1 and key==token.encode()and pid>1
   folder=directory/('qnx-'+str(pid)+'-'+str(ident));folder.mkdir(exist_ok=False)
   env={k:v for k,v in os.environ.items()if not k.startswith(('MHI2_','MEB_QNX_GL_'))};env.update(DYLD_LIBRARY_PATH=str(ROOT/'.local/angle-sdk/lib'),MEB_QNX_GL_STRICT='original-gtf-v1',MEB_QNX_GL_ORIGINAL_PID=str(pid),MHI2_GL_FRAME=str(folder/'frame.ppm'),MHI2_GL_TRACE='1')
   with(folder/'stderr.txt').open('wb')as err:child=await asyncio.create_subprocess_exec(str(ROOT/'runtime/darwin/gl-host'),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=err,env=env)
   children[ident]=child;state['channels'][str(ident)]=dict(qnx_pid=pid,host_pid=child.pid,folder=str(folder),request_bytes=0,reply_bytes=0,source='original_qnx_gles_forward');tasks[ident]=asyncio.create_task(replies(ident,child));save()
  elif kind==2:
   child=children.get(ident)
   if child and child.returncode is None:child.stdin.write(data);await child.stdin.drain();state['channels'][str(ident)]['request_bytes']+=len(data)
  else:
   child=children.pop(ident,None)
   if child and child.returncode is None:child.stdin.close()
  save()

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='--worker':asyncio.run(worker())
 else:
  j=ensure();print(json.dumps({k:v for k,v in j.items()if k!='token'},indent=2))
