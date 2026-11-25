from pathlib import Path
import sys,json,hashlib
from elftools.elf.elffile import ELFFile
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];root=binary.parents[2];p=root/'runtime/guest/qnx-gl/libmeb-qnx-gl-forward.so';s=(root/'src/emu/gl-forward/qnx-transport.h').read_text();record={'binary':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'label':label,'input':'Native SIGSEGV observation must preserve previous handler/re-raise, bounded FP/LR frame capture and last record entry; ELF/contract check only, actual original QNX fault separately required'}
try:
 with p.open('rb')as f:
  e=ELFFile(f);syms={x.name:x for x in e.get_section_by_name('.symtab').iter_symbols()};imports={x.name for x in e.get_section_by_name('.dynsym').iter_symbols()if x['st_shndx']=='SHN_UNDEF'};assert 'qnx_fault_handler'in syms and{'signal','raise'}<=imports;assert 'signal(sig,qnx_previous_segv);raise(sig);'in s;assert 'i<8'in s and'256*1024'in s;record.update(exit=0,handler_address=syms['qnx_fault_handler']['st_value'],native_imports=sorted(imports&{'signal','raise'}),actual_fault_not_masked=True)
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
