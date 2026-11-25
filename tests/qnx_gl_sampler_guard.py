from pathlib import Path
import sys,json,io,hashlib,importlib.util,struct
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE,UcError
from unicorn.arm64_const import UC_ARM64_REG_X20,UC_ARM64_REG_X21,UC_ARM64_REG_X19,UC_ARM64_REG_X29,UC_ARM64_REG_PC,UC_ARM64_REG_W0,UC_ARM64_REG_W1
sys.dont_write_bytecode=True
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];root=binary.parents[2];p=root/'src/emu/qnx_gl_forward.py';record={'label':label,'input':'Exact original AArch64 equal-null, equal-valid, changed-valid, changed-null sampler cases in actual generated private IFS','source':str(p),'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'cases':[]}
try:
 spec=importlib.util.spec_from_file_location('qnx_sampler_'+label,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 raw=(r/'var/inputs/analysis/cpu/qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn').read_bytes();assert m.digest(raw)==m.IFS_SHA
 assets={x.name:x.read_bytes()for x in(r/'runtime/guest/qnx-gl').glob('*.so')};image,meta=m.forward_image(raw,assets,b'55929\n'+b'0'*32+b'\n');entries,_,_=m.directory(image);row=next(x for x in entries if x['name'].endswith('/libGtfGuiOpenGLES3.so'));off,n=struct.unpack_from('<II',image,row['pos']+24);module=image[m.IMAGEFS+off:m.IMAGEFS+off+n];e=ELFFile(io.BytesIO(module));segs=list(e.iter_segments())
 for name,old,current,target in [('equal_null',0,0,0x1c038),('equal_valid',0x103000,0x103000,0x1c1b8),('changed_valid',0,0x103000,0x1bf54),('changed_null',0x103000,0,0x1c038)]:
  u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.mem_map(0,0x100000)
  for s in segs:
   if s['p_type']=='PT_LOAD':u.mem_write(s['p_vaddr'],s.data())
  u.mem_unmap(0,0x1000);u.mem_map(0x100000,0x10000)
  for reg,value in [(UC_ARM64_REG_X20,0x100000),(UC_ARM64_REG_X21,current),(UC_ARM64_REG_X19,0x101000),(UC_ARM64_REG_X29,0x102000)]:u.reg_write(reg,value)
  for address,value in [(0x100028,old),(0x101470,0x104000),(0x1047e0,0x90000)]:u.mem_write(address,struct.pack('<Q',value))
  u.mem_write(0x103028,struct.pack('<I',7));u.mem_write(0x10205c,struct.pack('<I',3));trace=[]
  def hook(vm,pc,size,data):
   trace.append(hex(pc))
   if pc==target:vm.emu_stop()
  u.hook_add(UC_HOOK_CODE,hook)
  try:
   u.emu_start(0x1bf34,0x1c1bc,count=40);pc=u.reg_read(UC_ARM64_REG_PC);result={'name':name,'old_sampler':old,'new_sampler':current,'pc':hex(pc),'expected':hex(target),'trace':trace,'exit':int(pc!=target)}
   if name.endswith('valid')and pc==target:result.update(unit=u.reg_read(UC_ARM64_REG_W0),sampler=u.reg_read(UC_ARM64_REG_W1));assert(result['unit'],result['sampler'])==(3,7)
  except UcError as ex:result={'name':name,'old_sampler':old,'new_sampler':current,'pc':hex(u.reg_read(UC_ARM64_REG_PC)),'exit':1,'stderr':str(ex),'trace':trace}
  record['cases'].append(result)
 assert all(x['exit']==0 for x in record['cases']),record['cases']
 assert meta['original_global_EGL_GLES_unchanged'] and meta['original_processed_elf_metadata_preserved'];record.update(exit=0,private_ifs_sha256=m.digest(image),original_ifs_sha256=m.digest(raw),original_ifs_unchanged=True,scope='Actual generated private IFS + real AArch64 branch execution; original runtime GUI acceptance separately required')
except Exception as ex:record.update(exit=1,error=repr(ex))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
