from pathlib import Path
import sys,json,hashlib
from elftools.elf.elffile import ELFFile
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];p=binary.parents[2]/'runtime/guest/qnx-gl/libmeb-qnx-gl-forward.so';record={'binary':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'label':label,'input':'Private core function-address/wrapper references must not preempt to native GLES; original Screen-side libEGL.so.1 remains real dependency; native execution separately required'}
try:
 with p.open('rb')as f:
  e=ELFFile(f);needed=[t.needed for t in e.get_section_by_name('.dynamic').iter_tags()if t.entry.d_tag=='DT_NEEDED'];references=[]
  for section in e.iter_sections():
   if section['sh_type']!='SHT_RELA':continue
   symbols=e.get_section(section['sh_link'])
   for rel in section.iter_relocations():
    if not rel['r_info_sym']:continue
    sym=symbols.get_symbol(rel['r_info_sym'])
    if sym.name.startswith(('gl','egl'))and sym['st_shndx']!='SHN_UNDEF':references.append({'symbol':sym.name,'kind':rel['r_info_type']})
 record.update(needed=needed,preemptible_frontend_references=references);assert 'libEGL.so.1'in needed,'original native EGL globals still unavailable';assert not references,'private API addresses can still bind to native Mali frontend';record['exit']=0
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
