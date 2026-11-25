from pathlib import Path
import json,subprocess,sys,tempfile
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
report={'scope':'Executable virtual touch transport ABI fixture; original HMI/nav live proof recorded separately','commands':[]}
try:
 source=root/'src/hvf_bench/native/unified-touch.c'
 java=(root/'src/hvf_bench/platform/BenchPlatform.java').read_text()
 block=java[java.index('private static final class NativeTouch'):java.index('private static final class PointerProtocol')]
 assert 'new FileOutputStream("/tmp/bench-touch-events")'in block and 'registerInputDevice'not in block and 'TouchHoverInputDevice'not in block
 code=r"""
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <poll.h>
#include <time.h>
static jmp_buf done;
static unsigned char memory[0x1a0000];
static void *table[16],*sender[1],*instance[2];
static int packets[8][44],properties[18],count,opened;
static const char *wire="geometry\ndown 80 48\nmove 150 90\nup 150 90\nmove 999 999\ndown 1920 0\ndown 300 400\n";
static unsigned offset;
static void fixture_touch(void*s,int*p){if(s!=sender||count==8)exit(10);memcpy(packets[count++],p,sizeof(packets[0]));}
static void fixture_property(void*s,int a,int b,int c,char*d,int e,int*p){if(s!=sender||a||b||c||d||e)exit(11);memcpy(properties,p,sizeof(properties));}
void *fixture_dlsym(void*h,const char*n){if(h)exit(12);if(strstr(n,"TouchMgr11getInstance"))return memory+0xb8cb8;if(strstr(n,"updateTouchCoordinateState"))return fixture_touch;if(strstr(n,"updateProperty"))return fixture_property;return 0;}
int fixture_open(const char*p,int flags,...){if(strcmp(p,"/tmp/bench-touch-events")||flags)exit(13);if(opened++)longjmp(done,1);return 9;}
long fixture_read(int fd,void*p,size_t n){if(fd!=9||n!=1)exit(14);if(!wire[offset])return 0;*(char*)p=wire[offset++];return 1;}
int fixture_close(int fd){return fd==9?0:1;}
int fixture_usleep(unsigned n){(void)n;exit(15);}
int fixture_thread(unsigned long*t,const void*a,void*(*fn)(void*),void*b){(void)t;(void)a;(void)fn;(void)b;return 1;}
int fixture_poll(struct pollfd*p,nfds_t n,int ms){(void)p;(void)n;(void)ms;return 1;}
int fixture_clock(clockid_t id,struct timespec *t){static long ticks=0;(void)id;t->tv_sec=1;t->tv_nsec=(ticks++*10)*1000000;return 0;}
#define poll fixture_poll
#define clock_gettime fixture_clock
#define dlsym fixture_dlsym
#define open fixture_open
#define read fixture_read
#define close fixture_close
#define usleep fixture_usleep
#define pthread_create fixture_thread
"""
 code+=source.read_text()+r"""
int sigwaitinfo(const signal_set*s,void*i){(void)s;(void)i;return -1;}
int __libc_current_sigrtmin(void){return 34;}
int main(void){sender[0]=table;table[14]=fixture_touch;instance[1]=sender;*(void**)(memory+0x19dbb0)=instance;
if(!setjmp(done))hardware(0);
if(count!=5||properties[3]!=1920||properties[4]!=1080||properties[8]!=286||properties[9]!=161||properties[10]!=174)return 20;
int expected[5][7]={{10,1,1,1,80,48,253},{10,1,1,1,150,90,254},{10,1,0,1,150,90,255},{10,1,1,1,300,400,253},{10,1,0,1,300,400,255}};
for(int i=0;i<5;i++)for(int j=0;j<7;j++)if(packets[i][j]!=expected[i][j])return 21;
printf("ORIGINAL_PROVIDER_ABI_PASS packets=5 begin=253 move=254 end=255 dimensions=1920x1080 eof_release=1\n");return 0;}
"""
 with tempfile.TemporaryDirectory(prefix='original-touch-fixture-')as d:
  p=Path(d)/'fixture.c';p.write_text(code);exe=Path(d)/'fixture'
  for argv in [['cc','-O2',str(p),'-o',str(exe)],[str(exe)]]:
   a=subprocess.run(argv,capture_output=True,text=True);report['commands'].append(dict(argv=argv,stdout=a.stdout,stderr=a.stderr,exit=a.returncode));assert a.returncode==0
 report['exit']=0
except Exception as error:report.update(error=repr(error),exit=2)
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
