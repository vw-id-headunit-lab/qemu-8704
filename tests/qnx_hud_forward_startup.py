from pathlib import Path
import hashlib,importlib.util,json,struct,sys
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X19
sys.dont_write_bytecode=True
r=Path.cwd();root=Path(sys.argv[1]).resolve();label=sys.argv[2];sys.path.insert(0,str(r/'src/emu'))
record=dict(label=label,input='Original private IID/ARHUD forward image, declared virtual MCU HUD=1, native AArch64 constructor before asynchronous persistence',source=str(root/'src/emu/qnx_gl_forward.py'))
try:
 spec=importlib.util.spec_from_file_location('hud_forward_'+label,root/'src/emu/qnx_gl_forward.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 import qnx_hud_startup as h
 raw=(r/'var/inputs/analysis/cpu/qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn').read_bytes();assets={n:(r/'runtime/guest/qnx-gl'/n).read_bytes()for n in ['libMEG.so','libMEBGL2.so','libmeb-qnx-gl-forward.so']};config=b'55929\n'+b'0'*32+b'\n'
 image,metadata=m.forward_image(raw,assets,config)
 def member(data,name):
  rows,_,_=m.directory(data);row=next(x for x in rows if x['name']==name);o,n=struct.unpack_from('<II',data,row['pos']+24);return data[m.IMAGEFS+o:m.IMAGEFS+o+n]
 old=member(raw,h.MEMBER);new=member(image,h.MEMBER)
 u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.mem_map(0,0x200000);u.mem_write(0,new);u.mem_map(0x2000000,0x1000);u.reg_write(UC_ARM64_REG_X19,0x2000000);u.mem_write(0x20002bc,struct.pack('<I',99));u.emu_start(h.SITE,h.SITE+4,count=8);variant=struct.unpack('<I',u.mem_read(0x20002bc,4))[0]
 record['observed_constructor_variant']=variant;assert variant==1,'Forward image discarded declared ARHUD initializer: got '+str(variant)
 assert new==h.modified(old);assert new[0x76f10:0x77318]==old[0x76f10:0x77318];assert member(raw,m.SCRIPT)==member(image,m.SCRIPT)
 assert metadata['original_global_EGL_GLES_unchanged'];assert m.digest(raw)==m.IFS_SHA
 record.update(exit=0,private_ifs_sha256=m.digest(image),module_sha256=m.digest(new),native_persistence_callback_unchanged=True,original_ifs_unchanged=True,scope='Native initializer and composition only; actual HUD pixels require live original application acceptance')
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-hud-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
