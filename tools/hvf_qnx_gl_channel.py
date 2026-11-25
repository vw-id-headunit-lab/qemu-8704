from pathlib import Path
import sys,json,socket,time,threading,shlex,secrets,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/emu'))
from original_8704_vm import QMP
from hvf_guest_command import session
from hvf_console import execute
LABEL='qnx-gl-bridge'
DEVICE='qnx-gl-port'
NAME='bench.qnx-gl'

def owned():
 s,j,debug=session('linux')
 for attempt in range(150):
  try:return s,j,debug,QMP(j['ports']['qmp_port'])
  except ConnectionRefusedError:
   if attempt==149:raise
   current,record,_=session('linux')
   if current!=s or record['qemu_pid']!=j['qemu_pid']or record['ports']['qmp_port']!=j['ports']['qmp_port']:raise ValueError('Linux owner changed while waiting for QMP')
   time.sleep(.1)

def inspect():
 s,j,_,q=owned()
 try:
  entries=[v for v in q.execute('query-chardev')if v['label']==LABEL]
  name=q.execute('qom-get',{'path':'/machine/peripheral/'+DEVICE,'property':'name'})if entries else None
  return dict(session=str(s),qemu_pid=j['qemu_pid'],label=LABEL,entries=entries,device_name=name)
 finally:q.close()

def create():
 s,j,_,q=owned();p=s/'qnx-gl-channel.json'
 try:
  present=[v for v in q.execute('query-chardev')if v['label']==LABEL]
  if present:
   if p.exists():r=json.loads(p.read_text())
   else:
    r=json.loads((ROOT/'.local/mac-port/transaction/qnx-gl-virtio-channel.json').read_text())
    if r['linux_qemu_pid']!=j['qemu_pid']or r['session']!=str(s):raise ValueError('Existing GL channel owner differs')
    r.update(qemu_pid=j['qemu_pid'],label=LABEL,device=DEVICE,name=NAME)
   if r['qemu_pid']!=j['qemu_pid']or str(r['host_port'])not in present[0]['filename']:raise ValueError('Existing GL channel endpoint differs')
   if q.execute('qom-get',{'path':'/machine/peripheral/'+DEVICE,'property':'name'})!=NAME:raise ValueError('Existing GL device name differs')
  else:
   with socket.socket()as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
   q.execute('chardev-add',{'id':LABEL,'backend':{'type':'socket','data':{'addr':{'type':'inet','data':{'host':'127.0.0.1','port':str(port)}},'server':True,'wait':False}}})
   try:q.execute('device_add',{'driver':'virtserialport','id':DEVICE,'chardev':LABEL,'name':NAME})
   except Exception:q.execute('chardev-remove',{'id':LABEL});raise
   r=dict(session=str(s),qemu_pid=j['qemu_pid'],host_port=port,label=LABEL,device=DEVICE,name=NAME)
  r={k:r[k]for k in ['session','qemu_pid','host_port','label','device','name']}
  p.write_text(json.dumps(r,indent=2)+'\n');return r
 finally:q.close()

def remove():
 s,j,_,q=owned();p=s/'qnx-gl-channel.json'
 try:
  if not p.exists():raise ValueError('GL channel ownership record missing')
  r=json.loads(p.read_text())
  if r['session']!=str(s)or r['qemu_pid']!=j['qemu_pid']:raise ValueError('GL channel process changed')
  present=[v for v in q.execute('query-chardev')if v['label']==LABEL]
  if not present:return dict(removed=True,already_absent=True)
  if str(r['host_port'])not in present[0]['filename']:raise ValueError('GL channel endpoint changed')
  if q.execute('qom-get',{'path':'/machine/peripheral/'+DEVICE,'property':'name'})!=NAME:raise ValueError('GL device name changed')
  q.execute('device_del',{'id':DEVICE});last=None
  for _ in range(100):
   try:q.execute('chardev-remove',{'id':LABEL});break
   except Exception as e:last=e;time.sleep(.05)
  else:raise RuntimeError('Same device still detaching: '+str(last))
  return dict(removed=True,session=str(s),qemu_pid=j['qemu_pid'])
 finally:q.close()

def probe():
 r=create();s,j,debug=session('linux');nonce=secrets.token_hex(12)
 code="import glob,os,json,hashlib; p=[x for x in glob.glob('/sys/class/virtio-ports/*/name') if open(x).read().strip()=='bench.qnx-gl']; assert len(p)==1; d='/dev/'+p[0].split('/')[-2]; f=os.open(d,os.O_RDWR); msg=('QNX_GL_LINUX|pid='+str(os.getpid())+'|nonce="+nonce+"|end\\n').encode(); os.write(f,msg); reply=os.read(f,64); os.close(f); print(json.dumps(dict(device=d,received_bytes=len(reply),reply_sha256=hashlib.sha256(reply).hexdigest())))"
 command='chroot /mnt/agl /usr/bin/python3 -c '+shlex.quote(code);out={}
 with socket.create_connection(('127.0.0.1',r['host_port']),timeout=5)as sock:
  sock.settimeout(5)
  def guest():
   try:out['stdout']=execute(command,timeout=10,port=debug)
   except Exception as e:out['error']=repr(e)
  thread=threading.Thread(target=guest);thread.start();wire=bytearray()
  while not wire.endswith(b'\n'):
   data=sock.recv(256)
   if not data:raise ConnectionError('Owned GL serial stream closed')
   wire.extend(data)
  if nonce.encode()not in wire:raise ValueError('Unexpected GL serial producer')
  reply=secrets.token_bytes(32);sock.sendall(reply);thread.join(12)
  if thread.is_alive():raise TimeoutError('Same guest probe still running')
 if 'error'in out:raise RuntimeError(out['error'])
 if hashlib.sha256(reply).hexdigest()not in out['stdout']:raise ValueError('Guest did not receive exact fresh host bytes')
 result=dict(channel=r,input='fresh Linux PID/nonce line then unpredictable32 host bytes',host_received=wire.decode(),guest_command=command,guest_stdout=out['stdout'],host_reply_sha256=hashlib.sha256(reply).hexdigest(),verified=True)
 (Path(r['session'])/'qnx-gl-channel-probe.json').write_text(json.dumps(result,indent=2)+'\n');return result

if __name__=='__main__':
 action=sys.argv[1]if len(sys.argv)>1 else'status'
 print(json.dumps({'status':inspect,'create':create,'remove':remove,'probe':probe}[action](),indent=2))
