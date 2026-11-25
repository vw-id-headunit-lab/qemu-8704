from pathlib import Path
import json,sys,subprocess,tempfile,struct,hashlib,io
ROOT=Path(__file__).resolve().parents[1]
from elftools.elf.elffile import ELFFile

def dynamic_exports(raw):
 e=ELFFile(io.BytesIO(raw));segs=list(e.iter_segments());d=next(s for s in segs if s['p_type']=='PT_DYNAMIC');tags={x.entry.d_tag:x.entry.d_val for x in d.iter_tags()}
 def read(va,n):
  s=next(s for s in segs if s['p_type']=='PT_LOAD'and s['p_vaddr']<=va and va+n<=s['p_vaddr']+s['p_filesz']);p=s['p_offset']+va-s['p_vaddr'];return raw[p:p+n]
 strings=read(tags['DT_STRTAB'],tags['DT_STRSZ']);size=tags['DT_STRTAB']-tags['DT_SYMTAB'];assert size>0 and size%24==0;symbols=read(tags['DT_SYMTAB'],size);out=set()
 for off in range(0,size,24):
  n,info,other,section,value,length=struct.unpack_from('<IBBHQQ',symbols,off)
  if section and info>>4 in [1,2]:out.add(strings[n:].split(b'\0')[0].decode())
 return out

def build(output):
 output=Path(output);output.mkdir(parents=True,exist_ok=True)
 source=ROOT/'var/inputs/analysis/cpu/qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn';raw=source.read_bytes();assert hashlib.sha256(raw).hexdigest()=='35306f08c1b9def32271ddb5233522cfe6abe79949dbe5b7c069724c88f92c96'
 inventory=json.loads((ROOT/'var/inputs/analysis/qnx-ifs-20261021T125600Z/findings.json').read_text());exports={};records=[]
 for soname in ['libc.so.4','libsocket.so.3','libscreen.so.1','libEGL.so.1']:
  row=next(x for x in inventory['entries']if x['name']==('usr/lib/'if soname=='libEGL.so.1'else'proc/boot/')+soname);data=raw[row['absolute_offset']:row['absolute_offset']+row['file_size']];assert hashlib.sha256(data).hexdigest()==row['sha256'];exports[soname]=dynamic_exports(data)
 zig=ROOT/'.local/deps/zig-aarch64-macos-0.15.2/zig';common=[str(zig),'cc','-target','aarch64-linux-gnu','-nostdlib','-fPIC','-O2','-g0','-fno-stack-protector','-fno-omit-frame-pointer','-mno-outline-atomics']
 def run(args):
  p=subprocess.run(list(map(str,args)),cwd=ROOT,capture_output=True,text=True);records.append({'argv':list(map(str,args)),'stdout':p.stdout,'stderr':p.stderr,'exit':p.returncode})
  if p.returncode:
   (ROOT/'.local/mac-port/transaction/qnx-forward-build-error.json').write_text(json.dumps(records,indent=2)+'\n');raise RuntimeError(p.stderr)
 with tempfile.TemporaryDirectory(prefix='qnx-gl-link-')as tmp:
  t=Path(tmp);obj=t/'client.o';run(common+['-DGL_FORWARD_QNX','-D__QNX__','-I'+str(ROOT/'.local/angle-sdk/include'),'-c',str(ROOT/'src/emu/gl-forward/gles-client.c'),'-o',str(obj)])
  with obj.open('rb')as f:
   e=ELFFile(f);undefined={s.name for s in e.get_section_by_name('.symtab').iter_symbols()if s.name and s['st_shndx']=='SHN_UNDEF'}-{'_GLOBAL_OFFSET_TABLE_'}
  native_egl_globals={'_glesGetProcAddressProc','_egl_context','_gles2StartTraceProc','_gles2GetProcAddressProc','__eglTlsContext'};assert native_egl_globals<=exports['libEGL.so.1'];missing=undefined-set().union(*exports.values());assert not missing,('No native QNX export',missing)
  assigned={n:[]for n in exports}
  for name in sorted(undefined):assigned[next(n for n in exports if name in exports[n])].append(name)
  links=[]
  for soname,names in assigned.items():
   if not names and soname!='libEGL.so.1':continue
   c=t/(soname+'.c');c.write_text('\n'.join('void '+n+'(void){}'for n in names)+'\n');lib=t/(soname+'.link.so');run(common+['-shared','-Wl,--hash-style=sysv','-Wl,-z,max-page-size=4096','-Wl,-soname,'+soname,str(c),'-o',str(lib)]);links.append(str(lib))
  core=output/'libmeb-qnx-gl-forward.so';run(common+['-shared','-Wl,--hash-style=sysv','-Wl,-z,defs','-Wl,-Bsymbolic','-Wl,--no-as-needed','-Wl,-z,max-page-size=4096','-Wl,-soname,/proc/boot/libmeb-qnx-gl-forward.so',str(obj),*links,'-o',str(core)])
  empty=t/'proxy.c';empty.write_text('int meb_qnx_gl_proxy_abi=1;\n')
  for soname in ['libMEG.so','libMEBGL2.so']:
   run(common+['-shared','-Wl,--hash-style=sysv','-Wl,-z,max-page-size=4096','-Wl,-soname,'+soname,str(empty),'-Wl,--no-as-needed',str(core),'-o',str(output/soname)])
  with core.open('rb')as f:
   e=ELFFile(f);assert not any(s['p_type']=='PT_TLS'for s in e.iter_segments());assert not any(s.name.startswith('slog2')for s in e.get_section_by_name('.dynsym').iter_symbols());needed=[x.needed for x in e.get_section_by_name('.dynamic').iter_tags()if x.entry.d_tag=='DT_NEEDED'];assert set(needed)==({n for n,v in assigned.items()if v}|{'libEGL.so.1'})
 result={'records':records,'native_imports':assigned,'native_EGL_dependency':'original libEGL.so.1 (global Screen/GLES ABI only)','native_EGL_required_globals':sorted(native_egl_globals),'private_functions_bound_locally':True,'link_stubs_shipped':False,'TLS_segment':False,'assets':{p.name:{'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in output.glob('*.so*')}}
 (ROOT/'.local/mac-port/transaction/qnx-forward-build.json').write_text(json.dumps(result,indent=2)+'\n');return result
if __name__=='__main__':print(json.dumps(build(sys.argv[1]if len(sys.argv)>1 else ROOT/'runtime/guest/qnx-gl'),indent=2))
