
from pathlib import Path
from io import BytesIO
import argparse,hashlib,json,re
import lief
from elftools.elf.elffile import ELFFile

ROOT=Path(__file__).resolve().parents[3]
PARENT_SHA='e8da73ce0e0ca5575ffa3ca2b8038d324959d1f6528f4543f4cd19b11569668e'
DEPENDENCY='/vw/oem-gl-forward/gfx.so'

def bind(raw):
 if hashlib.sha256(raw).hexdigest()!=PARENT_SHA:raise ValueError('Original JNI identity differs')
 elf=ELFFile(BytesIO(raw))
 if elf['e_machine']!='EM_AARCH64'or not elf.little_endian or elf.elfclass!=64:raise ValueError('Unsupported JNI ABI')
 aliases={}
 for symbol in elf.get_section_by_name('.dynsym').iter_symbols():
  name=symbol.name
  if symbol['st_shndx']=='SHN_UNDEF'and(name.startswith('gl')or name.startswith('egl')):
   replacement=('EGL'+name[3:])if name.startswith('egl')else('GL'+name[2:])
   aliases[name]=replacement
 dependencies=[tag.needed for tag in elf.get_section_by_name('.dynamic').iter_tags()if tag.entry.d_tag=='DT_NEEDED']
 if len(aliases)!=401 or dependencies[:2]!=['libGLESv2_adreno.so','libEGL_adreno.so']:raise ValueError('JNI import contract differs')
 binary=lief.ELF.parse(raw)
 if not binary.relocate_phdr_table(lief.ELF.Binary.PHDR_RELOC.SEGMENT_GAP):raise ValueError('No original segment gap for metadata relocation')
 for symbol in binary.dynamic_symbols:
  if symbol.shndx==0 and symbol.name in aliases:symbol.name=aliases[symbol.name]
 for name in dependencies[:2]:binary.get_library(name).name=DEPENDENCY
 result=bytes(binary.write_to_bytes());changed=ELFFile(BytesIO(result));verified=[]
 for section in elf.iter_sections():
  if section['sh_flags']&4:
   other=changed.get_section_by_name(section.name)
   if other is None or other['sh_addr']!=section['sh_addr']or other.data()!=section.data():raise ValueError('Original executable section changed')
   verified.append(dict(name=section.name,address=hex(section['sh_addr']),sha256=hashlib.sha256(section.data()).hexdigest()))
 def exports(e):return {s.name:(s['st_value'],s['st_size'],s['st_info']['type'])for s in e.get_section_by_name('.dynsym').iter_symbols()if s['st_shndx']!='SHN_UNDEF'}
 if exports(elf)!=exports(changed):raise ValueError('Original JNI export ABI changed')
 imports={s.name for s in changed.get_section_by_name('.dynsym').iter_symbols()if s['st_shndx']=='SHN_UNDEF'}
 if any(n in imports or new not in imports for n,new in aliases.items()):raise ValueError('JNI import readback differs')
 newdeps=[t.needed for t in changed.get_section_by_name('.dynamic').iter_tags()if t.entry.d_tag=='DT_NEEDED']
 if newdeps!=[DEPENDENCY,DEPENDENCY,*dependencies[2:]]:raise ValueError('JNI dependencies changed unexpectedly')
 return result,dict(parent_sha256=PARENT_SHA,sha256=hashlib.sha256(result).hexdigest(),bytes=len(result),dependency=DEPENDENCY,aliases=aliases,AT_SECURE_policy_changed=False,original_code_unchanged=True,executable_sections=verified,builder='LIEF '+lief.__version__)

def native_aliases(aliases,source):
 supported=set()
 for name in ['gl-procs.inc','egl-procs.inc']:
  supported.update(re.findall(r'ME_PROC\((\w+)\)',(source/name).read_text()))
 supported.add('eglGetProcAddress');lines=[];missing=[]
 for old,new in sorted(aliases.items()):
  if old in supported:lines.append(f'extern __typeof__({old}) {new} __attribute__((alias("{old}")));')
  else:
   missing.append(old);lines.append(f'_Noreturn void {new}(void){{report_unsupported_native("{old}");}}')
 return ('\n'.join(lines)+'\n').encode(),missing

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input',type=Path);parser.add_argument('output',type=Path);a=parser.parse_args();out=a.output.resolve()
 if not out.is_relative_to(ROOT)or out==a.input.resolve():raise ValueError('Require separate project output')
 raw=a.input.read_bytes();result,meta=bind(raw);out.write_bytes(result)
 if out.read_bytes()!=result or a.input.read_bytes()!=raw:raise ValueError('JNI write readback differs')
 inc,missing=native_aliases(meta['aliases'],Path(__file__).resolve().parent);Path(__file__).with_name('native-aliases.inc').write_bytes(inc);meta.update(input=str(a.input.resolve()),output=str(out),unimplemented_native_imports=missing,implemented_aliases=len(meta['aliases'])-len(missing),unsupported_policy='named immediate abort, never an empty success return')
 out.with_suffix('.json').write_text(json.dumps(meta,indent=2)+'\n',encoding='utf8');print(json.dumps({k:v for k,v in meta.items()if k not in ['aliases','unimplemented_native_imports']}))

if __name__=='__main__':main()
