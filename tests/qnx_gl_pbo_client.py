from pathlib import Path
import sys,subprocess,tempfile,json
r=Path(__file__).resolve().parents[1];source=Path(sys.argv[1]).resolve();text=source.read_text();a=text.index('void glReadPixels(');b=text.index('\nvoid glRenderbufferStorage',a);function=text[a:b]
code='''#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <assert.h>
#define GL_FORWARD_QNX 1
typedef unsigned u32;typedef int i32;
static unsigned calls,used,unlocks,copy_bytes;static unsigned char wire[64];
static void rec(u32 op,u32 size){assert(op==173&&size==32);calls++;used=0;}
static void part(const void *p,unsigned n){assert(used+n<=sizeof(wire));memcpy(wire+used,p,n);used+=n;}
#define PART(p,n) part(p,n)
static void transport_failed(void){abort();}
static void recv_bytes(void *p,unsigned long n,int release){assert(n==4);*(u32 *)p=copy_bytes;}
static void unlock_record(void){unlocks++;}
static void recv_all(void *p,unsigned long n){memset(p,0x5a,n);unlocks++;}
static void emit_iv(u32 op,const i32 *p,u32 n){calls++;}
'''+function+'''
int main(void){
 glReadPixels(0,0,1140,114,0x1908,0x1401,0);if(calls!=1)return 2;assert(used==32&&unlocks==1);uint64_t offset=123;memcpy(&offset,wire+24,8);assert(offset==0);
 glReadPixels(0,0,1140,114,0x1908,0x1401,(void *)16);memcpy(&offset,wire+24,8);assert(calls==2&&offset==16&&unlocks==2);
 unsigned char pixels[16]={0};copy_bytes=16;glReadPixels(0,0,2,2,0x1908,0x1401,pixels);for(unsigned i=0;i<16;i++)assert(pixels[i]==0x5a);assert(calls==3&&unlocks==3);
 glReadPixels(0,0,-1,2,0x1908,0x1401,pixels);assert(calls==3);puts("PBO offset0 and16 forwarded; CPU16bytes copied; invalid dimensions unchanged");return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='qnx-pbo-client-')as tmp:
 d=Path(tmp);(d/'client.c').write_text(code);p=subprocess.run(['clang','-O1','-fsanitize=address,undefined',str(d/'client.c'),'-o',str(d/'client')],capture_output=True,text=True);assert p.returncode==0,p.stderr;q=subprocess.run([str(d/'client')],capture_output=True,text=True);print(json.dumps({'source':str(source),'stdout':q.stdout,'stderr':q.stderr,'exit':q.returncode},indent=2));raise SystemExit(q.returncode)
