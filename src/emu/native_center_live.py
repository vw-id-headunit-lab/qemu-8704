import ctypes as c
import ctypes.wintypes as w
import hashlib
import json
import runpy
import struct
import time
from functools import lru_cache
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'var/sessions'
CARRIER_SHA='750f02ed9dc302de066e7893a4bf125318bcdb10569edd89e745418fa1de99c0'

class NativeCenterReader:
 def __init__(self,session):
  from vlm_platform import factory_file
  self.session=Path(session).resolve(strict=True)
  if self.session.parent!=BASE or (self.session/'integrity.json').exists():raise ValueError('Require current owned native session')
  rt=json.loads((self.session/'native-runtime-state.json').read_bytes());self.pid=rt['qemu_pid']
  if not rt['running']:raise ValueError('Original board is not running')
  self.k=c.WinDLL('kernel32',use_last_error=True);self.ps=c.WinDLL('psapi',use_last_error=True);k=self.k
  k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE;k.CloseHandle.argtypes=[w.HANDLE]
  self.handle=k.OpenProcess(0x410,False,self.pid)
  if not self.handle:raise c.WinError(c.get_last_error())
  try:
   k.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)];name=c.create_unicode_buffer(32768);n=w.DWORD(len(name))
   if not k.QueryFullProcessImageNameW(self.handle,0,name,c.byref(n)):raise c.WinError(c.get_last_error())
   carrier=Path(name.value).resolve(strict=True)
   expected=ROOT/'runtime/qemu-system-aarch64.exe';expected_sha=CARRIER_SHA
   if carrier!=expected:
    selection=json.loads((self.session/'gl-forward-carrier.json').read_bytes())
    profile=json.loads((self.session/'gl-forward-boot.json').read_bytes())
    boot_profile=selection.get('profile')
    pin=json.loads((ROOT/'src/emu/gl-forward'/('hmi-runtime-inputs.json'if boot_profile=='original-hmi-egl-v1'else'runtime-inputs.json')).read_bytes())['assets']['carrier']
    candidate=(ROOT/pin['path']).resolve(strict=True)
    if (boot_profile not in ('original-linux-egl-v1','original-hmi-egl-v1') or profile.get('profile')!=boot_profile or
        Path(selection['parent']).resolve()!=expected or selection['parent_sha256']!=CARRIER_SHA or
        Path(selection['selected']).resolve()!=candidate or selection['selected_sha256']!=pin['sha256']):raise ValueError('Different native carrier')
    expected=candidate;expected_sha=pin['sha256']
   if carrier!=expected or hashlib.sha256(carrier.read_bytes()).hexdigest()!=expected_sha:raise ValueError('Different native carrier')
   class Region(c.Structure):_fields_=[('BaseAddress',c.c_void_p),('AllocationBase',c.c_void_p),('AllocationProtect',w.DWORD),('PartitionId',w.WORD),('RegionSize',c.c_size_t),('State',w.DWORD),('Protect',w.DWORD),('Type',w.DWORD)]
   k.VirtualQueryEx.argtypes=[w.HANDLE,c.c_void_p,c.POINTER(Region),c.c_size_t];k.VirtualQueryEx.restype=c.c_size_t;k.ReadProcessMemory.argtypes=[w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t)];self.ps.GetMappedFileNameW.argtypes=[w.HANDLE,c.c_void_p,w.LPWSTR,w.DWORD]
   self.maps=[];seen=set();a=0
   while a<0x7fffffffffff:
    m=Region()
    if not k.VirtualQueryEx(self.handle,a,c.byref(m),c.sizeof(m)):break
    base=m.BaseAddress or 0
    if m.Type==0x40000 and m.AllocationBase not in seen:
     seen.add(m.AllocationBase);p=c.create_unicode_buffer(2048)
     if self.ps.GetMappedFileNameW(self.handle,base,p,len(p)) and self.session.name in p.value:
      start={'mach-virt.ram.temporary-ram':0x40000000,'exynos-vm3-factory-ram.temporary-ram':0xa80000000,'exynos-vm3-factory-ram-b0.temporary-ram':0xb00000000}.get(p.value.rsplit('\\',1)[-1])
      if start is not None:self.maps.append((start,start+m.RegionSize,m.AllocationBase))
    a=base+m.RegionSize
   if len(self.maps)!=3:raise ValueError('Owned RAM mappings changed')
   self.scan=runpy.run_path(str(BASE/'native-linux-scanout.py'));self.symbols=json.loads((ROOT/'var/inputs/symbols/linux-symbols.json').read_bytes());self.load=next(x for x in json.loads((self.session/'kernel-loads.json').read_bytes())if x['guest']=='linux')
   if self.load['kernel_sha256']!=self.scan['LINUX_SHA'] or self.symbols['sha256']!=self.scan['LINUX_SHA']:raise ValueError('Different original kernel')
   self.addr={x['name']:int(x['address'],16)for x in self.symbols['symbols']};self.ttbr=self.load['physical']+self.addr['swapper_pg_dir']-self.addr['_text'];self.tcr=int(self.symbols['observed_tcr_el1'],16)
   with factory_file('ivi/Image').open('rb')as f:
    
    expected=f.read(4096);actual=self.physical(self.load['physical'],4096)
    if actual[:2048]!=expected[:2048]:raise ValueError('Original runtime header differs')
    for i in range(2048,4096,4):
     original=int.from_bytes(expected[i:i+4],'little');runtime=int.from_bytes(actual[i:i+4],'little')
     if original!=runtime and not (runtime==0xd503201f and original&0xfc000000==0x14000000):raise ValueError('Unexpected original runtime prefix change')
   self.verified_image=self.scan['VerifiedFactoryImage'](self.symbols);self.last_raw=None;self.last_image=None
  except BaseException:self.close();raise
 def close(self):
  if getattr(self,'handle',None):self.k.CloseHandle(self.handle);self.handle=None
 def physical(self,a,n):
  if not 0<n<=16<<20 or not any(lo<=a and a+n<=hi for lo,hi in self.scan['VM3_RAM'] if hasattr(self,'scan')):raise ValueError('Outside original VM3 RAM')
  for lo,hi,base in self.maps:
   if lo<=a and a+n<=hi:
    b=c.create_string_buffer(n);got=c.c_size_t()
    if not self.k.ReadProcessMemory(self.handle,base+a-lo,b,n,c.byref(got))or got.value!=n:raise c.WinError(c.get_last_error())
    return b.raw
  raise ValueError('Outside owned mapping')
 def proof(self):
  from vlm_platform import walk_guest_4k
  @lru_cache(maxsize=4096)
  def page(a):return self.physical(a,4096)
  @lru_cache(maxsize=8192)
  def word(a):return struct.unpack_from('<Q',page(a&~4095),a&4095)[0]
  def read(va):return word(int(walk_guest_4k(va,self.tcr,self.ttbr,word)['physical_target'],16))
  return self.scan['inspect'](self.symbols,read,self.session,sysmmu={},verified_image=self.verified_image)
 def sample(self,*,raw_pixels=False):
  start=time.monotonic();rt=json.loads((self.session/'native-runtime-state.json').read_bytes())
  if not rt['running']or rt['qemu_pid']!=self.pid or (self.session/'integrity.json').exists():raise ValueError('Original session ended')
  a=self.proof()
  if a['status']not in ('active_scanout_pages_verified','active_gem_pages_verified'):raise ValueError(a['status'])
  raw=self.scan['read_active_raster'](a,self.physical);again=self.scan['read_active_raster'](a,self.physical)
  if raw!=again:raise ValueError('Original framebuffer changed during copy')
  b=self.proof();keys=['status','framebuffer','framebuffer_id','physical_spans','iova','pitch','format','source_and_destination','original_wod_blend','crtc_active','planes_on_crtc']
  if any(a.get(k)!=b.get(k)for k in keys):raise ValueError('Original DRM state changed during copy')
  raw_sha=hashlib.sha256(raw).hexdigest()
  if raw_pixels:
   a.update(captured_monotonic=time.monotonic(),pixel_buffer_sha256=raw_sha,
    frame_source='original_vm3_drm_primary',
    scope='Owned original VM3 RAM; stable DRM before/after and double identical raster; no QMP pause',
    read_seconds=time.monotonic()-start)
   return raw,a
  if raw_sha!=self.last_raw:
   png,props=self.scan['render_active_raster'](a,raw);self.last_image=(png,props);self.last_raw=raw_sha
  png,props=self.last_image;a.update(props,captured_monotonic=time.monotonic(),frame_source='original_vm3_drm_primary',scope='Owned original VM3 RAM; stable DRM before/after and double identical raster; no QMP pause',read_seconds=time.monotonic()-start)
  return png,a


