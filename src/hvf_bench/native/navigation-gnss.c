#include <stdint.h>
#include <stddef.h>
extern void *dlsym(void *,const char *);
extern int pthread_create(unsigned long *,const void *,void *(*)(void *),void *),pthread_detach(unsigned long);
extern int open(const char *,int,...),close(int),usleep(unsigned),dprintf(int,const char *,...);
extern long read(int,void *,size_t),readlink(const char *,char *,size_t);
struct timespec {long tv_sec,tv_nsec;};
struct tm {int sec,min,hour,mday,mon,year,wday,yday,isdst;long gmtoff;const char *zone;};
extern int clock_gettime(int,struct timespec *);
extern struct tm *gmtime_r(const long *,struct tm *);
static void put(unsigned char *p,uint64_t value,unsigned count){for(unsigned i=0;i<count;i++)p[i]=(unsigned char)(value>>(8*i));}
static int load_gnss_frame(unsigned char *packet){
 unsigned char sample[847];int fd=open("/tmp/bench-navigation/gnss-frame.bin",0);
 if(fd<0)return 0;long n=read(fd,sample,sizeof(sample));close(fd);
 if(n!=sizeof(sample))return 0;
 for(unsigned i=0;i<sizeof(sample);i++)packet[i]=sample[i];
 return 1;
}
static void *publish(void *unused){
 (void)unused;
 typedef void (*deliver_fn)(void *,char *,int);
 deliver_fn deliver=(deliver_fn)dlsym((void *)0,"_ZN10AdaptLayer15callStubWithGpsEPci");
 if(!deliver){dprintf(2,"VIRTUAL_GNSS_METHOD_MISSING\n");return (void *)0;}
 uintptr_t base=(uintptr_t)deliver-0x1f030;
 void **adapt=(void **)(base+0xa1618);
 unsigned char packet[847]={0};
 if(!load_gnss_frame(packet)){dprintf(2,"VIRTUAL_GNSS_FRAME_UNAVAILABLE\n");return (void *)0;}
 unsigned long sequence=0;
 for(;;){
  if(*adapt){
   if(!load_gnss_frame(packet)){usleep(200000);continue;}
   struct timespec now,mono;struct tm utc;
   clock_gettime(0,&now);clock_gettime(1,&mono);gmtime_r(&now.tv_sec,&utc);
   uint64_t milliseconds=(uint64_t)now.tv_sec*1000+now.tv_nsec/1000000;
   uint64_t tick=(uint64_t)mono.tv_sec*1000000000+mono.tv_nsec;
   uint64_t source_tick=0;for(unsigned i=0;i<8;i++)source_tick|=(uint64_t)packet[i]<<(8*i);
   if(source_tick&&(source_tick>tick||tick-source_tick>2000000000ULL)){usleep(200000);continue;}
   put(packet,tick,8);put(packet+8,milliseconds,8);
   put(packet+91,utc.year+1900,2);packet[93]=utc.mon+1;packet[94]=utc.mday;
   packet[95]=utc.hour;packet[96]=utc.min;packet[97]=utc.sec;
   put(packet+98,milliseconds,8);put(packet+839,tick,8);
   deliver(*adapt,(char *)packet,sizeof(packet));
   if(sequence++%25==0)dprintf(2,"VIRTUAL_GNSS_DELIVERED latitude_e7=%d longitude_e7=%d bytes=%u\n",(int32_t)((uint32_t)packet[37]|(uint32_t)packet[38]<<8|(uint32_t)packet[39]<<16|(uint32_t)packet[40]<<24),(int32_t)((uint32_t)packet[41]|(uint32_t)packet[42]<<8|(uint32_t)packet[43]<<16|(uint32_t)packet[44]<<24),(unsigned)sizeof(packet));
  }
  usleep(200000);
 }
 return (void *)0;
}
__attribute__((constructor)) static void start(void){
 char path[512];long n=readlink("/proc/self/exe",path,sizeof(path));
 if(n<4||path[n-4]!='/'||path[n-3]!='a'||path[n-2]!='l'||path[n-1]!='m')return;
 unsigned long thread;if(!pthread_create(&thread,(void *)0,publish,(void *)0))pthread_detach(thread);
}

extern int *__errno_location(void);
static int same_path(const char *a,const char *b){while(*a&&*a==*b){a++;b++;}return *a==*b;}
static void *board_open(const char *path,const char *mode,const char *symbol){
 void *(*original)(const char *,const char *)=dlsym((void *)-1,symbol);
 if(!original)return (void *)0;
 void *file=original(path,mode);
 if(!file&&*__errno_location()==2&&mode[0]=='r'){
  const char *source=(void *)0;
  if(same_path(path,"/sys/devices/platform/common_gpio_default/variant_id"))source="/tmp/bench-navigation/variant_id";
  else if(same_path(path,"/sys/devices/platform/common_gpio_default/display_id"))source="/tmp/bench-navigation/display_id";
  if(source){file=original(source,mode);if(file)dprintf(2,"VIRTUAL_BOARD_SOURCE=%s\n",source);}
 }
 return file;
}
__attribute__((visibility("default"))) void *fopen(const char *path,const char *mode){return board_open(path,mode,"fopen");}
__attribute__((visibility("default"))) void *fopen64(const char *path,const char *mode){return board_open(path,mode,"fopen64");}
