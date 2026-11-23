#define _GNU_SOURCE
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <fcntl.h>
#include <unistd.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#define BULK_BYTES (1024*1024)
static volatile uint32_t *control;
static unsigned char *bulk;
static uint32_t reg(unsigned offset){__sync_synchronize();return control[offset/4];}
static void set(unsigned offset,uint32_t value){__sync_synchronize();control[offset/4]=value;__sync_synchronize();}
static uint64_t address(const char *s)
{ char *end;errno=0;uint64_t v=strtoull(s,&end,0);if(errno||*end||v%4096){fputs("Invalid aligned address\n",stderr);exit(64);}return v; }
int main(int argc,char **argv)
{
 if(argc<6){fputs("usage: guest-agent CONTROL BULK LIBRARY -- COMMAND [ARG...]\n",stderr);return 64;}
 uint64_t ca=address(argv[1]),ba=address(argv[2]);
 if(ca!=UINT64_C(0xb7fc00000)||ba!=UINT64_C(0xb7fd00000)||strcmp(argv[4],"--")){fputs("Unverified transport aperture\n",stderr);return 64;}
 int memory=open("/dev/mem",O_RDWR|O_SYNC|O_CLOEXEC);if(memory<0){perror("open /dev/mem");return 1;}
 control=mmap(NULL,0x3000,PROT_READ|PROT_WRITE,MAP_SHARED,memory,ca);
 bulk=mmap(NULL,2*BULK_BYTES,PROT_READ|PROT_WRITE,MAP_SHARED,memory,ba);close(memory);
 if(control==MAP_FAILED||bulk==MAP_FAILED){perror("mmap private transport");return 1;}
 if(reg(0)!=0x474c4252||reg(24)!=BULK_BYTES||reg(48)!=2||!reg(44)){fputs("Private GL transport/provider unavailable\n",stderr);return 1;}
 if(reg(16)){fputs("Private GL transport already owned\n",stderr);return 1;}
 int sockets[2];if(socketpair(AF_UNIX,SOCK_STREAM,0,sockets)){perror("socketpair");set(16,0);return 1;}
 pid_t child=fork();if(child<0){perror("fork");set(16,0);return 1;}
 if(!child){
  close(sockets[0]);char fd[32],owner[32];snprintf(fd,sizeof(fd),"%d",sockets[1]);snprintf(owner,sizeof(owner),"%ld",(long)getpid());
  if(getenv("LD_PRELOAD")){fputs("Existing LD_PRELOAD requires explicit integration\n",stderr);_exit(64);}
  if(setenv("OEM_8704_GL_FORWARD","private-unix-fd-v1",1)||setenv("OEM_8704_GL_FORWARD_FD",fd,1)||setenv("OEM_8704_GL_FORWARD_OWNER",owner,1)||setenv("LD_PRELOAD",argv[3],1)){perror("setenv");_exit(1);}
  execvp(argv[5],argv+5);perror("exec original command");_exit(127);
 }
 close(sockets[1]);int fd=sockets[0],status=0,done=0,failed=0;signal(SIGPIPE,SIG_IGN);
 if(fcntl(fd,F_SETFL,fcntl(fd,F_GETFL)|O_NONBLOCK)<0){perror("fcntl");failed=1;}
 unsigned char *pending=malloc(BULK_BYTES);unsigned count=0,head=0;
 if(!pending)failed=1;
 while(!failed&&!done){
  if(!reg(44)){fputs("Host GL provider disconnected\n",stderr);failed=1;break;}
  if(count){ssize_t n=send(fd,pending+head,count,MSG_NOSIGNAL);if(n>0){head+=n;count-=n;if(!count)head=0;}else if(n<0&&errno!=EINTR&&errno!=EAGAIN&&errno!=EWOULDBLOCK){perror("agent send");failed=1;break;}}
  if(!count){unsigned n=reg(32);if(n>BULK_BYTES){failed=1;break;}if(n){memcpy(pending,bulk+BULK_BYTES,n);set(36,n);count=n;head=0;}}
  unsigned available=reg(40);if(available){unsigned n=available>65536?65536:available;ssize_t got=recv(fd,bulk,n,0);if(got>0)set(28,(unsigned)got);else if(!got)done=1;else if(errno!=EINTR&&errno!=EAGAIN&&errno!=EWOULDBLOCK){perror("agent recv");failed=1;break;}}
  if(!done){struct pollfd watch={fd,(short)((count?POLLOUT:0)|(available?POLLIN:0)),0};if(poll(&watch,1,1)<0&&errno!=EINTR){perror("poll");failed=1;}}
 }
 close(fd);if(failed)kill(child,SIGTERM);
 while(waitpid(child,&status,0)<0)if(errno!=EINTR){failed=1;break;}
 set(16,0);free(pending);munmap((void *)control,0x3000);munmap(bulk,2*BULK_BYTES);
 fprintf(stderr,"GL-forward agent child=%ld status=%d transport_error=%d\n",(long)child,status,failed);
 return failed?1:WIFEXITED(status)?WEXITSTATUS(status):128+WTERMSIG(status);
}
