
import ctypes as c
import ctypes.wintypes as w
import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path
import time

from native_center_live_darwin import NativeCenterReader,ROOT
from vlm_platform import walk_guest_4k


class GLCenterReader:
 def __init__(self,session):
  self.session=Path(session).resolve(strict=True);self.base=None;self.host_handle=None
  self.stamp=None;self.pixels=None;self.image=None;self.png=None;self.digest=None
  self.scan={'original_wod_ignores_pixel_alpha':lambda proof:False}
  try:
   boot=json.loads((self.session/'gl-forward-boot.json').read_bytes())
   self.geometry=tuple(boot.get('panel_geometry')or(1560,700))
   if self.geometry!=(1920,1080):raise ValueError('Require actual 1920x1080 source, not a resized legacy frame')
   runtime=json.loads((self.session/'gl-forward-runtime.json').read_bytes())
   if not(self.session/'gl-forward-frame.ppm').is_file():raise ValueError('Waiting for original 1080p frame')
   if not runtime.get('guest_pid')and not(self.session/'gl-forward-original-peer.json').exists():raise ValueError('Waiting for authenticated original HMI owner')
   self.base=NativeCenterReader(self.session)
   self.address=lru_cache(maxsize=256)(lambda va:int(walk_guest_4k(va,self.base.tcr,self.base.ttbr,self.word)['physical_target'],16))
   pins=json.loads((ROOT/'src/emu/gl-forward/hmi-runtime-inputs-darwin.json').read_bytes())['assets']
   path=self.session/'gl-forward-original-peer.json'
   proof=json.loads(path.read_bytes())if path.exists()else self.discover(runtime,boot,pins)
   if (boot['profile']!='original-hmi-egl-v1' or Path(proof['session']).resolve()!=self.session or
       proof['qemu_pid']!=self.base.pid or proof['guest_uid']!=724 or
       proof['guest_exe']!='/vw/oem-gl-forward/jvm/bin/java' or proof['kernel_sha256']!=self.base.load['kernel_sha256']):
    raise ValueError('Original HMI provenance differs')
   for key in ['agent','library','jni']:
    if boot['payload_sha256'][key]!=pins[key]['sha256'] or proof[key+'_sha256']!=pins[key]['sha256']:
     raise ValueError('Staged original HMI graphics asset differs: '+key)
   if proof['carrier_sha256']!=pins['carrier']['sha256'] or proof['host_sha256']!=pins['host']['sha256']:
    raise ValueError('Original HMI producer identity differs')
   self.provenance=proof;self.host_pid=proof['host_pid'];self.task=int(proof['guest_task'],16)
   self.mm=int(proof['guest_mm'],16);self.pid_struct=int(proof['guest_pid_struct'],16)
   if not self.mm or not self.pid_struct:raise ValueError('Original HMI task identity absent')
   from macos_process import executable,alive
   host=(ROOT/pins['host']['path']).resolve(strict=True)
   if not alive(self.host_pid) or executable(self.host_pid)!=host or hashlib.sha256(host.read_bytes()).hexdigest()!=pins['host']['sha256']:
    raise ValueError('Live graphics host differs')
   @lru_cache(maxsize=256)
   def address(va):return int(walk_guest_4k(va,self.base.tcr,self.base.ttbr,self.word)['physical_target'],16)
   self.address=address;self.check_live()
   if not path.exists():path.write_text(json.dumps(proof,indent=2)+'\n')
  except BaseException:self.close();raise
 def discover(self,runtime,boot,pins):
  from collections import deque
  from linux_pid1_context import capture
  pid=runtime.get('guest_pid')
  if type(pid)is not int or pid<=0:raise ValueError('Authenticated stream owner absent')
  first=int(capture(self.base.symbols,self.read)['task'],16);pending=deque([first]);seen=set()
  while pending:
   task=pending.popleft()
   if task in seen or len(seen)>=1024:raise ValueError('Original task list changed')
   seen.add(task);pidptr=self.read(task+0x570)
   if pidptr and self.read(pidptr+0x58)==self.base.addr['init_pid_ns'] and self.read(pidptr+0x50)&0xffffffff==pid:
    mm=self.read(task+0x458)
    if not mm:raise ValueError('Original HMI owner has no live memory')
    return dict(session=str(self.session),qemu_pid=self.base.pid,host_pid=runtime['host_pid'],guest_pid=pid,guest_uid=724,
     guest_exe='/vw/oem-gl-forward/jvm/bin/java',guest_task=hex(task),guest_mm=hex(mm),guest_pid_struct=hex(pidptr),
     kernel_sha256=self.base.load['kernel_sha256'],carrier_sha256=pins['carrier']['sha256'],host_sha256=pins['host']['sha256'],
     agent_sha256=boot['payload_sha256']['agent'],library_sha256=boot['payload_sha256']['library'],jni_sha256=boot['payload_sha256']['jni'],
     uid_basis='Mandatory SO_PEERCRED and executable allowlist enforced by the exact pinned private agent')
   head=task+0x528;link=self.read(head);siblings=set()
   while link!=head:
    if link in siblings or len(siblings)>=512:raise ValueError('Original child list changed')
    siblings.add(link);pending.append(link-0x538);link=self.read(link)
  raise ValueError('Original HMI owner ended')
 def word(self,pa):return int.from_bytes(self.base.physical(pa,8),'little')
 def read(self,va):return self.word(self.address(va))
 def check_live(self):
  self.base.check_current_owner()
  state=json.loads((self.session/'native-runtime-state.json').read_bytes())
  if not state['running'] or state['qemu_pid']!=self.base.pid or (self.session/'integrity.json').exists():
   raise ValueError('Original session ended')
  from macos_process import alive
  if not alive(self.base.pid) or not alive(self.host_pid):raise ValueError('Original producer ended')
  peer=json.loads((self.session/'gl-forward-runtime.json').read_bytes())
  if peer['host_pid']!=self.host_pid or peer['host_exit'] is not None or peer['closed']:raise ValueError('Original graphics transport ended')
  if (self.read(self.task+0x458)!=self.mm or self.read(self.task+0x570)!=self.pid_struct or
      self.read(self.pid_struct+0x50)&0xffffffff!=self.provenance['guest_pid'] or
      self.read(self.pid_struct+0x58)!=self.base.addr['init_pid_ns']):raise ValueError('Original HMI process ended or changed')
 def sample(self,*,raw_pixels=False):
  from PIL import Image
  self.check_live();path=self.session/'gl-forward-frame.ppm';stat=path.stat();stamp=(stat.st_mtime_ns,stat.st_size,stat.st_ino)
  if stamp!=self.stamp:
   raw=path.read_bytes();after=path.stat()
   if stamp!=(after.st_mtime_ns,after.st_size,after.st_ino):raise ValueError('Original GL frame changed during copy')
   parts=raw.split(b'\n',3)
   if len(parts)!=4 or parts[:3]!=[b'P6',b'1920 1080',b'255'] or len(parts[3])!=1920*1080*3:
    raise ValueError('Original GL frame layout differs')
   self.image=Image.frombytes('RGB',self.geometry,parts[3]);self.pixels=self.image.tobytes('raw','BGRX')
   self.digest=hashlib.sha256(self.pixels).hexdigest();self.png=None;self.stamp=stamp
  proof=dict(width=1920,height=1080,pitch=7680,format='XR24',nonzero=bool(self.image.getbbox()),
   pixel_buffer_sha256=self.digest,captured_monotonic=time.monotonic(),frame_source='original_hmi_authenticated_ANGLE',
   qemu_pid=self.base.pid,host_pid=self.host_pid,guest_pid=self.provenance['guest_pid'],guest_uid=724,
   source_frame_mtime_ns=self.stamp[0],scope='Live original HMI task/mm/pid and producer identities checked; atomic host RGB frame, no PNG in raw path')
  if raw_pixels:return self.pixels,proof
  if self.png is None:
   stream=io.BytesIO();self.image.save(stream,format='PNG');self.png=stream.getvalue()
  proof['png_sha256']=hashlib.sha256(self.png).hexdigest();return self.png,proof
 def close(self):
  if self.base:self.base.close();self.base=None
