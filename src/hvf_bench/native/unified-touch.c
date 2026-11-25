#include <stdint.h>
#include <stddef.h>
#include <poll.h>
#include <time.h>
extern void *dlsym(void *,const char *);
extern int pthread_create(unsigned long *,const void *,void *(*)(void *),void *),pthread_detach(unsigned long);
extern int open(const char *,int,...),close(int),usleep(unsigned),dprintf(int,const char *,...);
extern long read(int,void *,size_t),readlink(const char *,char *,size_t);
extern int strcmp(const char *,const char *);
extern int sscanf(const char *,const char *,...);
typedef struct { unsigned long words[16]; } signal_set;
typedef struct { void (*handler)(int,void *,void *);signal_set mask;int flags;void *restorer; } signal_action;
extern int sigemptyset(signal_set *),sigaddset(signal_set *,int),pthread_sigmask(int,const signal_set *,signal_set *);
extern int sigwaitinfo(const signal_set *,void *),__libc_current_sigrtmin(void);
static signal_set timer_signals;
static void (*timer_handler)(int,void *,void *);
static uintptr_t original_base;
__attribute__((visibility("default"))) int sigaction(int number,const signal_action *action,signal_action *old){
 int (*original)(int,const signal_action *,signal_action *)=dlsym((void *)-1,"sigaction");
 if(!original)return -1;
 int result=original(number,action,old);
 if(!result&&action&&number==__libc_current_sigrtmin()&&(uintptr_t)action->handler==original_base+0x13d0d0&&(action->flags&4)){
  __atomic_store_n(&timer_handler,action->handler,__ATOMIC_RELEASE);
  dprintf(2,"ORIGINAL_INPUT_TIMER_WORKER_BOUND signal=%d handler=13d0d0\n",number);
 }
 return result;
}
static void contact_frame(void *sender,int action,int x,int y,unsigned long long *previous){
 struct timespec now;int delta=10;
 if(!clock_gettime(CLOCK_MONOTONIC,&now)){
  unsigned long long tick=(unsigned long long)now.tv_sec*100+(unsigned long long)now.tv_nsec/10000000;
  if(*previous){unsigned long long elapsed=tick>=*previous?tick-*previous:0;delta=elapsed>255?2550:(int)elapsed*10;}
  *previous=tick;
 }
 int packet[44]={0};packet[0]=delta;packet[1]=1;packet[2]=action==255?0:1;packet[3]=1;packet[4]=x;packet[5]=y;packet[6]=action;
 void (*touch)(void *,int *)=dlsym(0,"_ZN12MsgSenderRSI26updateTouchCoordinateStateEP28_CAN_Touch_Coordinate_STATUS");
 touch(sender,packet);
}
static void *timers(void *unused){
 (void)unused;unsigned long sequence=0;
 for(;;){
  unsigned char info[128]={0};int number=sigwaitinfo(&timer_signals,info);
  void (*handler)(int,void *,void *)=__atomic_load_n(&timer_handler,__ATOMIC_ACQUIRE);
  if(number<0)continue;
  while(!handler){usleep(1000);handler=__atomic_load_n(&timer_handler,__ATOMIC_ACQUIRE);}
  handler(number,info,0);
  if(sequence++%120==0)dprintf(2,"ORIGINAL_INPUT_TIMER_DELIVERED sequence=%lu id=%d overrun=%d\n",sequence,*(int *)(info+24),*(int *)(info+20));
 }
 return 0;
}
static void *hardware(void *unused){
 (void)unused;
 void *method=dlsym(0,"_ZN8TouchMgr11getInstanceEv");
 void (*touch)(void *,int *)=dlsym(0,"_ZN12MsgSenderRSI26updateTouchCoordinateStateEP28_CAN_Touch_Coordinate_STATUS");
 void (*property)(void *,int,int,int,char *,int,int *)=dlsym(0,"_ZN12MsgSenderRSI14updatePropertyEiiiPciP21_CAN_ABT_feature_list");
 if(!method||!touch||!property)return 0;
 uintptr_t base=(uintptr_t)method-0xb8cb8;
 void **instance=(void **)(base+0x19dbb0);
 for(;;){
  if(*instance){void *sender=*(void **)((char *)*instance+8);if(sender&&(*(void ***)sender)[14]==(void *)touch)break;}
  usleep(100000);
 }
 void *sender=*(void **)((char *)*instance+8);
 dprintf(2,"VIRTUAL_TOUCH_PROVIDER_READY original_RSI=1\n");
 int pressed=0,x=0,y=0;unsigned long long previous=0;
 for(;;){
  int fd=open("/tmp/bench-touch-events",0);if(fd<0){usleep(100000);continue;}
  char line[80];unsigned length=0;int invalid=0;char c;
  for(;;){
   struct pollfd input={fd,POLLIN,0};int ready=poll(&input,1,pressed?10:-1);
   if(ready<0)continue;
   if(!ready){if(pressed)contact_frame(sender,254,x,y,&previous);continue;}
   if(read(fd,&c,1)!=1)break;
   if(c!='\n'){if(length+1<sizeof(line)&&c>0&&c<127)line[length++]=c;else invalid=1;continue;}
   line[length]=0;
   if(!invalid){
    char action[16];int nx,ny;
    if(sscanf(line,"%15s %d %d",action,&nx,&ny)==3&&nx>=0&&nx<1920&&ny>=0&&ny<1080){
     int down=!strcmp(action,"down"),move=!strcmp(action,"move"),up=!strcmp(action,"up");
     if((down&&!pressed)||((move||up)&&pressed)){
      contact_frame(sender,down?253:(up?255:254),nx,ny,&previous);pressed=!up;x=nx;y=ny;if(!move)dprintf(2,"VIRTUAL_TOUCH %s %d %d\n",action,x,y);
     }
    }else if(!strcmp(line,"geometry")){
     int feature[18]={0};feature[3]=1920;feature[4]=1080;feature[8]=286;feature[9]=161;feature[10]=174;feature[11]=-1;feature[13]=-1;
     property(sender,0,0,0,0,0,feature);
     dprintf(2,"VIRTUAL_TOUCH_GEOMETRY pixels=1920x1080 mm=286x161 dpi=174\n");
    }
   }
   length=0;invalid=0;
  }
  close(fd);
  if(pressed){contact_frame(sender,255,x,y,&previous);pressed=0;dprintf(2,"VIRTUAL_TOUCH_TRANSPORT_RELEASE\n");}
 }
 return 0;
}
__attribute__((constructor)) static void start(void){
 char path[512];long n=readlink("/proc/self/exe",path,sizeof(path));
 if(n<=0||n>=512)return;path[n]=0;
 if(strcmp(path,"/lge/app_ro/hmi/touch-app")&&strcmp(path,"/vw/oem-touch/touch-app"))return;
 void *method=dlsym(0,"_ZN8TouchMgr11getInstanceEv");if(!method)return;
 original_base=(uintptr_t)method-0xb8cb8;
 sigemptyset(&timer_signals);sigaddset(&timer_signals,__libc_current_sigrtmin());
 if(pthread_sigmask(0,&timer_signals,0))return;
 unsigned long thread;
 if(pthread_create(&thread,0,timers,0))return;
 pthread_detach(thread);
 if(!pthread_create(&thread,0,hardware,0))pthread_detach(thread);
}
