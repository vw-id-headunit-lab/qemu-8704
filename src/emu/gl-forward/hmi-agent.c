#define _GNU_SOURCE
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <fcntl.h>
#include <unistd.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
#include <elf.h>
#include <ucontext.h>
#include "mapped-copy.h"

#define BULK (1024*1024)
#define MAX_RECORD (16*1024*1024)
static const char endpoint[]="/run/oem-gl-forward/ipc/socket";
static volatile uint32_t *control;
static unsigned char *bulk;
static int listener=-1,client=-1;
static pid_t owner;
static atomic_int stopping,failed;
static _Thread_local unsigned copy_phase,copy_count;
static _Thread_local uintptr_t copy_base;
static char *fault_field(char *p,const char *name,uintptr_t value)
{
 *p++=' ';while(*name)*p++=*name++;*p++='=';*p++='0';*p++='x';
 for(int shift=(int)(sizeof(value)*8)-4;shift>=0;shift-=4)*p++="0123456789abcdef"[(value>>shift)&15];
 return p;
}
static void mapped_fault(int number,siginfo_t *info,void *context)
{
 char buffer[256],*p=buffer;const char *prefix="GRAPHICS_AGENT_SIGBUS";
 while(*prefix)*p++=*prefix++;
 p=fault_field(p,"code",(uintptr_t)info->si_code);p=fault_field(p,"address",(uintptr_t)info->si_addr);
 p=fault_field(p,"pc",(uintptr_t)((ucontext_t *)context)->uc_mcontext.pc);
 p=fault_field(p,"phase",copy_phase);p=fault_field(p,"bytes",copy_count);p=fault_field(p,"base",copy_base);*p++='\n';
 ssize_t ignored=write(STDERR_FILENO,buffer,(size_t)(p-buffer));(void)ignored;
 kill(getpid(),number);
}

