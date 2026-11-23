#include "egl-wire.h"

enum ME_Kind { ME_DISPLAY=1,ME_CONFIG,ME_CONTEXT,ME_SURFACE,ME_SYNC,ME_IMAGE };
struct ME_Handle { uint32_t owner,kind;void *native;EGLDisplay display;
 EGLConfig config;EGLContext capture;int window,initialized,pending; };
struct ME_Thread { uint32_t owner,tid;EGLenum api;EGLDisplay display;
 EGLContext context;EGLSurface draw,read; };
struct ME_Owner { uint32_t pid;GLuint *objects;struct BlendMetadata *blend; };
static struct ME_Handle me_handles[4096];
static struct ME_Thread me_threads[128];
static struct ME_Owner me_owners[32];
static unsigned me_handle_count,me_thread_count,me_owner_count;
static int me_write_all(const void *p,size_t length)
{
 const unsigned char *b=p;
 while(length){ssize_t n=write(STDOUT_FILENO,b,length);
  if(n<0&&errno==EINTR)continue;if(n<=0)return 0;b+=n;length-=n;}
 return 1;
}
static struct ME_Owner *me_owner(uint32_t pid,int create)
{
 for(unsigned i=0;i<me_owner_count;i++)if(me_owners[i].pid==pid)return &me_owners[i];
 if(!create||!pid||me_owner_count==32)return NULL;
 struct ME_Owner *o=&me_owners[me_owner_count];
 o->objects=calloc(MAXOBJ,sizeof(GLuint));o->blend=calloc(1,sizeof(*o->blend));
 if(!o->objects||!o->blend)return NULL;o->pid=pid;me_owner_count++;return o;
}
static struct ME_Thread *me_thread(uint32_t pid,uint32_t tid,int create)
{
 for(unsigned i=0;i<me_thread_count;i++)if(me_threads[i].owner==pid&&me_threads[i].tid==tid)return &me_threads[i];
 if(!create||me_thread_count==128)return NULL;
 struct ME_Thread *t=&me_threads[me_thread_count++];t->owner=pid;t->tid=tid;t->api=EGL_OPENGL_ES_API;return t;
}
static struct ME_Handle *me_get(uint32_t owner,uint64_t token,unsigned kind)
{
 if(!token||token>me_handle_count)return NULL;
 struct ME_Handle *h=&me_handles[token-1];
 return h->owner==owner&&h->kind==kind&&h->native&&!h->pending?h:NULL;
}
static uint64_t me_put(uint32_t owner,unsigned kind,void *native,EGLDisplay dpy,EGLConfig cfg,int window)
{
 if(!native)return 0;
 for(unsigned i=0;i<me_handle_count;i++)
  if(me_handles[i].owner==owner&&me_handles[i].kind==kind&&me_handles[i].native==native&&!me_handles[i].pending)return i+1;
 if(me_handle_count==4096)return 0;
 struct ME_Handle *h=&me_handles[me_handle_count++];h->owner=owner;h->kind=kind;
 h->native=native;h->display=dpy;h->config=cfg;h->window=window;return me_handle_count;
}
static int me_bind(struct ME_Thread *t)
{
 if(!t||!t->context||!t->display)return 0;
 return eglBindAPI(t->api)&&eglMakeCurrent(t->display,t->draw,t->read,t->context);
}
static void me_collect(uint32_t pid)
{
 for(unsigned i=0;i<me_handle_count;i++){
  struct ME_Handle *h=&me_handles[i];if(h->owner!=pid||!h->native||!h->pending)continue;
  int used=0;
  for(unsigned j=0;j<me_thread_count;j++){
   struct ME_Thread *t=&me_threads[j];if(t->owner!=pid)continue;
   if((h->kind==ME_CONTEXT&&t->context==h->native)||
      (h->kind==ME_SURFACE&&(t->draw==h->native||t->read==h->native)))used=1;
  }
  if(used)continue;
  if(h->kind==ME_CONTEXT)eglDestroyContext(h->display,h->native);
  if(h->kind==ME_SURFACE){if(h->capture)eglDestroyContext(h->display,h->capture);eglDestroySurface(h->display,h->native);}
  h->capture=NULL;h->native=NULL;
 }
}
static void me_terminate_owner(uint32_t pid,EGLDisplay dpy)
{
 EGLContext actual=eglGetCurrentContext();int unbind=0;
 for(unsigned i=0;i<me_thread_count;i++){
  struct ME_Thread *t=&me_threads[i];if(t->owner!=pid||t->display!=dpy)continue;
  if(t->context&&t->context==actual)unbind=1;
  t->display=NULL;t->context=NULL;t->draw=t->read=NULL;
 }
 if(unbind)eglMakeCurrent(dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
 for(unsigned i=0;i<me_handle_count;i++){
  struct ME_Handle *h=&me_handles[i];if(h->owner!=pid||h->display!=dpy||h->kind==ME_DISPLAY||!h->native)continue;
  if(h->kind==ME_SYNC)eglDestroySync(dpy,h->native);
  if(h->kind==ME_IMAGE)eglDestroyImage(dpy,h->native);
  if(h->kind==ME_CONTEXT||h->kind==ME_SURFACE)h->pending=1;else h->native=NULL;
 }
 me_collect(pid);
}
static int me_select(uint32_t pid,uint32_t tid)
{
 struct ME_Owner *o=me_owner(pid,0);struct ME_Thread *t=me_thread(pid,tid,0);
 if(!o||!me_bind(t))return 0;
 gmap=o->objects;blend=o->blend;current_pid=pid;display=t->display;surface=t->draw;
 EGLint w=0,h=0;
 win=t->draw&&eglQuerySurface(display,surface,EGL_WIDTH,&w)&&eglQuerySurface(display,surface,EGL_HEIGHT,&h);
 win_w=w;win_h=h;return 1;
}
static int me_capture(uint32_t pid,uint32_t tid,struct ME_Handle *h)
{
 struct ME_Thread *t=me_thread(pid,tid,0);
 if(!t||t->draw!=h->native||!h->window)return 1;
 EGLint width=0,height=0,major=2;
 if(!eglQuerySurface(h->display,h->native,EGL_WIDTH,&width)||
    !eglQuerySurface(h->display,h->native,EGL_HEIGHT,&height)||width!=panel_w||height!=panel_h)return 0;
 if(!h->capture){
  if(!eglQueryContext(t->display,t->context,EGL_CONTEXT_CLIENT_VERSION,&major))return 0;
  EGLint attrs[]={EGL_CONTEXT_CLIENT_VERSION,major,EGL_NONE};
  h->capture=eglCreateContext(h->display,h->config,EGL_NO_CONTEXT,attrs);
  if(!h->capture)return 0;
 }
 if(!eglMakeCurrent(h->display,h->native,h->native,h->capture))return 0;
 display=h->display;surface=h->native;win=1;win_w=width;win_h=height;
 dump_frame(frame_path());
 return me_bind(t);
}
static int me_egl_record_valid(uint32_t len,const uint8_t *p)
{
 if(len<72)return 0;struct ME_EGLRequest q;memcpy(&q,p,72);
 if(q.operation<ME_GET_DISPLAY||q.operation>ME_DESTROY_IMAGE||q.attribute_count>ME_EGL_MAX_ATTRS)return 0;
 if(len!=72+q.attribute_count*8)return 0;
 if(q.attribute_count&&(!(q.attribute_count&1)))return 0;
 if(q.attribute_count){int64_t last;memcpy(&last,p+len-8,8);if(last!=EGL_NONE)return 0;}
 return 1;
}
static int me_egl_dispatch(uint32_t pid,uint32_t tid,const uint8_t *p,uint32_t length)
{
 struct ME_EGLRequest q;memcpy(&q,p,72);uint64_t *a=q.arg;
 struct ME_EGLReply reply={0,EGL_SUCCESS,0,0,0};
 uint64_t ids[ME_EGL_MAX_CONFIGS];const void *extra=NULL;
 EGLint attrs[ME_EGL_MAX_ATTRS+4];EGLAttrib wide[ME_EGL_MAX_ATTRS];
 for(unsigned i=0;i<q.attribute_count;i++){
  int64_t value;memcpy(&value,p+72+i*8,8);wide[i]=(EGLAttrib)value;
  if(value<INT32_MIN||value>INT32_MAX){reply.error=EGL_BAD_ATTRIBUTE;goto send;}
  attrs[i]=(EGLint)value;
 }
 const EGLint *ap=q.attribute_count?attrs:NULL;
 struct ME_Owner *owner=me_owner(pid,1);struct ME_Thread *thread=me_thread(pid,tid,1);
 if(!owner||!thread){reply.error=EGL_BAD_ALLOC;goto send;}
 eglGetError();
 if(!eglBindAPI(thread->api)){reply.error=eglGetError();goto send;}
 if(q.operation==ME_GET_DISPLAY){
  const EGLAttrib platform[]={EGL_PLATFORM_ANGLE_TYPE_ANGLE,EGL_PLATFORM_ANGLE_TYPE_METAL_ANGLE,EGL_NONE};
  EGLDisplay dpy=eglGetPlatformDisplay(EGL_PLATFORM_ANGLE_ANGLE,NULL,platform);
  reply.value=me_put(pid,ME_DISPLAY,dpy,dpy,NULL,0);reply.success=reply.value!=0;
  if(dpy&&!reply.value)reply.error=EGL_BAD_ALLOC;goto result;
 }
 if(q.operation==ME_BIND_API){reply.success=eglBindAPI((EGLenum)a[0]);if(reply.success)thread->api=(EGLenum)a[0];goto result;}
 if(q.operation==ME_QUERY_API){reply.success=1;reply.value=eglQueryAPI();goto result;}
 if(q.operation==ME_RELEASE_THREAD){reply.success=eglReleaseThread();if(reply.success){thread->context=NULL;thread->draw=thread->read=NULL;thread->display=NULL;thread->api=EGL_OPENGL_ES_API;me_collect(pid);}goto result;}
 if(q.operation==ME_WAIT_CLIENT||q.operation==ME_WAIT_GL||q.operation==ME_WAIT_NATIVE){
  if(thread->context&&!me_bind(thread)){reply.error=eglGetError();goto send;}
  reply.success=q.operation==ME_WAIT_CLIENT?eglWaitClient():q.operation==ME_WAIT_GL?eglWaitGL():eglWaitNative((EGLint)a[0]);goto result;
 }
 struct ME_Handle *dh=me_get(pid,a[0],ME_DISPLAY);
 if(!dh){
  if(q.operation==ME_QUERY_STRING&&!a[0]){const char *s=eglQueryString(EGL_NO_DISPLAY,(EGLint)a[1]);if(s){reply.success=1;reply.bytes=(uint32_t)strlen(s);extra=s;}goto result;}
  reply.error=EGL_BAD_DISPLAY;goto send;
 }
 EGLDisplay dpy=dh->native;
 if(q.operation==ME_INITIALIZE){EGLint major=0,minor=0;reply.success=eglInitialize(dpy,&major,&minor);if(reply.success)dh->initialized=1;reply.value=(uint32_t)major|((uint64_t)(uint32_t)minor<<32);goto result;}
 if(q.operation==ME_TERMINATE){if(dh->initialized)me_terminate_owner(pid,dpy);dh->initialized=0;reply.success=1;goto result;}
 if(!dh->initialized){reply.error=EGL_NOT_INITIALIZED;goto send;}
 if(q.operation==ME_QUERY_STRING){const char *s=eglQueryString(dpy,(EGLint)a[1]);if(s){reply.success=1;reply.bytes=(uint32_t)strlen(s);extra=s;}goto result;}
 if(q.operation==ME_GET_CONFIGS||q.operation==ME_CHOOSE_CONFIG){
  EGLConfig configs[ME_EGL_MAX_CONFIGS];EGLint count=0;
  if((int64_t)a[1]<0){reply.error=EGL_BAD_PARAMETER;goto send;}
  unsigned wanted=a[2]?(unsigned)(a[1]>ME_EGL_MAX_CONFIGS?ME_EGL_MAX_CONFIGS:a[1]):0;
  reply.success=q.operation==ME_GET_CONFIGS?eglGetConfigs(dpy,wanted?configs:NULL,wanted,&count):eglChooseConfig(dpy,ap,wanted?configs:NULL,wanted,&count);
  if(reply.success){reply.value=count;reply.count=wanted?(unsigned)(count<(EGLint)wanted?count:(EGLint)wanted):0;
   for(unsigned i=0;i<reply.count;i++){ids[i]=me_put(pid,ME_CONFIG,configs[i],dpy,configs[i],0);if(!ids[i]){reply.success=0;reply.error=EGL_BAD_ALLOC;break;}}
   reply.bytes=reply.count*8;extra=ids;}goto result;
 }
 if(q.operation==ME_CONFIG_ATTRIB){struct ME_Handle *h=me_get(pid,a[1],ME_CONFIG);EGLint value=0;if(!h){reply.error=EGL_BAD_CONFIG;goto send;}reply.success=eglGetConfigAttrib(dpy,h->native,(EGLint)a[2],&value);reply.value=(uint64_t)(int64_t)value;goto result;}
 if(q.operation==ME_CREATE_CONTEXT){
  struct ME_Handle *cfg=me_get(pid,a[1],ME_CONFIG),*share=me_get(pid,a[2],ME_CONTEXT);
  if(a[1]&&!cfg){reply.error=EGL_BAD_CONFIG;goto send;}if(a[2]&&!share){reply.error=EGL_BAD_CONTEXT;goto send;}
  EGLContext ctx=eglCreateContext(dpy,cfg?cfg->native:EGL_NO_CONFIG_KHR,share?share->native:EGL_NO_CONTEXT,ap);
  reply.value=me_put(pid,ME_CONTEXT,ctx,dpy,cfg?cfg->native:NULL,0);reply.success=reply.value!=0;if(ctx&&!reply.value)reply.error=EGL_BAD_ALLOC;goto result;
 }
 if(q.operation==ME_DESTROY_CONTEXT){struct ME_Handle *h=me_get(pid,a[1],ME_CONTEXT);if(!h){reply.error=EGL_BAD_CONTEXT;goto send;}h->pending=1;me_collect(pid);reply.success=1;goto result;}
 if(q.operation==ME_CREATE_WINDOW||q.operation==ME_CREATE_PBUFFER){
  struct ME_Handle *cfg=me_get(pid,a[1],ME_CONFIG);if(!cfg){reply.error=EGL_BAD_CONFIG;goto send;}
  if(q.operation==ME_CREATE_WINDOW){
   if(!a[2]){reply.error=EGL_BAD_NATIVE_WINDOW;goto send;}
   unsigned n=q.attribute_count?q.attribute_count-1:0;attrs[n++]=EGL_WIDTH;attrs[n++]=panel_w;attrs[n++]=EGL_HEIGHT;attrs[n++]=panel_h;attrs[n]=EGL_NONE;ap=attrs;
  }
  EGLSurface surf=eglCreatePbufferSurface(dpy,cfg->native,ap);
  reply.value=me_put(pid,ME_SURFACE,surf,dpy,cfg->native,q.operation==ME_CREATE_WINDOW);reply.success=reply.value!=0;if(surf&&!reply.value)reply.error=EGL_BAD_ALLOC;goto result;
 }
 if(q.operation==ME_DESTROY_SURFACE){struct ME_Handle *h=me_get(pid,a[1],ME_SURFACE);if(!h){reply.error=EGL_BAD_SURFACE;goto send;}h->pending=1;me_collect(pid);reply.success=1;goto result;}
 if(q.operation==ME_MAKE_CURRENT){
  struct ME_Handle *draw=me_get(pid,a[1],ME_SURFACE),*read=me_get(pid,a[2],ME_SURFACE),*ctx=me_get(pid,a[3],ME_CONTEXT);
  if((a[1]&&!draw)||(a[2]&&!read)){reply.error=EGL_BAD_SURFACE;goto send;}if(a[3]&&!ctx){reply.error=EGL_BAD_CONTEXT;goto send;}
  for(unsigned i=0;i<me_thread_count;i++)if(&me_threads[i]!=thread){
   struct ME_Thread *other=&me_threads[i];
   if((ctx&&other->context==ctx->native)||(draw&&(other->draw==draw->native||other->read==draw->native))||
      (read&&(other->draw==read->native||other->read==read->native))){reply.error=EGL_BAD_ACCESS;goto send;}
  }
  reply.success=eglMakeCurrent(dpy,draw?draw->native:EGL_NO_SURFACE,read?read->native:EGL_NO_SURFACE,ctx?ctx->native:EGL_NO_CONTEXT);
  if(reply.success){thread->display=dpy;thread->draw=draw?draw->native:NULL;thread->read=read?read->native:NULL;thread->context=ctx?ctx->native:NULL;me_collect(pid);}goto result;
 }
 if(q.operation==ME_SWAP||q.operation==ME_QUERY_SURFACE||q.operation==ME_SURFACE_ATTRIB||q.operation==ME_BIND_TEX||q.operation==ME_RELEASE_TEX){
  struct ME_Handle *h=me_get(pid,a[1],ME_SURFACE);EGLint value=0;if(!h){reply.error=EGL_BAD_SURFACE;goto send;}
  if(thread->context&&!me_bind(thread)){reply.error=eglGetError();goto send;}
  if(q.operation==ME_SWAP){reply.success=eglSwapBuffers(dpy,h->native);reply.error=eglGetError();if(reply.success&&!me_capture(pid,tid,h))fprintf(stderr,"EGL: capture unavailable for owner %u\n",pid);goto send;}
  if(q.operation==ME_QUERY_SURFACE){reply.success=eglQuerySurface(dpy,h->native,(EGLint)a[2],&value);reply.value=(uint64_t)(int64_t)value;}
  if(q.operation==ME_SURFACE_ATTRIB)reply.success=eglSurfaceAttrib(dpy,h->native,(EGLint)a[2],(EGLint)a[3]);
  if(q.operation==ME_BIND_TEX)reply.success=eglBindTexImage(dpy,h->native,(EGLint)a[2]);
  if(q.operation==ME_RELEASE_TEX)reply.success=eglReleaseTexImage(dpy,h->native,(EGLint)a[2]);goto result;
 }
 if(q.operation==ME_QUERY_CONTEXT){struct ME_Handle *h=me_get(pid,a[1],ME_CONTEXT);EGLint value=0;if(!h){reply.error=EGL_BAD_CONTEXT;goto send;}reply.success=eglQueryContext(dpy,h->native,(EGLint)a[2],&value);reply.value=(uint64_t)(int64_t)value;goto result;}
 if(q.operation==ME_INTERVAL){if(thread->context&&!me_bind(thread)){reply.error=eglGetError();goto send;}reply.success=eglSwapInterval(dpy,(EGLint)a[1]);goto result;}
 if(q.operation==ME_CREATE_SYNC){if(thread->context&&!me_bind(thread)){reply.error=eglGetError();goto send;}EGLSync sync=eglCreateSync(dpy,(EGLenum)a[1],q.attribute_count?wide:NULL);reply.value=me_put(pid,ME_SYNC,sync,dpy,NULL,0);reply.success=reply.value!=0;if(sync&&!reply.value)reply.error=EGL_BAD_ALLOC;goto result;}
 if(q.operation==ME_DESTROY_SYNC||q.operation==ME_CLIENT_WAIT_SYNC||q.operation==ME_WAIT_SYNC||q.operation==ME_QUERY_SYNC){
  struct ME_Handle *h=me_get(pid,a[1],ME_SYNC);if(!h){reply.error=EGL_BAD_PARAMETER;goto send;}
  if(thread->context&&!me_bind(thread)){reply.error=eglGetError();goto send;}
  if(q.operation==ME_DESTROY_SYNC){reply.success=eglDestroySync(dpy,h->native);if(reply.success)h->native=NULL;}
  if(q.operation==ME_CLIENT_WAIT_SYNC){EGLint status=eglClientWaitSync(dpy,h->native,(EGLint)a[2],a[3]);reply.value=status;reply.success=status!=EGL_FALSE;}
  if(q.operation==ME_WAIT_SYNC)reply.success=eglWaitSync(dpy,h->native,(EGLint)a[2]);
  if(q.operation==ME_QUERY_SYNC){EGLAttrib value=0;reply.success=eglGetSyncAttrib(dpy,h->native,(EGLint)a[2],&value);reply.value=(uint64_t)value;}goto result;
 }
 if(q.operation==ME_CREATE_IMAGE){
  struct ME_Handle *ctx=me_get(pid,a[1],ME_CONTEXT);if(a[1]&&!ctx){reply.error=EGL_BAD_CONTEXT;goto send;}
  if(a[2]!=EGL_GL_TEXTURE_2D&&a[2]!=EGL_GL_RENDERBUFFER){reply.error=EGL_BAD_PARAMETER;goto send;}
  if(a[3]>=MAXOBJ||!me_select(pid,tid)){reply.error=EGL_BAD_CONTEXT;goto send;}
  EGLImage image=eglCreateImage(dpy,ctx?ctx->native:EGL_NO_CONTEXT,(EGLenum)a[2],(EGLClientBuffer)(uintptr_t)M((uint32_t)a[3]),q.attribute_count?wide:NULL);
  reply.value=me_put(pid,ME_IMAGE,image,dpy,NULL,0);reply.success=reply.value!=0;if(image&&!reply.value)reply.error=EGL_BAD_ALLOC;goto result;
 }
 if(q.operation==ME_DESTROY_IMAGE){struct ME_Handle *h=me_get(pid,a[1],ME_IMAGE);if(!h){reply.error=EGL_BAD_PARAMETER;goto send;}reply.success=eglDestroyImage(dpy,h->native);if(reply.success)h->native=NULL;goto result;}
 reply.error=EGL_BAD_PARAMETER;goto send;
result:
 if(reply.error==EGL_SUCCESS)reply.error=eglGetError();
send:
 if(reply.bytes>16384){reply.success=0;reply.error=EGL_BAD_ALLOC;reply.bytes=reply.count=0;}
 return me_write_all(&reply,sizeof(reply))&&(!reply.bytes||me_write_all(extra,reply.bytes));
}
