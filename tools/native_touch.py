import hashlib,json,os,struct,threading,time
from hvf_qnx import ROOT,owner
from native_domain_state import require_running

_lock=threading.Lock()
_epoch=None
_pressed=False
OFFSET=0x7fc03000

def emit(events,epoch):
 global _epoch,_pressed
 if not isinstance(events,list)or not 1<=len(events)<=64:raise ValueError('Require 1..64 original touch events')
 kinds={'down':1,'move':2,'up':3}
 for item in events:
  if not isinstance(item,list)or len(item)!=3 or item[0]not in kinds or any(type(v)is not int for v in item[1:])or not 0<=item[1]<1920 or not 0<=item[2]<1080:raise ValueError('Original touch event differs')
 with _lock:
  s,pid=owner();require_running(s,pid,3);actual=str(s)+':'+str(pid)
  if epoch!=actual:raise ValueError('Original native input epoch differs')
  boot=json.loads((s/'gl-forward-boot.json').read_bytes());manifest=json.loads((ROOT/'runtime/guest/native-touch/manifest.json').read_bytes())
  if boot.get('native_touch')!=manifest or boot['profile']!='original-hmi-egl-v1':raise ValueError('Original native touch provider was not staged')
  for row in manifest['assets'].values():
   p=ROOT/row['path']
   if not p.is_relative_to(ROOT/'runtime/guest/native-touch')or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Pinned native input asset differs')
  path=s/'exynos-vm3-factory-ram-b0.temporary-ram';fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
  try:
   st=os.fstat(fd)
   if st.st_size!=0x80000000:raise ValueError('Original VM3 input RAM differs')
   header=os.pread(fd,64,OFFSET);magic,version,producer,consumer,delivered,error,relay_pid,pressed,*nonce=struct.unpack('<16I',header)
   if magic!=0x3154434d or version!=1 or error or relay_pid<=1 or not any(nonce[:4]):raise RuntimeError('Original native input relay is not ready')
   if (producer-consumer)&0xffffffff>128-len(events):raise RuntimeError('Original input ring is full')
   key=(actual,tuple(nonce[:4]));state=_pressed if _epoch==key else bool(pressed)
   if producer==consumer:state=bool(pressed)
   for action,x,y in events:
    if (action=='down')==state:raise ValueError('Original input gesture order differs')
    state=action!='up'
   position=producer
   for action,x,y in events:
    sequence=(position+1)&0xffffffff;data=struct.pack('<4I',kinds[action],x,y,sequence)
    if os.pwrite(fd,data,OFFSET+64+(position%128)*16)!=16:raise OSError('Short original input record write')
    position=sequence
   current_s,current_pid=owner();require_running(s,pid,3)
   if(current_s,current_pid)!=(s,pid)or os.pread(fd,8,OFFSET)!=header[:8]or os.pread(fd,16,OFFSET+32)!=header[32:48]:raise RuntimeError('Original input owner changed before publication')
   if os.pwrite(fd,struct.pack('<I',position),OFFSET+8)!=4:raise OSError('Short original input publication')
   _epoch=key;_pressed=state
   receipt=dict(session=str(s),qemu_pid=pid,epoch=actual,sequence=position,delivered_before=delivered,relay_pid=relay_pid,events=events,source='virtual-physical-ring-original-touch-app-RSI',queued_unix=time.time())
  finally:os.close(fd)
  with(s/'native-touch-events.jsonl').open('a')as f:f.write(json.dumps(receipt)+'\n')
  return receipt
