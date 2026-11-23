#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>
#include <errno.h>
#include <pthread.h>
#ifndef GL_FORWARD_FIXTURE_STDIO
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/syscall.h>
#else
#include <windows.h>
#undef near
#undef far
#endif

typedef unsigned int u32;
typedef int i32;
static u32 g_id;
void glUniform2iv(i32,i32,const i32 *);
void glUniform3iv(i32,i32,const i32 *);
void glUniform4iv(i32,i32,const i32 *);

struct ClientAttrib { u32 size,type,norm,stride,buffer,enabled;const void *pointer; };
struct Record { unsigned char *data;unsigned size,used,capacity,reply;
    u32 array_buffer,element_buffer;struct ClientAttrib attribs[16]; };
static pthread_key_t record_key;
static pthread_once_t record_once=PTHREAD_ONCE_INIT;
static pthread_once_t connection_once=PTHREAD_ONCE_INIT;
static pthread_mutex_t record_mutex=PTHREAD_MUTEX_INITIALIZER;
static int bridge_in=-1,bridge_out=-1;
static uint32_t bridge_owner;
static uint32_t record_thread(void)
{
#ifdef GL_FORWARD_FIXTURE_STDIO
    return GetCurrentThreadId();
#else
    return (uint32_t)syscall(SYS_gettid);
#endif
}

static void transport_failed(void)
{
    fputs("GL-forward: owned IPC disconnected or unavailable\n",stderr);
    abort();
}
static void connect_once(void)
{
#ifdef GL_FORWARD_FIXTURE_STDIO
    bridge_in=0;bridge_out=1;
#else
    const char *profile=getenv("OEM_8704_GL_FORWARD");
    if(profile&&!strcmp(profile,"private-hmi-service-v1")){
        const char *path=getenv("OEM_8704_GL_FORWARD_SOCKET");struct stat node;
        if(getuid()!=724||!path||strcmp(path,"/run/oem-gl-forward/ipc/socket")||
           lstat(path,&node)||!S_ISSOCK(node.st_mode)||node.st_uid!=0)transport_failed();
        int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);
        struct sockaddr_un address={.sun_family=AF_UNIX};strcpy(address.sun_path,path);
        if(fd<0||connect(fd,(void *)&address,sizeof(address)))transport_failed();
        struct ucred peer;socklen_t length=sizeof(peer);
        if(getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&length)||peer.uid!=0||peer.pid<=0)transport_failed();
        bridge_in=bridge_out=fd;bridge_owner=(uint32_t)getpid();return;
    }
    const char *value=getenv("OEM_8704_GL_FORWARD_FD"),*owner=getenv("OEM_8704_GL_FORWARD_OWNER");char *end;
    if(!profile||strcmp(profile,"private-unix-fd-v1")||!value||!owner)transport_failed();
    long pid=strtol(owner,&end,10);if(*end||pid!=getpid())transport_failed();
    long fd=strtol(value,&end,10);
    if(*end||fd<3||fd>1048575)transport_failed();
    int domain=0,type=0;socklen_t length=sizeof(int);
    if(getsockopt(fd,SOL_SOCKET,SO_DOMAIN,&domain,&length)||domain!=AF_UNIX)transport_failed();
    length=sizeof(int);
    if(getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&length)||type!=SOCK_STREAM)transport_failed();
    struct ucred peer;length=sizeof(peer);
    if(getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&length)||peer.uid!=getuid())transport_failed();
    bridge_in=bridge_out=(int)fd;
    bridge_owner=(uint32_t)getpid();
#endif
}
static void release_record(void *p)
{ struct Record *r=p;if(r){free(r->data);free(r);} }
static void init_record_key(void)
{ if(pthread_key_create(&record_key,release_record))abort(); }
static struct Record *current_record(void)
{
    pthread_once(&record_once,init_record_key);
    struct Record *r=pthread_getspecific(record_key);
    if(!r){r=calloc(1,sizeof(*r));if(!r||pthread_setspecific(record_key,r))abort();}
    return r;
}
static void unlock_record(void) { pthread_mutex_unlock(&record_mutex); }
static void send_all(const void *data,unsigned long length)
{
    pthread_once(&connection_once,connect_once);
#ifndef GL_FORWARD_FIXTURE_STDIO
    if(bridge_owner!=(uint32_t)getpid())transport_failed();
#endif
    const unsigned char *p=data;
    while(length){long n=write(bridge_out,p,length);
        if(n<0&&errno==EINTR)continue;
        if(n<=0)transport_failed();p+=n;length-=n;}
}
static int recv_bytes(void *data,unsigned long length,int release)
{
    unsigned char *p=data;
    while(length){long n=read(bridge_in,p,length);
        if(n<0&&errno==EINTR)continue;
        if(n<=0)transport_failed();p+=n;length-=n;}
    if(release)unlock_record();return 0;
}
static int recv_all(void *p,unsigned long n) { return recv_bytes(p,n,1); }
static unsigned char pending_records[65536];
static unsigned pending_used;
static void flush_records(void)
{
    if(pending_used){send_all(pending_records,pending_used);pending_used=0;}
}
static void commit_record(struct Record *r)
{
    pthread_mutex_lock(&record_mutex);send_all(NULL,0);
    if(r->size>sizeof(pending_records)-pending_used)flush_records();
    if(r->size>sizeof(pending_records))send_all(r->data,r->size);
    else{memcpy(pending_records+pending_used,r->data,r->size);pending_used+=r->size;}
    if(r->reply||((u32 *)r->data)[4]==6)flush_records();
    if(!r->reply)unlock_record();
}
static void rec(u32 op,u32 total)
{
    struct Record *r=current_record();if(total>16*1024*1024)abort();
    unsigned size=24+total;
    if(size>r->capacity){r->data=realloc(r->data,size);if(!r->data)abort();r->capacity=size;}
    u32 *h=(u32 *)r->data;h[0]=119;h[1]=8;h[2]=getpid();h[3]=record_thread();h[4]=op;h[5]=total;
    r->size=size;r->used=24;r->reply=(op>=100&&op<=109)||op==120||op==121||op==123||op==124||op==126||op==127||op==131||op==132||op==138||op==140||op==142||op==145||op==200||(op>=134&&op<=137);
    if(!total)commit_record(r);
}
static void record_part(const void *p,unsigned n)
{
    if(!n)return;struct Record *r=current_record();
    if(n>r->size-r->used)abort();memcpy(r->data+r->used,p,n);r->used+=n;
    if(r->used==r->size)commit_record(r);
}
#define PART(p,n) record_part((p),(n))
static void emit_iv(u32 op,const i32 *p,u32 n)
{ rec(op,n*4);PART(p,n*4); }

static _Noreturn void report_unsupported_native(const char *name)
{
    fprintf(stderr,"GL-forward unsupported native import: %s\n",name);
    if(bridge_out>=0&&!pthread_mutex_trylock(&record_mutex)){
        size_t n=strlen(name);
        if(n&&n<=128){
            u32 header[6]={119,8,getpid(),record_thread(),144,(u32)n};
            flush_records();send_all(header,sizeof(header));send_all(name,n);
        }
        unlock_record();
    }
    abort();
}
