from pathlib import Path
import json,subprocess,sys,tempfile
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
records=[]
try:
 source=(r/'src/hvf_bench/native/unified-touch.c').read_text()
 code=r"""
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <poll.h>
#include <time.h>
static jmp_buf done;
static unsigned char memory[0x1a0000];static void *table[16],*sender[1],*instance[2];
static int packets[20][44],count,opened,pause_count;static unsigned offset;static long tick=100;
static const char *wire="down 75 48\nup 75 48\n";
static void fixture_touch(void*s,int*p){if(s!=sender||count==20)exit(10);memcpy(packets[count++],p,sizeof(packets[0]));}
static void fixture_property(void*s,int a,int b,int c,char*d,int e,int*p){(void)s;(void)a;(void)b;(void)c;(void)d;(void)e;(void)p;}
void *fixture_dlsym(void*h,const char*n){if(h)exit(12);if(strstr(n,"TouchMgr11getInstance"))return memory+0xb8cb8;if(strstr(n,"updateTouchCoordinateState"))return fixture_touch;if(strstr(n,"updateProperty"))return fixture_property;return 0;}
int fixture_open(const char*p,int flags,...){if(strcmp(p,"/tmp/bench-touch-events")||flags)exit(13);if(opened++)longjmp(done,1);return 9;}
int fixture_poll(struct pollfd*p,nfds_t n,int ms){if(p->fd!=9||n!=1)exit(14);if(offset==11&&pause_count<5){if(ms!=10)exit(15);pause_count++;tick++;return 0;}return 1;}
long fixture_read(int fd,void*p,size_t n){if(fd!=9||n!=1)exit(16);if(!wire[offset])return 0;if(offset==11)tick+=13;*(char*)p=wire[offset++];return 1;}
int fixture_close(int fd){return fd==9?0:1;}
int fixture_usleep(unsigned n){(void)n;exit(17);}
int fixture_thread(unsigned long*t,const void*a,void*(*fn)(void*),void*b){(void)t;(void)a;(void)fn;(void)b;return 1;}
int fixture_clock(clockid_t id,struct timespec*t){if(id!=CLOCK_MONOTONIC)exit(18);t->tv_sec=tick/100;t->tv_nsec=(tick%100)*10000000;return 0;}
#define dlsym fixture_dlsym
#define open fixture_open
#define poll fixture_poll
#define read fixture_read
#define close fixture_close
#define usleep fixture_usleep
#define pthread_create fixture_thread
#define clock_gettime fixture_clock
"""+source+r"""
int sigwaitinfo(const signal_set*s,void*i){(void)s;(void)i;return -1;}
int __libc_current_sigrtmin(void){return 34;}
int main(void){sender[0]=table;table[14]=fixture_touch;instance[1]=sender;*(void**)(memory+0x19dbb0)=instance;if(!setjmp(done))hardware(0);
 if(count!=7||packets[0][6]!=253||packets[6][6]!=255||packets[6][0]!=130)return 20;
 for(int i=1;i<6;i++)if(packets[i][0]!=10||packets[i][6]!=254||packets[i][2]!=1||packets[i][4]!=75||packets[i][5]!=48)return 21;
 for(int i=0;i<count;i++)for(int k=7;k<44;k++)if(packets[i][k])return 22;
 puts("ORIGINAL_CONTACT_TIMING_PASS down=1 held_frames=5 up=1 hold_delta_ms=130 original_RSI=1");return 0;}
"""
 with tempfile.TemporaryDirectory(prefix='original-contact-fixture-')as d:
  p=Path(d)/'fixture.c';p.write_text(code);exe=Path(d)/'fixture'
  for argv in [['cc','-O2',str(p),'-o',str(exe)],[str(exe)]]:
   a=subprocess.run(argv,capture_output=True,text=True,timeout=5);records.append(dict(argv=argv,stdout=a.stdout,stderr=a.stderr,exit=a.returncode));assert a.returncode==0
 report=dict(exit=0,records=records)
except Exception as e:report=dict(exit=2,records=records,error=repr(e))
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
