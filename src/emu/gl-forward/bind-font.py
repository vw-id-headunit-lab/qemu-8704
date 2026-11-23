
from pathlib import Path
from io import BytesIO
import argparse,hashlib,json
import lief
from elftools.elf.elffile import ELFFile

ROOT=Path(__file__).resolve().parents[3]
PARENT_SHA='1e420adc99347ca0ce6cccda8512cf783291cb3e7cba36e05e79467b212cf34f'

def bind(raw):
 if hashlib.sha256(raw).hexdigest()!=PARENT_SHA:raise ValueError('Original FontSPI identity differs')
 original=ELFFile(BytesIO(raw));binary=lief.ELF.parse(raw)
 if original['e_machine']!='EM_AARCH64':raise ValueError('Different FontSPI ABI')
 if any(t.entry.d_tag in ('DT_RPATH','DT_RUNPATH')for t in original.get_section_by_name('.dynamic').iter_tags()):raise ValueError('Existing FontSPI search path')
 if not binary.relocate_phdr_table(lief.ELF.Binary.PHDR_RELOC.BSS_END):raise ValueError('Cannot place FontSPI metadata after original BSS')
 binary.add(lief.ELF.DynamicEntryRpath('/lge/hmi/libs'));result=bytes(binary.write_to_bytes());changed=ELFFile(BytesIO(result));verified=[]
 metadata={'.dynamic','.dynstr','.dynsym','.gnu.hash','.hash','.gnu.version','.gnu.version_r'}
 for section in original.iter_sections():
  if section['sh_flags']&2 and section.name not in metadata:
   other=changed.get_section_by_name(section.name)
   if other is None or other['sh_addr']!=section['sh_addr']or other.data()!=section.data():raise ValueError('Original allocated FontSPI section changed: '+section.name)
   verified.append(dict(name=section.name,address=hex(section['sh_addr']),sha256=hashlib.sha256(section.data()).hexdigest()))
 def exports(elf):return {s.name:(s['st_value'],s['st_size'],s['st_info']['type'])for s in elf.get_section_by_name('.dynsym').iter_symbols()if s['st_shndx']!='SHN_UNDEF'}
 def needed(elf):return [t.needed for t in elf.get_section_by_name('.dynamic').iter_tags()if t.entry.d_tag=='DT_NEEDED']
 if exports(original)!=exports(changed)or needed(original)!=needed(changed):raise ValueError('FontSPI ABI or original dependency names changed')
 paths=[t.rpath for t in changed.get_section_by_name('.dynamic').iter_tags()if t.entry.d_tag=='DT_RPATH']
 if paths!=['/lge/hmi/libs']:raise ValueError('FontSPI path readback differs')
 return result,dict(parent_sha256=PARENT_SHA,sha256=hashlib.sha256(result).hexdigest(),bytes=len(result),DT_RPATH=paths[0],original_needed=needed(original),original_exports=len(exports(original)),original_allocated_sections_unchanged=verified,AT_SECURE_policy_changed=False)

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input',type=Path);parser.add_argument('output',type=Path);a=parser.parse_args();out=a.output.resolve()
 if not out.is_relative_to(ROOT)or out==a.input.resolve():raise ValueError('Require separate project copy')
 raw=a.input.read_bytes();result,meta=bind(raw);out.write_bytes(result)
 if out.read_bytes()!=result or a.input.read_bytes()!=raw:raise ValueError('FontSPI write readback differs')
 out.with_suffix('.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta))

if __name__=='__main__':main()
