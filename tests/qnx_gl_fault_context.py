from pathlib import Path
import sys,json,hashlib,subprocess,tempfile
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];root=binary.parents[2];p=root/'runtime/guest/qnx-gl/libmeb-qnx-gl-forward.so';s=(root/'src/emu/gl-forward/qnx-transport.h').read_text();record={'label':label,'input':'Native __signalstub always passes signo/siginfo/ucontext; bounded raw siginfo6/context48 words, preserve original fatal; no dladdr in handler','sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
try:
 assert 'const uint64_t *sinfo,const uint64_t *context'in s and 'qnx_fault_words("MEBQNX_FAULT_CONTEXT|",context,48)'in s
 helper=s[s.index('static void qnx_fault_words'):s.index('static void qnx_fault_handler')]
 program='#include <stdint.h>\n#include <unistd.h>\n'+helper+'\nint main(void){uint64_t v[8]={0,1,2,3,4,5,6,0xabcdef};qnx_fault_words("FIXTURE|",v,8);return 0;}\n'
 with tempfile.TemporaryDirectory()as t:
  f=Path(t)/'fixture.c';f.write_text(program);exe=Path(t)/'fixture';a=subprocess.run(['/usr/bin/cc',str(f),'-o',str(exe)],capture_output=True,text=True);assert a.returncode==0,a.stderr;b=subprocess.run([str(exe)],capture_output=True,text=True);assert b.returncode==0 and '0xabcdef'in b.stderr and b.stderr.count('FIXTURE|')==2,b.stderr
 record.update(exit=0,stdout=b.stdout,stderr=b.stderr,scope='Exact bounded output helper, not original crash resolution')
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
