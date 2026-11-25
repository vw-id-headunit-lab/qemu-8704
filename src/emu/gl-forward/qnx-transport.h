#include <stdint.h>
#include <stddef.h>
typedef unsigned int u32;
typedef int i32;
extern void *malloc(size_t), *calloc(size_t,size_t), *realloc(void *,size_t);
extern void free(void *);
extern void abort(void) __attribute__((noreturn));
extern void *memcpy(void *,const void *,size_t), *memmove(void *,const void *,size_t), *memset(void *,int,size_t);
extern int memcmp(const void *,const void *,size_t), strcmp(const char *,const char *);
extern size_t strlen(const char *);
extern long read(int,void *,unsigned long),write(int,const void *,unsigned long);
extern int open(const char *,int,...),close(int),getpid(void), *__get_errno_ptr(void);
extern unsigned pthread_self(void);
extern int pthread_create(unsigned *,const void *,void *(*)(void *),void *),pthread_detach(unsigned);
extern int usleep(unsigned);
extern char *getenv(const char *);
extern int unsetenv(const char *),system(const char *);
extern long lseek(int,long,int);
extern int fcntl(int,int,...);
static _Noreturn void qnx_native_observer(void);
extern int pthread_key_create(unsigned *,void (*)(void *)),pthread_setspecific(unsigned,const void *);
extern void *pthread_getspecific(unsigned);
extern int pthread_mutex_init(void *,const void *),pthread_mutex_lock(void *),pthread_mutex_unlock(void *);
extern int screen_get_window_property_iv(void *,int,int *);
extern int socket(int,int,int),connect(int,const void *,unsigned),setsockopt(int,int,int,const void *,unsigned);
#define errno (*__get_errno_ptr())
#define EINTR 4
extern void (*signal(int,void (*)(int)))(int);
extern int raise(int);
extern int pthread_sigmask(int,const void *,void *),sigemptyset(void *),sigaddset(void *,int),sigismember(const void *,int);
static int qnx_observer_mode,qnx_fault_trace_enabled;
static void (*qnx_previous_segv)(int);
static volatile u32 qnx_last_op,qnx_last_size;
static void qnx_fault_words(const char *tag,const uint64_t *words,unsigned count)
{
 if(!words||((uintptr_t)words&7))return;
 for(unsigned offset=0;offset<count;offset+=4){
  char line[160];unsigned used=0;const char *p=tag;while(*p)line[used++]=*p++;
  for(unsigned k=0;k<5&&offset+k<=count;k++){
   uint64_t v=k?words[offset+k-1]:offset;line[used++]='0';line[used++]='x';char digits[16];unsigned n=0;
   do{unsigned d=v&15;digits[n++]=d<10?'0'+d:'a'+d-10;v>>=4;}while(v);while(n)line[used++]=digits[--n];line[used++]=k==4||offset+k==count?'\n':'|';
  }
  write(2,line,used);
 }
}
static void qnx_fault_handler(int sig,const uint64_t *sinfo,const uint64_t *context)
{
 qnx_fault_words("MEBQNX_FAULT_INFO|",sinfo,6);qnx_fault_words("MEBQNX_FAULT_CONTEXT|",context,48);
 uintptr_t *frame=__builtin_frame_address(0),start=(uintptr_t)frame;
 for(unsigned i=0;i<8;i++){
  uintptr_t values[8]={(unsigned)sig,(unsigned)getpid(),pthread_self(),i,(uintptr_t)&qnx_fault_handler,(uintptr_t)frame,frame[1],((uint64_t)qnx_last_op<<32)|qnx_last_size};
  const char *keys[8]={"sig=","|pid=","|tid=","|frame=","|anchor=","|fp=","|lr=","|last_op_size="};char line[384];unsigned used=0;const char *prefix="MEBQNX_NATIVE_FAULT|";while(*prefix)line[used++]=*prefix++;
  for(unsigned k=0;k<8;k++){prefix=keys[k];while(*prefix)line[used++]=*prefix++;line[used++]='0';line[used++]='x';char digits[2*sizeof(uintptr_t)];unsigned n=0;uintptr_t value=values[k];do{unsigned d=value&15;digits[n++]=d<10?'0'+d:'a'+d-10;value>>=4;}while(value);while(n)line[used++]=digits[--n];}
  line[used++]='\n';write(2,line,used);uintptr_t next=frame[0];if(next<=(uintptr_t)frame||next>start+256*1024||(next&15))break;frame=(uintptr_t *)next;
 }
 signal(sig,qnx_previous_segv);raise(sig);
}
static void qnx_enable_fault_trace(void)
{
 if(qnx_observer_mode||!qnx_fault_trace_enabled)return;
 void (*previous)(int)=signal(11,(void (*)(int))qnx_fault_handler);if(previous!=(void (*)(int))qnx_fault_handler&&previous!=(void (*)(int))-1)qnx_previous_segv=previous;
 unsigned long mask[2]={0,0},observed[2]={0,0};sigemptyset(mask);sigaddset(mask,11);
 int result=pthread_sigmask(1,mask,0);pthread_sigmask(0,0,observed);
 if(result||sigismember(observed,11)){static const char error[]="MEBQNX_FAULT_MASK_UNCHANGED\n";write(2,error,sizeof(error)-1);}
}
static u32 g_id;
void glUniform2iv(i32,i32,const i32 *);
void glUniform3iv(i32,i32,const i32 *);
void glUniform4iv(i32,i32,const i32 *);
struct ClientAttrib { u32 size,type,norm,stride,buffer,enabled;const void *pointer; };
struct ProgramReflection { struct ProgramReflection *next;void *context;u32 program,header[8];unsigned char data[]; };
static volatile u32 reflection_epoch;
struct Record { struct ProgramReflection *reflections;u32 reflection_epoch,reflection_count;unsigned char *data;unsigned size,used,capacity,reply;
 u32 array_buffer,element_buffer;struct ClientAttrib attribs[16];
 uintptr_t callers[64];unsigned caller_count;
 int egl_error;void *egl_display,*egl_context,*egl_draw,*egl_read;
 char gl_strings[5][16385],egl_strings[4][16385]; };
