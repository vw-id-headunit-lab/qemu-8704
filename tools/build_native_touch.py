from pathlib import Path
import gzip,hashlib,io,json,os,subprocess,sys,tempfile
import lief
from elftools.elf.elffile import ELFFile
from dissect.extfs import ExtFS

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL='e1bb4059e85b0ffc1108f66b0a9e23f5a1ebec590804602ebf00ab67c47256c9'

def bind(raw):
 if hashlib.sha256(raw).hexdigest()!=ORIGINAL:raise ValueError('Original touch-app differs')
 before=ELFFile(io.BytesIO(raw));binary=lief.ELF.parse(raw)
 if before['e_machine']!='EM_AARCH64':raise ValueError('Original touch ABI differs')
 if not binary.relocate_phdr_table(lief.ELF.Binary.PHDR_RELOC.BSS_END):raise ValueError('Touch metadata placement failed')
 dependency='/vw/oem-touch/provider.so';binary.add_library(dependency)
 old=[x.needed for x in before.get_section_by_name('.dynamic').iter_tags()if x.entry.d_tag=='DT_NEEDED']
 for tag,name in zip([x for x in binary.dynamic_entries if x.tag==lief.ELF.DynamicEntry.TAG.NEEDED],[dependency,*old]):tag.name=name
 result=bytes(binary.write_to_bytes());after=ELFFile(io.BytesIO(result));sections=[]
 metadata={'.dynamic','.dynstr','.dynsym','.gnu.hash','.hash','.gnu.version','.gnu.version_r'}
 for section in before.iter_sections():
  if section['sh_flags']&2 and section.name not in metadata:
   other=after.get_section_by_name(section.name)
   if other is None or section['sh_addr']!=other['sh_addr']or section.data()!=other.data():raise ValueError('Original touch section differs: '+section.name)
   sections.append(dict(name=section.name,address=hex(section['sh_addr']),sha256=hashlib.sha256(section.data()).hexdigest()))
 def exports(e):return {s.name:(s['st_value'],s['st_size'],s['st_info']['type'])for s in e.get_section_by_name('.dynsym').iter_symbols()if s['st_shndx']!='SHN_UNDEF'}
 needed=[x.needed for x in after.get_section_by_name('.dynamic').iter_tags()if x.entry.d_tag=='DT_NEEDED']
 if before['e_entry']!=after['e_entry']or exports(before)!=exports(after)or needed!=[dependency,*old]:raise ValueError('Original touch entry/ABI differs')
 return result,dict(original_sha256=ORIGINAL,DT_NEEDED=needed,original_allocated_sections_unchanged=sections,original_exports=len(exports(before)),original_entry=hex(before['e_entry']))

def build():
 output=ROOT/'runtime/guest/native-touch';output.mkdir(parents=True,exist_ok=True)
 with(ROOT/'var/inputs/analysis/cpu/ivi/icas3cn-gp-image-euto-v9.ext4').open('rb')as f:raw=ExtFS(f).get('/lge/app_ro/hmi/touch-app').open().read()
 changed,proof=bind(raw);(output/'touch-app').write_bytes(changed)
 common=[str(ROOT/'.local/deps/zig-aarch64-macos-0.15.2/zig'),'cc','-target','aarch64-linux-gnu.2.27','-O2','-g0','-mno-outline-atomics'];commands=[]
 env=dict(os.environ,ZIG_GLOBAL_CACHE_DIR=str(ROOT/'.local/cache/zig-global'),ZIG_LOCAL_CACHE_DIR=str(ROOT/'.local/cache/zig-local'))
 for source,destination,flags in [('src/hvf_bench/native/unified-touch.c','provider.so',['-fPIC','-shared','-pthread','-ldl']),('src/emu/native-touch-relay.c','relay',[])]:
  argv=common+flags+[str(ROOT/source),'-o',str(output/destination)];p=subprocess.run(argv,env=env,capture_output=True,text=True);commands.append(dict(argv=argv,stdout=p.stdout,stderr=p.stderr,exit=p.returncode))
  if p.returncode:print(json.dumps(commands));raise RuntimeError('Native touch build failed')
 assets={name:dict(path=str((output/name).relative_to(ROOT)),bytes=(output/name).stat().st_size,sha256=hashlib.sha256((output/name).read_bytes()).hexdigest())for name in ('touch-app','provider.so','relay')}
 result=dict(profile='original-unified-touch-v1',assets=assets,binding=proof,commands=commands,source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in ('src/hvf_bench/native/unified-touch.c','src/emu/native-touch-relay.c')})
 (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));return result

if __name__=='__main__':build()
