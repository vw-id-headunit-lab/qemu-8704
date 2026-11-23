#define _GNU_SOURCE
#include <sys/mman.h>
#include <sys/wait.h>
#include <sys/resource.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <errno.h>
#include <stdlib.h>
#include <stdio.h>
#include "mapped-copy.h"
#ifndef USE_MAPPED_COPY
#error USE_MAPPED_COPY must be selected explicitly
#endif
static void *(*volatile ordinary_copy)(void *,const void *,size_t)=memcpy;
static void copy_signal(int number,siginfo_t *info,void *context)
{
 (void)context;
 if(info->si_code==BUS_ADRALN){const char text[]="COPY_SIGNAL=SIGBUS BUS_ADRALN\n";ssize_t ignored=write(STDOUT_FILENO,text,sizeof(text)-1);(void)ignored;}
 else{const char text[]="COPY_SIGNAL=SIGBUS OTHER_CODE\n";ssize_t ignored=write(STDOUT_FILENO,text,sizeof(text)-1);(void)ignored;}
 kill(getpid(),number);
}

int main(int argc,char **argv)
{
 if(argc!=2||strcmp(argv[1],"--private-copy-check")||getuid()!=0)return 64;
 int fd=open("/dev/mem",O_RDWR|O_SYNC|O_CLOEXEC);if(fd<0){perror("open /dev/mem");return 65;}
 volatile uint32_t *control=mmap(NULL,0x3000,PROT_READ|PROT_WRITE,MAP_SHARED,fd,UINT64_C(0xb7fc00000));
 volatile unsigned char *bulk=mmap(NULL,2*1024*1024,PROT_READ|PROT_WRITE,MAP_SHARED,fd,UINT64_C(0xb7fd00000));close(fd);
 if(control==MAP_FAILED||bulk==MAP_FAILED)return 66;
 if(control[0]!=0x474c4252||control[12]!=2||!control[11]||control[8])return 67;
 enum{SPAN=16384+16};volatile unsigned char *scratch=bulk+0x80000;
 unsigned char *pristine=malloc(SPAN),*pattern=malloc(SPAN),*result=malloc(SPAN);if(!pristine||!pattern||!result)return 68;
 mapped_read(pristine,scratch,SPAN);for(unsigned i=0;i<SPAN;i++)pattern[i]=(unsigned char)(i*37+11);
 fflush(NULL);pid_t child=fork();if(child<0)return 69;
 if(!child){
  struct rlimit limit={0,0};if(setrlimit(RLIMIT_CORE,&limit))_exit(70);
  struct sigaction action={.sa_sigaction=copy_signal,.sa_flags=SA_SIGINFO|SA_RESETHAND};
  sigemptyset(&action.sa_mask);if(sigaction(SIGBUS,&action,NULL))_exit(75);
  const unsigned sizes[]={1,4,7,8,9,15,16,17,24,31,32,33,63,64,65,127,128,129,4095,4096,4097,8050,8058,8192,16384};
  const unsigned offsets[]={0,1,7};
  for(unsigned oi=0;oi<3;oi++)for(unsigned si=0;si<sizeof(sizes)/sizeof(sizes[0]);si++){
   unsigned n=sizes[si],offset=offsets[oi];printf("COPY_CASE direction=TX bytes=%u offset=%u safe=%d\n",n,offset,USE_MAPPED_COPY);fflush(stdout);
#if USE_MAPPED_COPY
   mapped_write(scratch+offset,pattern+offset,n);
#else
   ordinary_copy((void *)(scratch+offset),pattern+offset,n);
#endif
   __sync_synchronize();mapped_read(result,scratch+offset,n);if(memcmp(result,pattern+offset,n))_exit(71);
   printf("COPY_CASE direction=RX bytes=%u offset=%u safe=%d\n",n,offset,USE_MAPPED_COPY);fflush(stdout);memset(result,0,n);
#if USE_MAPPED_COPY
   mapped_read(result,scratch+offset,n);
#else
   ordinary_copy(result,(const void *)(scratch+offset),n);
#endif
   if(memcmp(result,pattern+offset,n))_exit(72);
  }
  printf("COPY_CHECK_OK cases=150 safe=%d\n",USE_MAPPED_COPY);fflush(stdout);_exit(0);
 }
 int status;while(waitpid(child,&status,0)<0){if(errno!=EINTR)return 73;}
 mapped_write(scratch,pristine,SPAN);__sync_synchronize();mapped_read(result,scratch,SPAN);if(memcmp(pristine,result,SPAN))return 74;
 printf("COPY_SCRATCH_RESTORED=1\n");int rc=WIFSIGNALED(status)?128+WTERMSIG(status):WEXITSTATUS(status);
 printf("COPY_CHILD_EXIT=%d SIGNAL=%d\n",rc,WIFSIGNALED(status)?WTERMSIG(status):0);free(pristine);free(pattern);free(result);munmap((void *)bulk,2*1024*1024);munmap((void *)control,0x3000);return rc;
}
