#define _GNU_SOURCE
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <time.h>

static volatile sig_atomic_t stopping;
static void stop(int n){(void)n;stopping=1;}
static uint32_t get(uint32_t *p){return __atomic_load_n(p,__ATOMIC_ACQUIRE);}
static void put(uint32_t *p,uint32_t n){__atomic_store_n(p,n,__ATOMIC_RELEASE);}
static int send_line(int fd,const char *action,unsigned x,unsigned y){
 char line[80];int n=snprintf(line,sizeof(line),"%s %u %u\n",action,x,y);
 return write(fd,line,n)==n;
}
int main(void){
 if(getuid())return 64;
 int memory=open("/dev/mem",O_RDWR|O_SYNC|O_CLOEXEC);
 if(memory<0){perror("touch reserved aperture");return 1;}
 uint32_t *box=mmap(0,4096,PROT_READ|PROT_WRITE,MAP_SHARED,memory,UINT64_C(0xb7fc03000));close(memory);
 if(box==MAP_FAILED){perror("touch mmap");return 1;}
 memset(box,0,4096);signal(SIGTERM,stop);signal(SIGINT,stop);signal(SIGPIPE,SIG_IGN);
 int fd=-1;
 for(unsigned i=0;i<6000&&!stopping;i++){
  fd=open("/tmp/bench-touch-events",O_WRONLY|O_NONBLOCK|O_CLOEXEC);
  if(fd>=0)break;
  if(errno!=ENOENT&&errno!=ENXIO){perror("original input FIFO");break;}
  usleep(100000);
 }
 if(fd<0){munmap(box,4096);return 2;}
 if(write(fd,"geometry\n",9)!=9){close(fd);munmap(box,4096);return 3;}
 int random=open("/dev/urandom",O_RDONLY|O_CLOEXEC);
 if(random<0||read(random,box+8,16)!=16){if(random>=0)close(random);close(fd);munmap(box,4096);return 4;}close(random);
 box[1]=1;box[6]=getpid();put(box,0x3154434d);
 fprintf(stderr,"ORIGINAL_TOUCH_RELAY_READY pid=%u ring=128 geometry=1920x1080\n",box[6]);fflush(stderr);
 unsigned pressed=0,x=0,y=0;uint64_t last=0;
 while(!stopping){
  uint32_t read_at=get(box+3),write_at=get(box+2);
  struct timespec now;clock_gettime(CLOCK_MONOTONIC,&now);uint64_t ms=(uint64_t)now.tv_sec*1000+now.tv_nsec/1000000;
  if(write_at-read_at>128){put(box+5,1);break;}
  if(write_at==read_at){
   if(pressed&&ms-last>2000){if(!send_line(fd,"up",x,y)){put(box+5,2);break;}pressed=0;put(box+7,0);fprintf(stderr,"ORIGINAL_TOUCH_IDLE_RELEASE\n");}
   usleep(2000);continue;
  }
  uint32_t item[4];memcpy(item,box+16+(read_at%128)*4,sizeof(item));
  unsigned action=item[0];
  if(item[3]!=read_at+1||action<1||action>3||item[1]>=1920||item[2]>=1080||(action==1&&pressed)||(action!=1&&!pressed)){
   put(box+5,3);break;
  }
  if(!send_line(fd,action==1?"down":action==2?"move":"up",item[1],item[2])){put(box+5,2);break;}
  x=item[1];y=item[2];pressed=action!=3;last=ms;put(box+7,pressed);put(box+4,item[3]);put(box+3,read_at+1);
  if(action!=2){fprintf(stderr,"ORIGINAL_TOUCH_RELAY event=%u x=%u y=%u sequence=%u\n",action,x,y,item[3]);fflush(stderr);}
 }
 if(pressed)send_line(fd,"up",x,y);
 put(box,0);close(fd);munmap(box,4096);return stopping?0:5;
}
