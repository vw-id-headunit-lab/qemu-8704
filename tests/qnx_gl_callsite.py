from pathlib import Path
import sys,json,hashlib,subprocess,tempfile
from elftools.elf.elffile import ELFFile
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];root=binary.parents[2];p=root/'runtime/guest/qnx-gl/libmeb-qnx-gl-forward.so';s=(root/'src/emu/gl-forward/qnx-transport.h').read_text();c=(root/'src/emu/gl-forward/gles-client.c').read_text();record={'input':'Original texture-bind caller module identity collected in normal call path, never dladdr in signal handler; native pidin mem before unsupported backtrace','binary':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'label':label}
try:
 with p.open('rb')as f:
  e=ELFFile(f);imports={x.name for x in e.get_section_by_name('.dynsym').iter_symbols()if x['st_shndx']=='SHN_UNDEF'};assert 'dladdr'in imports
 assert 'qnx_trace_callers(__builtin_frame_address(0));'in c
 assert 'dladdr('not in s[s.index('static void qnx_fault_handler'):s.index('static void qnx_enable_fault_trace')]
 assert 'GtfStartup backtrace'not in s
 helper=s[s.index('struct QnxDlInfo'):s.index('static void io_send')]
 assert 'r->caller_count<64'in helper and '256*1024'in helper
 program='''#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
struct Record {uintptr_t callers[64];unsigned caller_count;};static struct Record r;
static struct Record *current_record(void){return &r;}
'''+helper+'''
int main(void){_Alignas(16) uintptr_t fp[4]={(uintptr_t)&fp[2],0x1234,0,0x5678};qnx_trace_callers(fp);qnx_trace_callers(fp);return r.caller_count==2?0:3;}
'''
 program=program.replace('extern int dladdr(void *,struct QnxDlInfo *);','int dladdr(void *p,struct QnxDlInfo *o){o->fname="original-module.so";o->base=(void *)0x1000;o->sname="real_symbol";o->saddr=(void *)0x1200;return p!=0;}')
 with tempfile.TemporaryDirectory()as t:
  f=Path(t)/'fixture.c';f.write_text(program);exe=Path(t)/'fixture';a=subprocess.run(['/usr/bin/cc','-O0',str(f),'-o',str(exe)],capture_output=True,text=True);assert a.returncode==0,a.stderr;b=subprocess.run([str(exe)],capture_output=True,text=True);assert b.returncode==0,b.stderr;assert b.stderr.count('MEBQNX_NATIVE_CALLER|')==2 and 'module=original-module.so'in b.stderr
 record.update(exit=0,fixture_stdout=b.stdout,fixture_stderr=b.stderr,fixture_exit=b.returncode,scope='Exact helper controlled caller/map fixture and actual ELF imports; original native callsite separate runtime evidence')
except Exception as ex:record.update(exit=1,error=repr(ex))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
