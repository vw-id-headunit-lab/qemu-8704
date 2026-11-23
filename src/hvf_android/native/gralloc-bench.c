/* Android gralloc0/fbdev HAL backed by ashmem and a real pixel transport.
 * No screen contents are synthesized: post() sends the composited client buffer. */
#include <stddef.h>
#include <stdint.h>
#include <hardware/gralloc.h>
extern void *calloc(size_t,size_t); extern void free(void*);
extern void *mmap(void*,size_t,int,int,int,long); extern int munmap(void*,size_t);
extern int open(const char*,int,...); extern int close(int); extern long write(int,const void*,size_t);
extern int getpid(void); extern int strcmp(const char*,const char*);
extern int ashmem_create_region(const char*,size_t);
extern int __android_log_print(int,const char*,const char*,...);
extern int *__errno(void); extern int usleep(unsigned);
typedef struct {
    native_handle_t native;
    int fd,magic,width,height,stride,format,size;
} handle_t;
typedef struct map_t { const native_handle_t *key; void *address; size_t size; struct map_t *next; } map_t;
static map_t *maps;
static int maplock;
static void enter(void){while(__atomic_test_and_set(&maplock,__ATOMIC_ACQUIRE))usleep(50);}
static void leave(void){__atomic_clear(&maplock,__ATOMIC_RELEASE);}
static const int width=800,height=480;
static int bytespp(int format) { return format==4?2:format==3?3:4; }
static int register_buffer(const gralloc_module_t *m,buffer_handle_t buffer) {
    (void)m; const handle_t *h=(const handle_t*)buffer;
    if(!h || h->native.version!=12 || h->magic!=0x42454e43 || h->size<=0) return -22;
    enter();for(map_t *r=maps;r;r=r->next) if(r->key==buffer){leave();return 0;}
    void *p=mmap(0,h->size,3,1,h->fd,0); if(p==(void*)-1){leave();return -12;}
    map_t *r=calloc(1,sizeof(*r)); if(!r){munmap(p,h->size);leave();return -12;}
    r->key=buffer;r->address=p;r->size=h->size;r->next=maps;maps=r;leave();return 0;
}
static int unregister_buffer(const gralloc_module_t *m,buffer_handle_t b) {
    (void)m;enter();map_t **r=&maps;while(*r){if((*r)->key==b){map_t *v=*r;*r=v->next;munmap(v->address,v->size);free(v);leave();return 0;}r=&(*r)->next;}leave();return 0;
}
static int lock_buffer(const gralloc_module_t *m,buffer_handle_t b,int usage,int l,int t,int w,int h,void **out) {
    (void)usage;(void)l;(void)t;(void)w;(void)h;int e=register_buffer(m,b);if(e)return e;
    enter();for(map_t *r=maps;r;r=r->next)if(r->key==b){*out=r->address;leave();return 0;}leave();return -22;
}
static int unlock_buffer(const gralloc_module_t *m,buffer_handle_t b){(void)m;(void)b;return 0;}
static int alloc_buffer(alloc_device_t *d,int w,int h,int fmt,int usage,buffer_handle_t *out,int *stride){
    (void)usage;if(w<1||h<1||w>8192||h>8192)return -22;
    handle_t *v=calloc(1,sizeof(*v));if(!v)return -12;
    v->native.version=12;v->native.numFds=1;v->native.numInts=6;
    v->magic=0x42454e43;v->width=w;v->height=h;v->stride=(w+15)&~15;v->format=fmt;
    v->size=(v->stride*h*bytespp(fmt)+4095)&~4095;
    v->fd=ashmem_create_region("bench-gralloc",v->size);if(v->fd<0){free(v);return -12;}
    int e=register_buffer((gralloc_module_t*)d->common.module,(buffer_handle_t)v);
    if(e){close(v->fd);free(v);return e;}*out=(buffer_handle_t)v;*stride=v->stride;return 0;
}
static int free_buffer(alloc_device_t *d,buffer_handle_t b){
    unregister_buffer((gralloc_module_t*)d->common.module,b);close(((handle_t*)b)->fd);free((void*)b);return 0;
}
static int device_close(hw_device_t *d){free(d);return 0;}
static int interval(framebuffer_device_t *d,int n){(void)d;(void)n;return 0;}
static int transport=-1; static unsigned count;
static int sendall(int fd,const void *b,size_t n){const char *p=b;while(n){long k=write(fd,p,n);if(k<=0){if(*__errno()==4)continue;return -1;}p+=k;n-=k;}return 0;}
static int post(framebuffer_device_t *d,buffer_handle_t b){
    void *pixels=0;int e=lock_buffer((gralloc_module_t*)d->common.module,b,3,0,0,width,height,&pixels);if(e)return e;
    const handle_t *h=(const handle_t*)b;
    if(transport<0)transport=open("/dev/bench-display",2|0x80000);
    if(transport>=0){uint32_t header[8]={0x4d524642,h->width,h->height,h->stride,h->format,h->stride*h->height*bytespp(h->format),++count,0};
        if(sendall(transport,header,sizeof(header))||sendall(transport,pixels,header[5])){close(transport);transport=-1;}
        else if(count==1 || count%60==0)__android_log_print(4,"BenchDisplay","posted real frame %u %dx%d format=%d",count,h->width,h->height,h->format);
    }
    unlock_buffer((gralloc_module_t*)d->common.module,b);return 0;
}
static int complete(framebuffer_device_t *d){(void)d;return 0;}
static int device_open(const hw_module_t *m,const char *name,hw_device_t **out){
    if(!strcmp(name,GRALLOC_HARDWARE_GPU0)){
        alloc_device_t *d=calloc(1,sizeof(*d));d->common=(hw_device_t){.tag=HARDWARE_DEVICE_TAG,.version=1,.module=(void*)m,.close=device_close};
        d->alloc=alloc_buffer;d->free=free_buffer;*out=&d->common;return 0;
    }
    if(!strcmp(name,GRALLOC_HARDWARE_FB0)){
        framebuffer_device_t init={.common={.tag=HARDWARE_DEVICE_TAG,.version=0,.module=(void*)m,.close=device_close},
            .width=800,.height=480,.stride=800,.format=1,.xdpi=160,.ydpi=160,.fps=30,.minSwapInterval=1,.maxSwapInterval=1,.numFramebuffers=2,
            .setSwapInterval=interval,.post=post,.compositionComplete=complete};
        framebuffer_device_t *d=calloc(1,sizeof(*d));__builtin_memcpy(d,&init,sizeof(init));*out=&d->common;
        __android_log_print(4,"BenchDisplay","opened real software framebuffer 800x480");return 0;
    }
    return -22;
}
static hw_module_methods_t methods={.open=device_open};
__attribute__((visibility("default"))) gralloc_module_t HMI={
    .common={.tag=HARDWARE_MODULE_TAG,.module_api_version=GRALLOC_MODULE_API_VERSION_0_1,.hal_api_version=0,
        .id=GRALLOC_HARDWARE_MODULE_ID,.name="Bench ashmem pixel transport",.author="bench",.methods=&methods},
    .registerBuffer=register_buffer,.unregisterBuffer=unregister_buffer,.lock=lock_buffer,.unlock=unlock_buffer};
