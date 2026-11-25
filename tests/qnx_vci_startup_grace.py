from pathlib import Path
import importlib.util,sys,json,struct,hashlib,io
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X29,UC_ARM64_REG_SP,UC_ARM64_REG_W1,UC_ARM64_REG_X2,UC_ARM64_REG_W3,UC_ARM64_REG_W4
root=Path(__file__).resolve().parents[1];source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else root/'src/emu/qnx_gl_forward.py';sys.path[:0]=[str(source.parent),str(root/'src/emu')];report=dict(source=str(source),scope='Execute exact original AArch64 initial timer tail and private IFS staging; original running watchdog bytes pinned separately')
try:
 spec=importlib.util.spec_from_file_location('vci_staged',source);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);path=root/'var/inputs/analysis/cpu/qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn';raw=path.read_bytes();before_sha=hashlib.sha256(raw).hexdigest();assets={n:(root/'runtime/guest/qnx-gl'/n).read_bytes()for n in ['libmeb-qnx-gl-forward.so','libMEG.so','libMEBGL2.so']};candidate,receipt=mod.forward_image(raw,assets,b'55395\n'+b'1'*32+b'\n',observe=True,control_only=True)
 def payload(image):
  rows,_,_=mod.directory(image);row=next(x for x in rows if x['name']=='middleware/MgrVCI');a,n=struct.unpack_from('<II',image,row['pos']+24);return image[mod.IMAGEFS+a:mod.IMAGEFS+a+n]
 original=payload(raw);modified=payload(candidate)
 def timer(blob):
  e=ELFFile(io.BytesIO(blob));u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.mem_map(0,0x70000)
  for s in e.iter_segments():
   if s['p_type']=='PT_LOAD':u.mem_write(s['p_vaddr'],s.data())
  u.mem_map(0x7000000,65536);u.reg_write(UC_ARM64_REG_X29,0x7008000);u.reg_write(UC_ARM64_REG_SP,0x7008000);calls=[]
  def hook(uc,pc,size,data):
   if pc==0xe3e0:calls.append(dict(timer_id=uc.reg_read(UC_ARM64_REG_W1),seconds=uc.reg_read(UC_ARM64_REG_X2),flags=[uc.reg_read(UC_ARM64_REG_W3),uc.reg_read(UC_ARM64_REG_W4)]));uc.emu_stop()
  u.hook_add(UC_HOOK_CODE,hook);u.emu_start(0x21138,0xe3e4,count=100);assert len(calls)==1;return calls[0]
 old=timer(original);new=timer(modified);expected=bytearray(original);expected[0x21148:0x2114c]=bytes.fromhex('024b80d2');report.update(original_timer=old,modified_timer=new,exact_single_instruction_change=modified==bytes(expected),original_input_unchanged=hashlib.sha256(path.read_bytes()).hexdigest()==before_sha,watchdog_start_unchanged=hashlib.sha256(modified[0x2d718:0x2d874]).hexdigest()=='616427d28d3f2e0b0f2e827497acb4b2917d94ba91673580cadb48ac95f93094',watchdog_check_unchanged=hashlib.sha256(modified[0x2d874:0x2d914]).hexdigest()=='04897a2b4cf7a39e6741d83d2e950c35ae6da66be0623fdda63dbc75bb44b2ac')
 assert old==dict(timer_id=100,seconds=15,flags=[0,0])and new==dict(timer_id=100,seconds=600,flags=[0,0]),'Finite initial wait not applied'
 assert all(report[k]for k in ['exact_single_instruction_change','original_input_unchanged','watchdog_start_unchanged','watchdog_check_unchanged']);assert any(x['member']=='middleware/MgrVCI'for x in receipt['changed']);report['exit']=0
except Exception as error:report.update(exit=2,error=repr(error))
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
