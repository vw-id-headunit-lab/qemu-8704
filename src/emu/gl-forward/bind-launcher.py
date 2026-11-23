
from pathlib import Path
from io import BytesIO
import argparse,hashlib,json
import lief
from elftools.elf.elffile import ELFFile

ROOT=Path(__file__).resolve().parents[3]
PARENT_SHA='b3db9b25a18c8b29da3cbdf60b0a7561b2df6c83473b55766866849d6cc4344a'
JSIG='/lge/hmi/jvm/lib/aarch64/libjsig.so'

def bind(raw):
 if hashlib.sha256(raw).hexdigest()!=PARENT_SHA:raise ValueError('Original Java launcher identity differs')
 original=ELFFile(BytesIO(raw));binary=lief.ELF.parse(raw)
 if original['e_machine']!='EM_AARCH64':raise ValueError('Different Java launcher ABI')
 def needed(elf):return [t.needed for t in elf.get_section_by_name('.dynamic').iter_tags()if t.entry.d_tag=='DT_NEEDED']
 old=needed(original)
 if old!=['libpthread.so.0','libjli.so','libdl.so.2','libc.so.6','ld-linux-aarch64.so.1']:raise ValueError('Original launcher linkage differs')
 if not binary.relocate_phdr_table(lief.ELF.Binary.PHDR_RELOC.BSS_END):raise ValueError('Cannot place launcher metadata after original BSS')
 binary.add_library(JSIG)
 entries=[t for t in binary.dynamic_entries if t.tag==lief.ELF.DynamicEntry.TAG.NEEDED]
 if len(entries)!=len(old)+1:raise ValueError('Unexpected launcher dependency count')
 for entry,name in zip(entries,[JSIG,*old]):entry.name=name
 result=bytes(binary.write_to_bytes());changed=ELFFile(BytesIO(result));verified=[]
 metadata={'.dynamic','.dynstr','.dynsym','.gnu.hash','.hash','.gnu.version','.gnu.version_r'}
 for section in original.iter_sections():
  if section['sh_flags']&2 and section.name not in metadata:
   other=changed.get_section_by_name(section.name)
   if other is None or other['sh_addr']!=section['sh_addr']or other.data()!=section.data():raise ValueError('Original launcher allocated section changed: '+section.name)
   verified.append(dict(name=section.name,address=hex(section['sh_addr']),sha256=hashlib.sha256(section.data()).hexdigest()))
 def exports(elf):return {s.name:(s['st_value'],s['st_size'],s['st_info']['type'])for s in elf.get_section_by_name('.dynsym').iter_symbols()if s['st_shndx']!='SHN_UNDEF'}
 if original['e_entry']!=changed['e_entry']or exports(original)!=exports(changed)or needed(changed)!=[JSIG,*old]:raise ValueError('Launcher entry, ABI or dependency order differs')
 paths=[t.rpath for t in changed.get_section_by_name('.dynamic').iter_tags()if t.entry.d_tag=='DT_RPATH']
 if paths!=['$ORIGIN/../lib/aarch64/jli:$ORIGIN/../lib/aarch64']:raise ValueError('Original Java layout contract changed')
 return result,dict(parent_sha256=PARENT_SHA,sha256=hashlib.sha256(result).hexdigest(),bytes=len(result),DT_NEEDED=needed(changed),DT_RPATH=paths,original_allocated_sections_unchanged=verified,original_exports=len(exports(original)),security_mode_change_requested=False,security_metadata_staging_pending=True,layout_requirement='private jvm/bin/java with jvm/lib symlink to original /lge/hmi/jvm/lib',runtime_verified=False)

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input',type=Path);parser.add_argument('output',type=Path);a=parser.parse_args();out=a.output.resolve()
 if not out.is_relative_to(ROOT)or out==a.input.resolve():raise ValueError('Require separate project copy')
 raw=a.input.read_bytes();result,meta=bind(raw);out.write_bytes(result)
 if out.read_bytes()!=result or a.input.read_bytes()!=raw:raise ValueError('Java launcher write readback differs')
 out.with_suffix('.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta))

if __name__=='__main__':main()
