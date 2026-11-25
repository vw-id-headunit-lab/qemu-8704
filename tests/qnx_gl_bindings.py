from pathlib import Path
import json,re,hashlib,sys
from elftools.elf.elffile import ELFFile
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];record={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'label':label,'input':'Exact original GTF queried GL symbol names; distinguish actual implementations from explicit no-return unsupported-call reporters'}
try:
 names=set()
 for x in json.load(open(r/'.local/mac-port/transaction/qnx-gl-original-gtf-contract.json')):names.update(n for n in x.get('dynamic_query_name_literals',[])if re.fullmatch(r'gl[A-Za-z0-9_]+',n))
 with binary.open('rb')as f:
  e=ELFFile(f);exports={s.name for s in e.get_section_by_name('.dynsym').iter_symbols()if s['st_shndx']!='SHN_UNDEF'}
 traps=set(re.findall(r'void (gl\w+)\(void\)',(r/'src/emu/gl-forward/qnx-required-gl.inc').read_text()));missing=sorted(names-exports);record.update(queried=len(names),bound=len(names&exports),missing=missing,explicit_abort_reporters=sorted(traps&exports),actual_implementation_count=len((names&exports)-traps),complete_GL_implementation=False)
 assert not missing,'original GTF can call a null GL function pointer';record['exit']=0
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-bindings-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