def gl_profile(session):
 path=Path(session)/'gl-forward-boot.json'
 return path.is_file() and json.loads(path.read_bytes()).get('profile')=='original-hmi-egl-v1'


def center_reader(session):
 if gl_profile(session):
  from gl_center_live import GLCenterReader
  return GLCenterReader(session)
 return NativeCenterReader(session)


class LiveCenterFeed:
 def __init__(self,session):
  import threading
  self.session=Path(session);self.current=None;self.error=None;self.samples=0;self.changes=0;self.closed=threading.Event();self.thread=threading.Thread(target=self.run,name='original-center-live',daemon=True);self.thread.start()
 def latest(self):
  sample=self.current
  return sample if sample is not None and 0<=time.monotonic()-sample[1]['captured_monotonic']<.75 else None
 def close(self):self.closed.set();self.thread.join(timeout=3)
 def run(self):
  reader=None;last=None;active_until=0;gate=None
  try:
   while not self.closed.is_set():
    started=time.monotonic()
    try:
     marker=self.session/'native-center-frame.json';stamp=marker.stat().st_mtime_ns if marker.exists()else 0
     if gate is not None and gate==stamp and not gl_profile(self.session):self.closed.wait(.2);continue
     if reader is None:reader=center_reader(self.session)
     sample=reader.sample();digest=sample[1]['png_sha256'];self.samples+=1
     if digest!=last:self.changes+=1;active_until=started+1;last=digest
     sample[1]['live_read_samples']=self.samples;sample[1]['live_unique_frames']=self.changes;self.current=sample;self.error=None;gate=None
     delay=1/30 if started<active_until else .2
    except (OSError,ValueError,KeyError,RuntimeError)as error:
     self.current=None;self.error=str(error);gate=stamp;delay=.2
    self.closed.wait(max(.005,delay-(time.monotonic()-started)))
  finally:
   self.current=None
   if reader is not None:reader.close()