static uint32_t reg(unsigned offset){__sync_synchronize();return control[offset/4];}
static void set(unsigned offset,uint32_t value){__sync_synchronize();control[offset/4]=value;__sync_synchronize();}
static void stop(int signal_number)
{
 (void)signal_number;atomic_store(&stopping,1);
 if(client>=0)shutdown(client,SHUT_RDWR);
 if(listener>=0)shutdown(listener,SHUT_RDWR);
}
static int exact(void *data,size_t count)
{
 unsigned char *p=data;
 while(count&&!atomic_load(&stopping)){
  ssize_t n=recv(client,p,count,0);
  if(n<0&&errno==EINTR)continue;if(n<=0)return 0;p+=n;count-=n;
 }
 return !count;
}
static int transmit(const unsigned char *p,size_t count)
{
 while(count&&!atomic_load(&stopping)){
  if(!reg(44))return 0;
  unsigned available=reg(40),n=count>BULK?BULK:(unsigned)count;
  if(!available){usleep(1000);continue;}if(n>available)n=available;
  copy_phase=1;copy_count=n;copy_base=(uintptr_t)bulk;mapped_write(bulk,p,n);copy_phase=0;set(28,n);p+=n;count-=n;
 }
 return !count;
}
static int valid_record(const uint32_t *header)
{
 return header[0]==119&&header[1]==8&&header[2]==(uint32_t)owner&&header[3]&&
        header[3]<=INT_MAX&&header[4]!=119&&header[5]<=MAX_RECORD;
}
static int append_available_records(unsigned char *record,unsigned *length,unsigned capacity,int *rejected)
{
 ssize_t available;
 do{available=recv(client,record+*length,capacity-*length,MSG_PEEK|MSG_DONTWAIT);}
 while(available<0&&errno==EINTR&&!atomic_load(&stopping));
 if(available<=0)return 1;
 unsigned used=0;
 while((unsigned)available-used>=24){
  uint32_t header[6];memcpy(header,record+*length+used,24);
  if(!valid_record(header)){*rejected=1;break;}
  unsigned n=24+header[5];if(n>(unsigned)available-used)break;
  used+=n;
 }
 unsigned received=0;
 while(received<used&&!atomic_load(&stopping)){
  ssize_t n=recv(client,record+*length+received,used-received,0);
  if(n<0&&errno==EINTR)continue;if(n<=0)break;received+=(unsigned)n;
 }
 if(received!=used){
  unsigned complete=0;*rejected=0;
  while(received-complete>=24){
   uint32_t header[6];memcpy(header,record+*length+complete,24);
   unsigned n=24+header[5];if(n>received-complete)break;complete+=n;
  }
  *length+=complete;return 0;
 }
 *length+=used;return 1;
}
static void *uplink(void *unused)
{
 (void)unused;
 while(!atomic_load(&stopping)){
  uint32_t header[6];if(!exact(header,sizeof(header)))break;
  if(!valid_record(header)){
   fputs("Rejected original HMI record identity or length\n",stderr);atomic_store(&failed,1);break;
  }
  unsigned n=24+header[5],capacity=n<65536?65536:n;
  unsigned char *record=malloc(capacity);
  if(!record){atomic_store(&failed,1);break;}
  memcpy(record,header,24);int rejected=0;
  int okay=exact(record+24,header[5]);
  if(okay){
   int received=n<capacity?append_available_records(record,&n,capacity,&rejected):1;
   okay=transmit(record,n)&&received;
  }
  free(record);
  if(rejected){fputs("Rejected original HMI record identity or length\n",stderr);atomic_store(&failed,1);break;}
  if(!okay)break;
 }
 stop(0);return NULL;
}
static int notify_ready(void)
{
 const char *name=getenv("NOTIFY_SOCKET");if(!name)return 1;
 size_t n=strlen(name);struct sockaddr_un address={.sun_family=AF_UNIX};
 if(!n||n>=sizeof(address.sun_path))return 0;
 memcpy(address.sun_path,name,n+1);int abstract=name[0]=='@';if(abstract)address.sun_path[0]=0;
 int fd=socket(AF_UNIX,SOCK_DGRAM|SOCK_CLOEXEC,0);if(fd<0)return 0;
 const char message[]="READY=1\nSTATUS=Private graphics endpoint and host transport ready";
 ssize_t sent=sendto(fd,message,sizeof(message)-1,MSG_NOSIGNAL,(void *)&address,
  (socklen_t)(offsetof(struct sockaddr_un,sun_path)+n+!abstract));close(fd);return sent==(ssize_t)sizeof(message)-1;
}
static int original_peer(void)
{
 struct ucred peer;socklen_t size=sizeof(peer);
 if(getsockopt(client,SOL_SOCKET,SO_PEERCRED,&peer,&size)||peer.uid!=724||peer.pid<=0)return 0;
 char path[96],actual[PATH_MAX],expected[PATH_MAX];
 snprintf(path,sizeof(path),"/proc/%ld/exe",(long)peer.pid);
 ssize_t n=readlink(path,actual,sizeof(actual)-1);if(n<=0)return 0;actual[n]=0;
 int accepted=realpath("/vw/oem-gl-forward/jvm/bin/java",expected)&&!strcmp(actual,expected);
 if(!accepted)accepted=realpath("/lge/hmi/jvm/bin/java",expected)&&!strcmp(actual,expected);
 if(!accepted)return 0;
 snprintf(path,sizeof(path),"/proc/%ld/auxv",(long)peer.pid);int aux=open(path,O_RDONLY|O_CLOEXEC);
 if(aux<0){perror("read owned HMI auxiliary vector");return 0;}
 uint64_t pair[2],secure=UINT64_MAX;
 for(unsigned i=0;i<64&&read(aux,pair,sizeof(pair))==sizeof(pair)&&pair[0];i++)if(pair[0]==AT_SECURE)secure=pair[1];
 close(aux);if(secure==UINT64_MAX)return 0;
 owner=peer.pid;fprintf(stderr,"ORIGINAL_HMI_PEER pid=%ld uid=%u exe=%s AT_SECURE=%llu\n",(long)owner,peer.uid,actual,(unsigned long long)secure);fflush(stderr);return 1;
}
int main(void)
{
 if(getuid()!=0){fputs("Graphics hardware service requires its original root service identity\n",stderr);return 64;}
 struct sigaction action={.sa_sigaction=mapped_fault,.sa_flags=SA_SIGINFO|SA_RESETHAND};
 sigemptyset(&action.sa_mask);if(sigaction(SIGBUS,&action,NULL)){perror("SIGBUS diagnostic");return 1;}
 int memory=open("/dev/mem",O_RDWR|O_SYNC|O_CLOEXEC);if(memory<0){perror("open /dev/mem");return 1;}
 control=mmap(NULL,0x3000,PROT_READ|PROT_WRITE,MAP_SHARED,memory,UINT64_C(0xb7fc00000));
 bulk=mmap(NULL,2*BULK,PROT_READ|PROT_WRITE,MAP_SHARED,memory,UINT64_C(0xb7fd00000));close(memory);
 if(control==MAP_FAILED||bulk==MAP_FAILED){perror("mmap private graphics transport");return 1;}
 if(reg(0)!=0x474c4252||reg(24)!=BULK||reg(48)!=2||!reg(44)){fputs("Real graphics transport not available\n",stderr);return 1;}
 if(reg(16)){fputs("Graphics transport already owned\n",stderr);return 1;}
 struct stat previous;int created=0,result=1;
 if(!lstat(endpoint,&previous)||errno!=ENOENT){fputs("Graphics endpoint already exists or cannot be inspected\n",stderr);goto finish;}
 listener=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);if(listener<0){perror("socket");goto finish;}
 struct sockaddr_un address={.sun_family=AF_UNIX};memcpy(address.sun_path,endpoint,sizeof(endpoint));
 if(bind(listener,(void *)&address,sizeof(address))){perror("bind owned graphics endpoint");goto finish;}created=1;
 if(chown(endpoint,0,44)||chmod(endpoint,0660)||listen(listener,1)){perror("prepare owned graphics endpoint");goto finish;}
 signal(SIGPIPE,SIG_IGN);signal(SIGTERM,stop);signal(SIGINT,stop);
 if(!notify_ready()){perror("notify graphics readiness");goto finish;}
 fputs("GRAPHICS_SERVICE_READY transport=MMIO-v2 allowed_uid=724\n",stderr);fflush(stderr);
 while(!atomic_load(&stopping)){
  client=accept4(listener,NULL,NULL,SOCK_CLOEXEC);
  if(client<0){if(errno==EINTR)continue;break;}
  if(original_peer())break;
  fputs("Rejected graphics peer identity\n",stderr);close(client);client=-1;
 }
 if(client<0)goto finish;
 pthread_t sender;if(pthread_create(&sender,NULL,uplink,NULL)){perror("pthread_create");goto finish;}
 unsigned char *reply=malloc(BULK);if(!reply){atomic_store(&failed,1);stop(0);}
 while(reply&&!atomic_load(&stopping)){
  if(!reg(44)){fputs("Graphics host disconnected\n",stderr);atomic_store(&failed,1);break;}
  unsigned n=reg(32);if(n>BULK){atomic_store(&failed,1);break;}
  if(!n){usleep(1000);continue;}
  copy_phase=2;copy_count=n;copy_base=(uintptr_t)(bulk+BULK);mapped_read(reply,bulk+BULK,n);copy_phase=0;set(36,n);unsigned done=0;
  while(done<n&&!atomic_load(&stopping)){
   ssize_t sent=send(client,reply+done,n-done,MSG_NOSIGNAL);
   if(sent<0&&errno==EINTR)continue;
   if(sent<=0){atomic_store(&failed,1);stop(0);break;}done+=sent;
  }
 }
 free(reply);stop(0);pthread_join(sender,NULL);result=atomic_load(&failed)?1:0;
finish:
 if(client>=0)close(client);if(listener>=0)close(listener);if(created)unlink(endpoint);
 set(16,0);munmap((void *)control,0x3000);munmap(bulk,2*BULK);
 fprintf(stderr,"GRAPHICS_SERVICE_EXIT=%d\n",result);return result;
}
