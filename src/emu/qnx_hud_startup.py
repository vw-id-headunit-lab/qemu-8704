import hashlib,json,os,struct
from pathlib import Path

PROFILE='bench-arhud-v1'
PARENT_SHA='f7bc3f73b5048212d00da19fc5eab13a5049c6e220a141e06a6df1292a98d650'
MEMBER_OFFSET=27385856
IFS_ADDRESS=0x91080000
SITE=0x79754
PAD=0x42554
MEMBER='hmi/vwHMI/ICAS3-VW/IID_MODEL/resources/extensions/libModelAdapter.so'

def digest(raw):return hashlib.sha256(raw).hexdigest()

def file_digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb')as f:
  while b:=f.read(4<<20):h.update(b)
 return h.hexdigest()

def branch(source,target):
 delta=target-source
 if delta%4 or not -(1<<27)<=delta<(1<<27):raise ValueError('A64 branch range')
 return struct.pack('<I',0x14000000|((delta//4)&0x3ffffff))

def changes():
 return [(SITE,bytes.fromhex('7fbe02b9'),branch(SITE,PAD)),
         (PAD,bytes.fromhex('1f2003d5')*3,bytes.fromhex('3000805270be02b9')+branch(PAD+8,SITE+4))]

def modified(original):
 if digest(original)!=PARENT_SHA:raise ValueError('Original IID adapter identity changed')
 out=bytearray(original)
 for offset,before,after in changes():
  if len(before)!=len(after)or out[offset:offset+len(before)]!=before:raise ValueError('HUD initializer preimage changed')
  out[offset:offset+len(before)]=after
 return bytes(out)

def ips():
 return b'PATCH'+b''.join(off.to_bytes(3,'big')+len(after).to_bytes(2,'big')+after for off,_,after in changes())+b'EOF'

def stage(command,output,display_profile):
 if os.environ.get('MEB_QNX_GL_FORWARD'):
  from qnx_gl_forward import stage_forward
  return stage_forward(command,output,display_profile)
 if os.environ.get('MEB_QNX_GL_ABI_PROBE'):
  from qnx_gl_forward import stage_loader_probe
  return stage_loader_probe(command,output,display_profile)
 profile=os.environ.get('OEM_8704_QNX_HUD_STARTUP','')
 if not profile:return None
 import offline_bench_hmi
 if profile!=PROFILE or display_profile!='iid-arhud'or not offline_bench_hmi.selected():raise ValueError('HUD startup seed requires explicit private IID/ARHUD bench')
 from vlm_platform import factory_file
 from original_8704_vm import ROOT
 source=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn');raw=source.read_bytes()
 inventory=json.loads((ROOT/'var/inputs/analysis/qnx-ifs-20261021T125600Z/findings.json').read_bytes())
 if digest(raw)!=inventory['sha256']:raise ValueError('Original QNX IFS identity changed')
 rows=[r for r in inventory['entries']if r['name'].lstrip('/')==MEMBER and r['kind']=='reg']
 if len(rows)!=1 or rows[0]['absolute_offset']!=MEMBER_OFFSET:raise ValueError('Original IID adapter location changed')
 original=raw[MEMBER_OFFSET:MEMBER_OFFSET+rows[0]['file_size']];candidate=modified(original)
 exact=f'loader,file={source},addr={IFS_ADDRESS:#x},force-raw=on'
 if command.count(exact)!=1:raise ValueError('Require exact original QNX IFS RAM loader')
 output=Path(output).resolve(strict=True);private=output/'qnx-hud-ifs-private.bin'
 if not output.is_relative_to(ROOT/'var')or private.exists()or private.is_symlink():raise ValueError('Require fresh task-local QNX IFS copy')
 created=False
 try:
  with private.open('xb')as f:
   created=True;f.write(raw)
   for offset,before,after in changes():
    f.seek(MEMBER_OFFSET+offset);f.write(after)
  with private.open('rb')as f:f.seek(MEMBER_OFFSET);actual=f.read(len(original))
  if actual!=candidate:raise ValueError('Private QNX library readback differs')
 except BaseException:
  if created:private.unlink()
  raise
 command[command.index(exact)]=f'loader,file={private},addr={IFS_ADDRESS:#x},force-raw=on'
 applied=[]
 for offset,before,after in changes():
  applied.append(dict(file_offset=offset,physical=hex(IFS_ADDRESS+MEMBER_OFFSET+offset),before=before.hex(),after=after.hex()))
 record=dict(profile=profile,vmid=2,scope='Explicit private ARHUD fixture initializes variant1; original persistence callbacks remain active',original_ifs=str(source),original_ifs_sha256=digest(raw),private_ifs=str(private),private_ifs_sha256=file_digest(private),original_library_sha256=PARENT_SHA,private_library_sha256=digest(candidate),declared_hud_variant=1,original_library_bytes=len(original),changes=applied,original_bytes_unchanged=True)
 (Path(output)/'qnx-hud-startup-private.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
 return record
