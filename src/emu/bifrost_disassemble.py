
from pathlib import Path
import json,hashlib,subprocess,tempfile,argparse,os
R=Path(__file__).resolve().parents[2];D=R/'var/inputs/support/center-live-render';ap=argparse.ArgumentParser();ap.add_argument('input',type=Path);ap.add_argument('output',type=Path);a=ap.parse_args();data=a.input.resolve(strict=True).read_bytes();assert data and len(data)%16==0 and len(data)<=65536;O=D/'mesa-bifrost';gcc=R/'.local/deps/bin/gcc.exe';env=dict(os.environ,PATH=str(gcc.parent)+os.pathsep+os.environ['PATH']);records=[]
def run(label,argv):
 p=subprocess.run([str(x)for x in argv],capture_output=True,env=env)
 rec=dict(label=label,argv=p.args,stdout=p.stdout.decode('utf-8','backslashreplace'),stderr=p.stderr.decode('utf-8','backslashreplace'),exit_status=p.returncode)
 records.append(rec)
 with (D/'VERIFICATION.txt').open('a',encoding='utf-8') as log:
  log.write('\n'+json.dumps(dict(event='bifrost-isa-disassembly',**rec),ensure_ascii=True)+'\n')
 assert not p.returncode,rec
 return p
with tempfile.TemporaryDirectory(prefix='mesa-disasm-',dir=D)as td:
 t=Path(td);(t/'util').mkdir();(t/'bifrost').mkdir()
 for name in ['bifrost.h','bi_print_common.h','bi_print_common.c','generated-disasm.c']:(t/name).write_bytes((O/name).read_bytes())
 for name in ['bi_disasm.h','disassemble.h']:(t/'bifrost'/name).write_bytes((O/'bifrost'/name).read_bytes())
 (t/'bifrost/disassemble.c').write_bytes((O/'disassemble.c').read_bytes())
                                                                                    
 (t/'util/compiler.h').write_text('#pragma once\n#define ASSERTED __attribute__((unused))\n#define unlikely(x) __builtin_expect(!!(x),0)\n',encoding='utf-8');(t/'util/macros.h').write_text('#pragma once\n#include <stdlib.h>\n#include "compiler.h"\n#define UNREACHABLE(msg) do { fprintf(stderr,"%s\\n",msg); abort(); } while(0)\n#define FALLTHROUGH __attribute__((fallthrough))\n',encoding='utf-8')
 (t/'main.c').write_text('#include <stdlib.h>\n#include <stdio.h>\n#include "bifrost/disassemble.h"\nint main(int argc,char**argv){if(argc!=2)return 64;FILE*f=fopen(argv[1],"rb");if(!f)return 66;fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);if(n<=0||n>65536||(n&15))return 65;void*p=calloc(1,n);if(!p)return 71;if(fread(p,1,n,f)!=(size_t)n)return 74;fclose(f);disassemble_bifrost(stdout,p,n,true);free(p);return 0;}\n',encoding='utf-8')
 exe=t/'disasm.exe';run('COMPILE',[gcc,'-std=c11','-mno-ms-bitfields','-O0','-I',t,'-I',t/'bifrost',t/'main.c',t/'bifrost/disassemble.c',t/'generated-disasm.c',t/'bi_print_common.c','-o',exe]);p=run('DISASSEMBLE',[exe,a.input.resolve()]);a.output.write_bytes(p.stdout);print('Original shader',len(data),'sha256',hashlib.sha256(data).hexdigest(),'disassembly',str(a.output.resolve()))