static unsigned record_key;
static unsigned long record_mutex[32];
static int bridge_in=-1,bridge_out=-1;
static uint32_t bridge_owner;
static uint32_t record_thread(void) { return pthread_self(); }
static char qnx_progress[192];
static void qnx_checkpoint(unsigned phase,unsigned error)
{
 char *p=qnx_progress;const char *s="MEBQNX_GL_CORE|pid=";while(*s)*p++=*s++;
 unsigned values[3]={(unsigned)getpid(),phase,error};const char *names[3]={"|phase=","|error=","|end\n"};
 for(unsigned k=0;k<3;k++){char b[20];unsigned n=values[k],i=0;do{b[i++]='0'+n%10;n/=10;}while(n);while(i)*p++=b[--i];s=names[k];while(*s)*p++=*s++;}*p=0;write(2,qnx_progress,p-qnx_progress);
}
static _Noreturn void transport_failed(void)
{
 static const char msg[]="QNX GL-forward: owned IPC failed\n";
 qnx_checkpoint(99,errno);write(2,msg,sizeof(msg)-1);abort();
}
static void release_reflections(struct Record *r)
{struct ProgramReflection *p=r->reflections;while(p){struct ProgramReflection *next=p->next;free(p);p=next;}r->reflections=0;r->reflection_count=0;}
static void release_record(void *p) {struct Record *r=p;if(r){release_reflections(r);free(r->data);free(r);}}
__attribute__((constructor)) static void qnx_transport_init(void)
{if(pthread_key_create(&record_key,release_record)||pthread_mutex_init(record_mutex,0))transport_failed();qnx_checkpoint(1,0);const char *observe=getenv("MEB_QNX_OBSERVE");if(observe&&(!strcmp(observe,"original-process-v1")||!strcmp(observe,"original-control-v1")))qnx_native_observer();const char *trace=getenv("MEB_QNX_FAULT_TRACE");if(trace&&!strcmp(trace,"native-fault-v1")){qnx_fault_trace_enabled=1;qnx_previous_segv=signal(11,(void (*)(int))qnx_fault_handler);}}
static struct Record *current_record(void)
{
 struct Record *r=pthread_getspecific(record_key);
 if(!r){r=calloc(1,sizeof(*r));if(!r||pthread_setspecific(record_key,r))transport_failed();r->egl_error=0x3000;}
 return r;
}
struct QnxDlInfo {const char *fname;void *base;const char *sname;void *saddr;};
extern int dladdr(void *,struct QnxDlInfo *);
static void qnx_trace_callers(uintptr_t *frame)
{
 struct Record *r=current_record();uintptr_t start=(uintptr_t)frame;
 for(unsigned depth=0;depth<8;depth++){
  uintptr_t address=frame[1];unsigned i=0;while(i<r->caller_count&&r->callers[i]!=address)i++;
  if(i==r->caller_count&&r->caller_count<64){
   r->callers[r->caller_count++]=address;struct QnxDlInfo info={0};int found=dladdr((void *)address,&info);
   char line[768];unsigned used=0;const char *s="MEBQNX_NATIVE_CALLER|";while(*s)line[used++]=*s++;
   uintptr_t values[3]={address,(uintptr_t)info.base,(uintptr_t)info.saddr};const char *keys[3]={"pc=","|base=","|symbol="};
   for(unsigned k=0;k<3;k++){s=keys[k];while(*s)line[used++]=*s++;line[used++]='0';line[used++]='x';char digits[16];unsigned n=0;uintptr_t v=values[k];do{unsigned d=v&15;digits[n++]=d<10?'0'+d:'a'+d-10;v>>=4;}while(v);while(n)line[used++]=digits[--n];}
   const char *texts[4]={"|module=",found&&info.fname?info.fname:"?","|name=",found&&info.sname?info.sname:"?"};
   for(unsigned k=0;k<4;k++){s=texts[k];for(unsigned n=0;n<256&&*s&&used<sizeof(line)-2;n++)line[used++]=*s++;}line[used++]='\n';write(2,line,used);
  }
  uintptr_t next=frame[0];if(next<=(uintptr_t)frame||next>start+256*1024||(next&15))break;frame=(uintptr_t *)next;
 }
}
static void io_send(const void *data,unsigned long length)
{
 const unsigned char *p=data;
 while(length){long n=write(bridge_out,p,length);if(n<0&&(errno==EINTR||errno==11)){if(errno==11)usleep(25000);continue;}
  if(n<=0)transport_failed();p+=n;length-=n;}
}
static void configure_owned_socket(int fd)
{
 int one=1;
 if(fcntl(fd,2,1)<0||setsockopt(fd,6,1,&one,sizeof(one)))transport_failed();
}
static void connect_once(void)
{
 char conf[128];int f=open("/proc/boot/meb-qnx-gl.conf",0);
 if(f<0)transport_failed();qnx_checkpoint(2,0);long n=read(f,conf,sizeof(conf)-1);close(f);
 if(n<35||n>100)transport_failed();conf[n]=0;
 unsigned port=0,k=0;while(conf[k]>='0'&&conf[k]<='9'){port=port*10+conf[k++]-'0';if(port>65535)transport_failed();}
 if(!port||conf[k++]!='\n'||strlen(conf+k)!=33||conf[k+32]!='\n')transport_failed();
 unsigned char address[28]={28,24,port>>8,port&255,0,0,0,0,0xfd,0x53,0x7c,0xb8,0x03,0x83,0,3,0,0,0,0,0,0,1,8,0,0,0,0};
 int fd=-1;unsigned failure=0;
 struct {long seconds;int micros;int pad;} timeout={2,0,0};
 for(unsigned attempt=0;attempt<40;attempt++){
  fd=socket(24,1,0);if(fd<0)transport_failed();qnx_checkpoint(3,0);configure_owned_socket(fd);
  if(setsockopt(fd,0xffff,0x1005,&timeout,sizeof(timeout))||setsockopt(fd,0xffff,0x1006,&timeout,sizeof(timeout)))transport_failed();
  if(!connect(fd,address,sizeof(address)))break;
  failure=errno;close(fd);fd=-1;qnx_checkpoint(6,failure);
  if(failure!=249&&failure!=250&&failure!=251&&failure!=260&&failure!=261&&failure!=264&&failure!=265){errno=failure;transport_failed();}
  if(attempt<39)usleep(250000);
 }
 if(fd<0){errno=failure;transport_failed();}
 timeout.seconds=10;
 if(setsockopt(fd,0xffff,0x1005,&timeout,sizeof(timeout))||setsockopt(fd,0xffff,0x1006,&timeout,sizeof(timeout)))transport_failed();
 bridge_in=bridge_out=fd;bridge_owner=getpid();qnx_checkpoint(4,0);
 unsigned char header[48]={ 'Q','N','X','G','L','V','1',0 };
 u32 pid=getpid(),version=1;memcpy(header+8,&pid,4);memcpy(header+12,&version,4);memcpy(header+16,conf+k,32);io_send(header,sizeof(header));qnx_checkpoint(5,0);
}
static void unlock_record(void) {pthread_mutex_unlock(record_mutex);}
static void send_all(const void *data,unsigned long length)
{
 if(bridge_out<0)connect_once();if(bridge_owner!=(uint32_t)getpid())transport_failed();io_send(data,length);
}
static int recv_bytes(void *data,unsigned long length,int release)
{
 unsigned char *p=data;while(length){long n=read(bridge_in,p,length);if(n<0&&(errno==EINTR||errno==11)){if(errno==11)usleep(25000);continue;}
  if(n<=0)transport_failed();p+=n;length-=n;}
 if(release)unlock_record();return 0;
}
static int recv_all(void *data,unsigned long length) {return recv_bytes(data,length,1);}
static unsigned char pending_records[65536];
static unsigned pending_used;
static void flush_records(void) {if(pending_used){send_all(pending_records,pending_used);pending_used=0;}}
static void commit_record(struct Record *r)
{
 pthread_mutex_lock(record_mutex);send_all(0,0);
 if(r->size>sizeof(pending_records)-pending_used)flush_records();
 if(r->size>sizeof(pending_records))send_all(r->data,r->size);
 else{memcpy(pending_records+pending_used,r->data,r->size);pending_used+=r->size;}
 if(r->reply||((u32 *)r->data)[4]==6)flush_records();if(!r->reply)unlock_record();
}
static void rec(u32 op,u32 total)
{
 qnx_enable_fault_trace(); qnx_last_op=op;qnx_last_size=total;struct Record *r=current_record();if(total>16*1024*1024)transport_failed();unsigned size=24+total;
 if(size>r->capacity){r->data=realloc(r->data,size);if(!r->data)transport_failed();r->capacity=size;}
 u32 *h=(u32 *)r->data;h[0]=119;h[1]=8;h[2]=getpid();h[3]=record_thread();h[4]=op;h[5]=total;
 r->size=size;r->used=24;r->reply=(op>=100&&op<=109)||op==120||op==121||op==123||op==124||op==126||op==127||op==131||op==132||op==138||op==140||op==142||op==145||op==146||op==147||op==148||op==162||op==150||op==152||(op>=153&&op<=157)||op==200||op==171||op==172||op==173||(op>=134&&op<=137);
 if(!total)commit_record(r);
}
static void record_part(const void *p,unsigned n)
{if(!n)return;struct Record *r=current_record();if(n>r->size-r->used)transport_failed();memcpy(r->data+r->used,p,n);r->used+=n;if(r->used==r->size)commit_record(r);}
#define PART(p,n) record_part((p),(n))
static void emit_iv(u32 op,const i32 *p,u32 n) {rec(op,n*4);PART(p,n*4);}
static _Noreturn void report_unsupported_native(const char *name)
{
 unsigned n=strlen(name);rec(144,n);PART(name,n);
 pthread_mutex_lock(record_mutex);flush_records();pthread_mutex_unlock(record_mutex);
 write(2,name,n);qnx_checkpoint(98,0);abort();
}

