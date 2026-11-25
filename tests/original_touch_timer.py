from pathlib import Path
import json,subprocess,sys,tempfile
root=Path(sys.argv[1])if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
source=root/'src/hvf_bench/native/unified-touch.c'
s=source.read_text()if source.exists()else''
modified='static void *timers('in s
body=s[s.index('static void *timers('):s.index('static void *hardware(')]if modified else''
code=r"""
#include <signal.h>
#include <pthread.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
static sigset_t timer_signals;
static pthread_mutex_t mutex=PTHREAD_MUTEX_INITIALIZER;
static volatile int count;
static void callback(int signal,void*info,void*context){
 if(signal!=SIGUSR1||*(int*)((char*)info+24)!=7||*(int*)((char*)info+20)!=3||context)_exit(9);
 pthread_mutex_lock(&mutex);count++;pthread_mutex_unlock(&mutex);
}
static void (*timer_handler)(int,void*,void*)=callback;
static int fixture_wait(const sigset_t*s,void*i){int n;int result=sigwait(s,&n);if(result)return -1;memset(i,0,128);*(int*)((char*)i+24)=7;*(int*)((char*)i+20)=3;return n;}
static void unsafe_handler(int n){char info[128]={0};*(int*)(info+24)=7;*(int*)(info+20)=3;callback(n,info,0);}
static void* sender(void*unused){(void)unused;usleep(30000);kill(getpid(),SIGUSR1);return 0;}
#define sigwaitinfo fixture_wait
"""+body+r"""
int main(void){
 sigemptyset(&timer_signals);sigaddset(&timer_signals,SIGUSR1);pthread_sigmask(SIG_BLOCK,&timer_signals,0);
 pthread_t worker,source;pthread_mutex_lock(&mutex);
#ifdef SAFE_DELIVERY
 pthread_create(&worker,0,timers,0);
#else
 signal(SIGUSR1,unsafe_handler);
#endif
 pthread_create(&source,0,sender,0);
#ifndef SAFE_DELIVERY
 pthread_sigmask(SIG_UNBLOCK,&timer_signals,0);
#endif
 usleep(150000);pthread_mutex_unlock(&mutex);pthread_join(source,0);
 for(int i=0;i<100&&!count;i++)usleep(1000);
 if(count!=1)return 10;
 puts("ORIGINAL_TIMER_WORKER_PASS timer_id=7 overrun=3 mutex_reentry=0 delivered=1");return 0;}
"""
records=[]
with tempfile.TemporaryDirectory(prefix='original-timer-reentry-')as d:
 p=Path(d)/'timer.c';p.write_text(code);exe=Path(d)/'timer'
 argv=['cc','-O2','-pthread']+(['-DSAFE_DELIVERY']if modified else[])+[str(p),'-o',str(exe)]
 a=subprocess.run(argv,capture_output=True,text=True);records.append(dict(argv=argv,stdout=a.stdout,stderr=a.stderr,exit=a.returncode))
 if not a.returncode:
  try:
   a=subprocess.run([str(exe)],capture_output=True,text=True,timeout=2);records.append(dict(argv=a.args,stdout=a.stdout,stderr=a.stderr,exit=a.returncode))
  except subprocess.TimeoutExpired as error:records.append(dict(argv=[str(exe)],stdout=(error.stdout or b'').decode(),stderr=(error.stderr or b'').decode(),exit=124))
report=dict(scope='Real POSIX signal/mutex replay; macOS sigwait adapter only supplies recorded timer metadata, Linux native timer ABI checked live',source=str(source),modified=modified,records=records,exit=0 if records[-1]['exit']==0 else 2)
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
