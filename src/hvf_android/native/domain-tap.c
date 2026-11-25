#include <stdint.h>
#include <stddef.h>
extern int open(const char *,int,...),close(int),ioctl(int,unsigned long,...),socket(int,int,int);
extern long read(int,void *,size_t),write(int,const void *,size_t);
extern int poll(void *,unsigned long,int),fprintf(void *,const char *,...),*__errno(void);
extern void *stderr;
struct pollfd {int fd;short events,revents;};
static int take(int fd,void *buffer,size_t size){
 char *p=buffer;
 while(size){long n=read(fd,p,size);if(n<0&&*__errno()==4)continue;if(n<=0)return -1;p+=n;size-=n;}
 return 0;
}
static int sendall(int fd,const void *buffer,size_t size){
 const char *p=buffer;
 while(size){long n=write(fd,p,size);if(n<0&&*__errno()==4)continue;if(n<=0)return -1;p+=n;size-=n;}
 return 0;
}
int main(int argc,char **argv,char **environment){
 (void)environment;
 if(argc!=2)return 2;
 int tap=open("/dev/net/tun",2|0x80000);
 if(tap<0){fprintf(stderr,"TAP_OPEN errno=%d\n",*__errno());return 3;}
 unsigned char req[40]={0};
 const char name[]="bench0";
 for(unsigned i=0;i<sizeof(name);i++)req[i]=name[i];
 *(short *)(req+16)=0x1002;
 if(ioctl(tap,0x400454caUL,req)<0){fprintf(stderr,"TUNSETIFF errno=%d\n",*__errno());return 4;}
 int ctl=socket(2,2|0x80000,0);
 if(ctl<0)return 5;
 *(short *)(req+16)=1;
 unsigned char mac[6]={0x52,0x54,0x00,0x87,0x04,0x02};
 for(unsigned i=0;i<6;i++)req[18+i]=mac[i];
 if(ioctl(ctl,0x8924UL,req)<0){fprintf(stderr,"TAP_MAC errno=%d\n",*__errno());return 6;}
 if(ioctl(ctl,0x8913UL,req)<0)return 15;
 *(short *)(req+16)|=1;
 if(ioctl(ctl,0x8914UL,req)<0){fprintf(stderr,"TAP_UP errno=%d\n",*__errno());return 16;}
 close(ctl);
 int serial=open(argv[1],2|0x80000|0x100);
 if(serial<0){fprintf(stderr,"SERIAL_OPEN errno=%d\n",*__errno());return 7;}
 fprintf(stderr,"DOMAIN_TAP_READY bench0 %s\n",argv[1]);
 struct pollfd fds[2]={{tap,1,0},{serial,1,0}};
 unsigned char packet[65536],header[4];
 for(;;){
  int result=poll(fds,2,-1);
  if(result<0){if(*__errno()==4)continue;return 8;}
  if((fds[0].revents|fds[1].revents)&(8|16|32))return 9;
  if(fds[0].revents&1){
   long n=read(tap,packet,sizeof(packet));
   if(n<0&&*__errno()==4)continue;
   if(n<14||n>65535)return 10;
   header[0]=0;header[1]=0;header[2]=(unsigned)n>>8;header[3]=(unsigned)n;
   if(sendall(serial,header,4)||sendall(serial,packet,n))return 11;
  }
  if(fds[1].revents&1){
   if(take(serial,header,4))return 12;
   uint32_t n=(uint32_t)header[0]<<24|(uint32_t)header[1]<<16|(uint32_t)header[2]<<8|header[3];
   if(n<14||n>65535)return 13;
   if(take(serial,packet,n))return 14;
   long sent=write(tap,packet,n);
   if(sent!=(long)n){fprintf(stderr,"TAP_WRITE length=%u result=%ld errno=%d\n",n,sent,*__errno());return 14;}
  }
 }
}

extern void __libc_init(void *,void *,int (*)(int,char **,char **),const void *);
void bench_start(void *args){void *constructors[4]={0};__libc_init(args,0,main,constructors);}
__asm__(".global _start\n_start:\nmov x0,sp\nb bench_start\n");