static _Noreturn void report_client_abort(const char *name)
{
 unsigned n=strlen(name);rec(166,n);PART(name,n);
 pthread_mutex_lock(record_mutex);flush_records();pthread_mutex_unlock(record_mutex);
 write(2,name,n);qnx_checkpoint(97,0);abort();
}
static unsigned qnx_display_liveness_started;
static void *qnx_display_liveness(void *unused)
{
 for(;;){
  usleep(1000000);rec(170,0);pthread_mutex_lock(record_mutex);flush_records();pthread_mutex_unlock(record_mutex);
 }
 return 0;
}
static void qnx_start_display_liveness(void)
{
 if(__sync_lock_test_and_set(&qnx_display_liveness_started,1))return;
 unsigned thread;int error=pthread_create(&thread,0,qnx_display_liveness,0);
 if(error){qnx_checkpoint(95,(unsigned)error);__sync_lock_release(&qnx_display_liveness_started);return;}
 error=pthread_detach(thread);if(error)qnx_checkpoint(96,(unsigned)error);
}
static void qnx_observer_text(const void *text,u32 size)
{
 rec(168,size);PART(text,size);pthread_mutex_lock(record_mutex);flush_records();pthread_mutex_unlock(record_mutex);
}
static void *qnx_native_control(void *unused)
{
 char command[2049],text[32768];
 for(;;){
  usleep(1000000);u32 size=0;rec(171,0);recv_bytes(&size,4,0);
  if(size>2048)transport_failed();
  if(size)recv_all(command,size);else unlock_record();
  if(!size)continue;command[size]=0;
  qnx_observer_text("QNX_NATIVE_CONTROL_BEGIN\n",25);
  int result=system(command);qnx_checkpoint(92,(unsigned)result);
  int fd=open("/tmp/meb-qnx-control.txt",0);
  if(fd>=0){long length=lseek(fd,0,2);lseek(fd,length>(long)sizeof(text)?length-(long)sizeof(text):0,0);long n;
   while((n=read(fd,text,sizeof(text)))>0)qnx_observer_text(text,(u32)n);close(fd);}
  qnx_observer_text("QNX_NATIVE_CONTROL_END\n",23);
 }
 return 0;
}
static void *qnx_native_slog(void *unused)
{
 const char *begin="QNX_ORIGINAL_SLOG_BEGIN\n";qnx_observer_text(begin,(u32)strlen(begin));
 const char *command="/proc/boot/ksh -c 'while :; do for f in /dev/shmem/slogger2/MgrTsk.*; do if [ -r \"$f\" ]; then echo QNX_ORIGINAL_SLOG_SOURCE=\"$f\"; exec /proc/boot/slog2info -w -S -b MgrTsk,MgrVCI,MgrComRx,MgrComTx; fi; done; /usr/bin/sleep 1; done' </dev/null >/tmp/meb-native-mgrtsk.slog 2>&1 &";
 int status=system(command);qnx_checkpoint(98,(unsigned)status);
 int fd=-1;while(fd<0){fd=open("/tmp/meb-native-mgrtsk.slog",0);if(fd<0)usleep(100000);}
 char text[2048];for(;;){long n=read(fd,text,sizeof(text));if(n>0)qnx_observer_text(text,(u32)n);else if(!n||errno==EINTR)usleep(100000);else break;}
 close(fd);return 0;
}
static _Noreturn void qnx_native_observer(void)
{
 const char *mode=getenv("MEB_QNX_OBSERVE");int control_only=mode&&!strcmp(mode,"original-control-v1");
 qnx_observer_mode=1; unsetenv("MEB_QNX_OBSERVE");unsetenv("LD_PRELOAD");
 for(unsigned i=0;i<10;i++)usleep(500000);qnx_checkpoint(7,0);
 unsigned control;int control_error=pthread_create(&control,0,qnx_native_control,0);if(control_error)qnx_checkpoint(93,(unsigned)control_error);else pthread_detach(control);
 if(control_only){unsigned logging;int error=pthread_create(&logging,0,qnx_native_slog,0);if(error)qnx_checkpoint(97,(unsigned)error);else pthread_detach(logging);for(;;)usleep(1000000);}
 const char *commands[]={
  "/proc/boot/ksh -c '/proc/boot/cat /tmp/meb-gtf-stderr.txt' >/tmp/meb-qnx-observe.txt 2>&1",
  "/proc/boot/ksh -c '/proc/boot/pidin ar' >/tmp/meb-qnx-observe.txt 2>&1",
  "/proc/boot/ksh -c '/proc/boot/pidin -p GtfStartup channels; /proc/boot/pidin -p GtfStartup fds; /proc/boot/pidin -p GtfStartup timers; /proc/boot/pidin -p GtfStartup threads' >/tmp/meb-qnx-observe.txt 2>&1",
  "/proc/boot/ksh -c '/proc/boot/pidin -p MgrTsk channels; /proc/boot/pidin -p MgrTsk fds; /proc/boot/pidin -p MgrTsk timers; /proc/boot/pidin -p MgrTsk threads; /proc/boot/pidin -p mq threads; /proc/boot/pidin -p io-pkt-v6-hc threads' >/tmp/meb-qnx-observe.txt 2>&1",
  "/proc/boot/ksh -c '/proc/boot/slog2info -b GtfStartup,MgrTsk,wdi,HeadUpDisplay_app -S' >/tmp/meb-qnx-observe.txt 2>&1"
 };
 unsigned iteration=0;char text[32768];
 for(;;){
  for(unsigned kind=0;kind<sizeof(commands)/sizeof(commands[0]);kind++){
   qnx_checkpoint(8,kind);int result=system(commands[kind]);qnx_checkpoint(9,(unsigned)result);char header[128];unsigned n=0;
   const char *prefix="QNX_NATIVE_COMMAND|iteration=";while(*prefix)header[n++]=*prefix++;
   unsigned numbers[3]={iteration,kind,(unsigned)result};const char *keys[3]={"|kind=","|system_status=","|end\n"};
   for(unsigned k=0;k<3;k++){char digits[16];unsigned count=0,value=numbers[k];do{digits[count++]='0'+value%10;value/=10;}while(value);while(count)header[n++]=digits[--count];prefix=keys[k];while(*prefix)header[n++]=*prefix++;}
   qnx_observer_text(header,n);
   int fd=open("/tmp/meb-qnx-observe.txt",0);
   if(fd>=0){long size=lseek(fd,0,2);lseek(fd,size>(long)sizeof(text)?size-(long)sizeof(text):0,0);long got;
    while((got=read(fd,text,sizeof(text)))>0)qnx_observer_text(text,(u32)got);close(fd);}
  }
  iteration++;for(unsigned i=0;i<5;i++)usleep(1000000);
 }
}

#define abort() report_client_abort(__func__)
