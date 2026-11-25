from pathlib import Path
import hashlib,json,os,stat,struct
from hvf_console import ROOT

HYP_SHA='49250f278f354865146c05355575312caa8dac24274f7d46e680aa48f1c889ff'
HYP=ROOT/'var/inputs/support/display-chain-8704/original-platform/hyp-binary/hv.img'
VMS=0x801955a8
RAM_BASE=0x40000000
RAM_BYTES=0xc0000000

def snapshot(session,qemu_pid,vmid):
 s=Path(session).resolve(strict=True)
 if s.parent!=ROOT/'var/hvf' or vmid not in (2,3,4):raise ValueError('Native domain owner differs')
 launch=json.loads((s/'launch.json').read_bytes());runtime=json.loads((s/'native-runtime-state.json').read_bytes())
 if launch['input_sha256']!=HYP_SHA or runtime['qemu_pid']!=qemu_pid:raise ValueError('Native domain epoch or HYP differs')
 original=HYP.read_bytes()
 if hashlib.sha256(original).hexdigest()!=HYP_SHA:raise ValueError('Original native HYP hash differs')
 fd=os.open(s/'mach-virt.ram.temporary-ram',os.O_RDONLY|os.O_NOFOLLOW)
 try:
  before=os.fstat(fd)
  if not stat.S_ISREG(before.st_mode)or before.st_size!=RAM_BYTES:raise ValueError('Native HYP RAM backing differs')
  def read(address,size):
   if not RAM_BASE<=address<=address+size<=RAM_BASE+RAM_BYTES:raise ValueError('Native domain pointer outside HYP RAM')
   value=os.pread(fd,size,address-RAM_BASE)
   if len(value)!=size:raise ValueError('Native HYP RAM read incomplete')
   return value
  if read(0x80080000,64)!=original[:64]or read(0x800915d0,208)!=original[0x115d0:0x116a0]:raise ValueError('Loaded native HYP identity differs')
  def state():
   pointer=struct.unpack('<Q',read(VMS+(vmid-1)*8,8))[0]
   if pointer&7:raise ValueError('Native domain descriptor is unaligned')
   identity=struct.unpack('<I',read(pointer,4))[0]
   flags=struct.unpack('<I',read(pointer+0x50,4))[0]
   if identity!=vmid:raise ValueError('Original domain ID differs')
   return pointer,flags
  first=state();second=state();after=os.fstat(fd)
  if first!=second or(before.st_dev,before.st_ino,before.st_size)!=(after.st_dev,after.st_ino,after.st_size):raise ValueError('Native domain changed during observation')
 finally:os.close(fd)
 return dict(session=str(s),qemu_pid=qemu_pid,vmid=vmid,pointer=hex(first[0]),flags=first[1],running=first[1]==2,source='original-vLM-domain-flags-readonly')

def require_running(session,qemu_pid,vmid):
 observed=snapshot(session,qemu_pid,vmid)
 if not observed['running']:raise RuntimeError('Original native VM'+str(vmid)+' is not RUN: vLM flags='+hex(observed['flags'])+'; no command sent')
 return observed
