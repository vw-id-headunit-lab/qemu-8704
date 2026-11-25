#include <EGL/egl.h>
#include <EGL/eglext.h>
#include "egl-wire.h"
#ifdef GL_FORWARD_QNX
#define me_error (current_record()->egl_error)
#define me_current_display (current_record()->egl_display)
#define me_current_context (current_record()->egl_context)
#define me_current_draw (current_record()->egl_draw)
#define me_current_read (current_record()->egl_read)
#else
static __thread EGLint me_error=EGL_SUCCESS;
static __thread EGLDisplay me_current_display;
static __thread EGLContext me_current_context;
static __thread EGLSurface me_current_draw,me_current_read;
#endif
static int me_attributes(const EGLint *p,int64_t *out)
{
 if(!p)return 0;
 for(unsigned i=0;i<ME_EGL_MAX_ATTRS;i+=2){out[i]=p[i];if(p[i]==EGL_NONE)return i+1;
  if(i+1<ME_EGL_MAX_ATTRS)out[i+1]=p[i+1];}
 me_error=EGL_BAD_ATTRIBUTE;return -1;
}
static int me_wide_attributes(const EGLAttrib *p,int64_t *out)
{
 if(!p)return 0;
 for(unsigned i=0;i<ME_EGL_MAX_ATTRS;i+=2){out[i]=p[i];if(p[i]==EGL_NONE)return i+1;
  if(i+1<ME_EGL_MAX_ATTRS)out[i+1]=p[i+1];}
 me_error=EGL_BAD_ATTRIBUTE;return -1;
}
static struct ME_EGLReply me_call(unsigned op,const uint64_t a[8],const int64_t *attrs,int count,void *data,unsigned capacity)
{
 struct ME_EGLReply reply={0,EGL_SUCCESS,0,0,0};
 if(count<0)return reply;
 struct ME_EGLRequest q={op,(uint32_t)count,{0}};if(a)memcpy(q.arg,a,64);
 rec(200,72+count*8);PART(&q,72);if(count)PART(attrs,count*8);
 recv_bytes(&reply,sizeof(reply),0);
 if(reply.bytes>capacity||reply.bytes>16384)transport_failed();
 recv_bytes(data,reply.bytes,1);
 if(reply.error!=EGL_SUCCESS)me_error=reply.error;
 return reply;
}
#define ME_ARGS(...) (uint64_t[8]){__VA_ARGS__}
#define ME_ID(x) ((uint64_t)(uintptr_t)(x))
#define ME_SIMPLE(op,...) me_call(op,ME_ARGS(__VA_ARGS__),NULL,0,NULL,0)
EGLDisplay eglGetDisplay(EGLNativeDisplayType native)
{ struct ME_EGLReply r=ME_SIMPLE(ME_GET_DISPLAY,ME_ID(native));return (EGLDisplay)(uintptr_t)r.value; }
EGLDisplay eglGetPlatformDisplay(EGLenum platform,void *native,const EGLAttrib *attrs)
{
 if(platform!=0x31d8&&platform!=0){me_error=EGL_BAD_PARAMETER;return EGL_NO_DISPLAY;}
 int64_t values[ME_EGL_MAX_ATTRS];int n=me_wide_attributes(attrs,values);
 struct ME_EGLReply r=me_call(ME_GET_DISPLAY,ME_ARGS(ME_ID(native),platform),values,n,NULL,0);return (EGLDisplay)(uintptr_t)r.value;
}
EGLBoolean eglInitialize(EGLDisplay d,EGLint *major,EGLint *minor)
{ struct ME_EGLReply r=ME_SIMPLE(ME_INITIALIZE,ME_ID(d));if(r.success){if(major)*major=(EGLint)r.value;if(minor)*minor=(EGLint)(r.value>>32);}return r.success; }
EGLBoolean eglTerminate(EGLDisplay d)
{ struct ME_EGLReply r=ME_SIMPLE(ME_TERMINATE,ME_ID(d));if(r.success&&me_current_display==d){me_current_display=NULL;me_current_context=NULL;me_current_draw=me_current_read=NULL;}return r.success; }
static EGLBoolean me_configs(unsigned op,EGLDisplay d,const EGLint *attrs,EGLConfig *configs,EGLint size,EGLint *number)
{
 if(!number){me_error=EGL_BAD_PARAMETER;return EGL_FALSE;}
 int64_t values[ME_EGL_MAX_ATTRS];int n=me_attributes(attrs,values);uint64_t ids[ME_EGL_MAX_CONFIGS];
 struct ME_EGLReply r=me_call(op,ME_ARGS(ME_ID(d),(uint64_t)(int64_t)size,configs!=NULL),values,n,ids,sizeof(ids));
 if(r.success){if(r.count>ME_EGL_MAX_CONFIGS||r.count>(unsigned)size)transport_failed();*number=(EGLint)r.value;
  for(unsigned i=0;i<r.count;i++)configs[i]=(EGLConfig)(uintptr_t)ids[i];}return r.success;
}
EGLBoolean eglGetConfigs(EGLDisplay d,EGLConfig *c,EGLint size,EGLint *number)
{ return me_configs(ME_GET_CONFIGS,d,NULL,c,size,number); }
EGLBoolean eglChooseConfig(EGLDisplay d,const EGLint *a,EGLConfig *c,EGLint size,EGLint *number)
{ return me_configs(ME_CHOOSE_CONFIG,d,a,c,size,number); }
EGLBoolean eglGetConfigAttrib(EGLDisplay d,EGLConfig c,EGLint attribute,EGLint *value)
{ if(!value){me_error=EGL_BAD_PARAMETER;return 0;}struct ME_EGLReply r=ME_SIMPLE(ME_CONFIG_ATTRIB,ME_ID(d),ME_ID(c),attribute);if(r.success)*value=(EGLint)r.value;return r.success; }
EGLContext eglCreateContext(EGLDisplay d,EGLConfig c,EGLContext share,const EGLint *a)
{ int64_t attrs[ME_EGL_MAX_ATTRS];int n=me_attributes(a,attrs);struct ME_EGLReply r=me_call(ME_CREATE_CONTEXT,ME_ARGS(ME_ID(d),ME_ID(c),ME_ID(share)),attrs,n,NULL,0);return (EGLContext)(uintptr_t)r.value; }
EGLBoolean eglDestroyContext(EGLDisplay d,EGLContext c)
{ return ME_SIMPLE(ME_DESTROY_CONTEXT,ME_ID(d),ME_ID(c)).success; }
EGLSurface eglCreateWindowSurface(EGLDisplay d,EGLConfig c,EGLNativeWindowType w,const EGLint *a)
{
 int64_t attrs[ME_EGL_MAX_ATTRS];int n=me_attributes(a,attrs);
#ifdef GL_FORWARD_QNX
 int size[2]={0,0},id=0;
 if(!w||screen_get_window_property_iv((void *)(uintptr_t)w,5,size)||size[0]<1||size[1]<1||size[0]>4096||size[1]>4096){me_error=EGL_BAD_NATIVE_WINDOW;return EGL_NO_SURFACE;}
 screen_get_window_property_iv((void *)(uintptr_t)w,87,&id);
 struct ME_EGLReply r=me_call(ME_CREATE_WINDOW,ME_ARGS(ME_ID(d),ME_ID(c),ME_ID(w),size[0],size[1],id),attrs,n,NULL,0);
#else
 struct ME_EGLReply r=me_call(ME_CREATE_WINDOW,ME_ARGS(ME_ID(d),ME_ID(c),ME_ID(w)),attrs,n,NULL,0);
#endif
 return (EGLSurface)(uintptr_t)r.value;
}
EGLSurface eglCreatePlatformWindowSurface(EGLDisplay d,EGLConfig c,void *w,const EGLAttrib *a)
{ int64_t attrs[ME_EGL_MAX_ATTRS];int n=me_wide_attributes(a,attrs);
#ifdef GL_FORWARD_QNX
 int size[2]={0,0},id=0;if(!w||screen_get_window_property_iv(w,5,size)||size[0]<1||size[1]<1||size[0]>4096||size[1]>4096){me_error=EGL_BAD_NATIVE_WINDOW;return EGL_NO_SURFACE;}screen_get_window_property_iv(w,87,&id);
 struct ME_EGLReply r=me_call(ME_CREATE_WINDOW,ME_ARGS(ME_ID(d),ME_ID(c),ME_ID(w),size[0],size[1],id),attrs,n,NULL,0);
#else
 struct ME_EGLReply r=me_call(ME_CREATE_WINDOW,ME_ARGS(ME_ID(d),ME_ID(c),ME_ID(w)),attrs,n,NULL,0);
#endif
 return (EGLSurface)(uintptr_t)r.value; }
EGLSurface eglCreatePbufferSurface(EGLDisplay d,EGLConfig c,const EGLint *a)
{ int64_t attrs[ME_EGL_MAX_ATTRS];int n=me_attributes(a,attrs);struct ME_EGLReply r=me_call(ME_CREATE_PBUFFER,ME_ARGS(ME_ID(d),ME_ID(c)),attrs,n,NULL,0);return (EGLSurface)(uintptr_t)r.value; }
EGLBoolean eglDestroySurface(EGLDisplay d,EGLSurface s)
{ return ME_SIMPLE(ME_DESTROY_SURFACE,ME_ID(d),ME_ID(s)).success; }
EGLBoolean eglMakeCurrent(EGLDisplay d,EGLSurface draw,EGLSurface read,EGLContext c)
{ struct ME_EGLReply r=ME_SIMPLE(ME_MAKE_CURRENT,ME_ID(d),ME_ID(draw),ME_ID(read),ME_ID(c));if(r.success){me_current_context=c;me_current_display=c?d:EGL_NO_DISPLAY;me_current_draw=draw;me_current_read=read;}return r.success; }
EGLBoolean eglSwapBuffers(EGLDisplay d,EGLSurface s)
{ EGLBoolean success=ME_SIMPLE(ME_SWAP,ME_ID(d),ME_ID(s)).success;
#ifdef GL_FORWARD_QNX
 if(success)qnx_start_display_liveness();
#endif
 return success; }
EGLBoolean eglSwapInterval(EGLDisplay d,EGLint interval)
{ return ME_SIMPLE(ME_INTERVAL,ME_ID(d),(uint64_t)(int64_t)interval).success; }
EGLBoolean eglQuerySurface(EGLDisplay d,EGLSurface s,EGLint attribute,EGLint *value)
{ if(!value){me_error=EGL_BAD_PARAMETER;return 0;}struct ME_EGLReply r=ME_SIMPLE(ME_QUERY_SURFACE,ME_ID(d),ME_ID(s),attribute);if(r.success)*value=(EGLint)r.value;return r.success; }
EGLBoolean eglQueryContext(EGLDisplay d,EGLContext c,EGLint attribute,EGLint *value)
{ if(!value){me_error=EGL_BAD_PARAMETER;return 0;}struct ME_EGLReply r=ME_SIMPLE(ME_QUERY_CONTEXT,ME_ID(d),ME_ID(c),attribute);if(r.success)*value=(EGLint)r.value;return r.success; }
EGLBoolean eglSurfaceAttrib(EGLDisplay d,EGLSurface s,EGLint attribute,EGLint value)
{ return ME_SIMPLE(ME_SURFACE_ATTRIB,ME_ID(d),ME_ID(s),attribute,(uint64_t)(int64_t)value).success; }
EGLBoolean eglBindAPI(EGLenum api)
{ return ME_SIMPLE(ME_BIND_API,api).success; }
EGLenum eglQueryAPI(void) { return (EGLenum)ME_SIMPLE(ME_QUERY_API,0).value; }
EGLBoolean eglWaitClient(void) { return ME_SIMPLE(ME_WAIT_CLIENT,0).success; }
EGLBoolean eglWaitGL(void) { return ME_SIMPLE(ME_WAIT_GL,0).success; }
EGLBoolean eglWaitNative(EGLint engine) { return ME_SIMPLE(ME_WAIT_NATIVE,engine).success; }
EGLBoolean eglReleaseThread(void)
{ struct ME_EGLReply r=ME_SIMPLE(ME_RELEASE_THREAD,0);if(r.success){me_current_context=NULL;me_current_display=NULL;me_current_draw=me_current_read=NULL;}return r.success; }
EGLDisplay eglGetCurrentDisplay(void) { return me_current_display; }
EGLContext eglGetCurrentContext(void) { return me_current_context; }
EGLSurface eglGetCurrentSurface(EGLint which)
{ if(which==EGL_DRAW)return me_current_draw;if(which==EGL_READ)return me_current_read;me_error=EGL_BAD_PARAMETER;return EGL_NO_SURFACE; }
EGLint eglGetError(void) { EGLint value=me_error;me_error=EGL_SUCCESS;return value; }
static int me_extension_supported(const char *name,size_t length)
{
 const char *list[]={"EGL_KHR_create_context","EGL_KHR_surfaceless_context","EGL_KHR_fence_sync","EGL_KHR_wait_sync","EGL_KHR_image_base","EGL_KHR_gl_texture_2D_image","EGL_KHR_gl_renderbuffer_image",NULL};
 for(unsigned i=0;list[i];i++)if(strlen(list[i])==length&&!memcmp(list[i],name,length))return 1;return 0;
}
const char *eglQueryString(EGLDisplay d,EGLint name)
{
#ifdef GL_FORWARD_QNX
 char (*values)[16385]=current_record()->egl_strings;unsigned slot;
#else
 static __thread char values[4][16385];unsigned slot;
#endif
 switch(name){case EGL_VENDOR:slot=0;break;case EGL_VERSION:slot=1;break;case EGL_EXTENSIONS:slot=2;break;case EGL_CLIENT_APIS:slot=3;break;default:slot=0;break;}
 struct ME_EGLReply r=me_call(ME_QUERY_STRING,ME_ARGS(ME_ID(d),name),NULL,0,values[slot],16384);
 if(!r.success)return NULL;values[slot][r.bytes]=0;
 if(name==EGL_EXTENSIONS){char *read=values[slot],*write=read;
  while(*read){while(*read==' ')read++;char *end=read;while(*end&&*end!=' ')end++;
   size_t n=end-read;if(n&&me_extension_supported(read,n)){memmove(write,read,n);write+=n;*write++=' ';}read=end;}
  if(write>values[slot])write--;*write=0;}
 return values[slot];
}
EGLBoolean eglBindTexImage(EGLDisplay d,EGLSurface s,EGLint buffer)
{ return ME_SIMPLE(ME_BIND_TEX,ME_ID(d),ME_ID(s),buffer).success; }
EGLBoolean eglReleaseTexImage(EGLDisplay d,EGLSurface s,EGLint buffer)
{ return ME_SIMPLE(ME_RELEASE_TEX,ME_ID(d),ME_ID(s),buffer).success; }
EGLSync eglCreateSync(EGLDisplay d,EGLenum type,const EGLAttrib *a)
{ int64_t attrs[ME_EGL_MAX_ATTRS];int n=me_wide_attributes(a,attrs);struct ME_EGLReply r=me_call(ME_CREATE_SYNC,ME_ARGS(ME_ID(d),type),attrs,n,NULL,0);return (EGLSync)(uintptr_t)r.value; }
EGLBoolean eglDestroySync(EGLDisplay d,EGLSync s) { return ME_SIMPLE(ME_DESTROY_SYNC,ME_ID(d),ME_ID(s)).success; }
EGLint eglClientWaitSync(EGLDisplay d,EGLSync s,EGLint flags,EGLTime timeout)
{ return (EGLint)ME_SIMPLE(ME_CLIENT_WAIT_SYNC,ME_ID(d),ME_ID(s),flags,timeout).value; }
EGLBoolean eglWaitSync(EGLDisplay d,EGLSync s,EGLint flags) { return ME_SIMPLE(ME_WAIT_SYNC,ME_ID(d),ME_ID(s),flags).success; }
EGLBoolean eglGetSyncAttrib(EGLDisplay d,EGLSync s,EGLint attribute,EGLAttrib *value)
{ if(!value){me_error=EGL_BAD_PARAMETER;return 0;}struct ME_EGLReply r=ME_SIMPLE(ME_QUERY_SYNC,ME_ID(d),ME_ID(s),attribute);if(r.success)*value=(EGLAttrib)r.value;return r.success; }
EGLImage eglCreateImage(EGLDisplay d,EGLContext c,EGLenum target,EGLClientBuffer buffer,const EGLAttrib *a)
{ int64_t attrs[ME_EGL_MAX_ATTRS];int n=me_wide_attributes(a,attrs);struct ME_EGLReply r=me_call(ME_CREATE_IMAGE,ME_ARGS(ME_ID(d),ME_ID(c),target,ME_ID(buffer)),attrs,n,NULL,0);return (EGLImage)(uintptr_t)r.value; }
EGLBoolean eglDestroyImage(EGLDisplay d,EGLImage i) { return ME_SIMPLE(ME_DESTROY_IMAGE,ME_ID(d),ME_ID(i)).success; }
EGLSurface eglCreatePixmapSurface(EGLDisplay d,EGLConfig c,EGLNativePixmapType p,const EGLint *a)
{ (void)d;(void)c;(void)p;(void)a;me_error=EGL_BAD_NATIVE_PIXMAP;return EGL_NO_SURFACE; }
EGLSurface eglCreatePlatformPixmapSurface(EGLDisplay d,EGLConfig c,void *p,const EGLAttrib *a)
{ (void)d;(void)c;(void)p;(void)a;me_error=EGL_BAD_NATIVE_PIXMAP;return EGL_NO_SURFACE; }
EGLBoolean eglCopyBuffers(EGLDisplay d,EGLSurface s,EGLNativePixmapType p)
{ (void)d;(void)s;(void)p;me_error=EGL_BAD_NATIVE_PIXMAP;return EGL_FALSE; }
EGLSurface eglCreatePbufferFromClientBuffer(EGLDisplay d,EGLenum type,EGLClientBuffer buffer,EGLConfig config,const EGLint *attrs)
{ (void)d;(void)type;(void)buffer;(void)config;(void)attrs;me_error=EGL_BAD_PARAMETER;return EGL_NO_SURFACE; }
EGLSyncKHR eglCreateSyncKHR(EGLDisplay d,EGLenum type,const EGLint *a)
{ int64_t attrs[ME_EGL_MAX_ATTRS];int n=me_attributes(a,attrs);struct ME_EGLReply r=me_call(ME_CREATE_SYNC,ME_ARGS(ME_ID(d),type),attrs,n,NULL,0);return (EGLSyncKHR)(uintptr_t)r.value; }
EGLBoolean eglDestroySyncKHR(EGLDisplay d,EGLSyncKHR s) { return eglDestroySync(d,(EGLSync)s); }
EGLint eglClientWaitSyncKHR(EGLDisplay d,EGLSyncKHR s,EGLint flags,EGLTimeKHR timeout) { return eglClientWaitSync(d,(EGLSync)s,flags,timeout); }
EGLint eglWaitSyncKHR(EGLDisplay d,EGLSyncKHR s,EGLint flags) { return eglWaitSync(d,(EGLSync)s,flags); }
EGLBoolean eglGetSyncAttribKHR(EGLDisplay d,EGLSyncKHR s,EGLint attribute,EGLint *value)
{ if(!value){me_error=EGL_BAD_PARAMETER;return 0;}EGLAttrib wide;EGLBoolean ok=eglGetSyncAttrib(d,(EGLSync)s,attribute,&wide);if(ok)*value=(EGLint)wide;return ok; }
EGLImageKHR eglCreateImageKHR(EGLDisplay d,EGLContext c,EGLenum target,EGLClientBuffer buffer,const EGLint *a)
{ int64_t attrs[ME_EGL_MAX_ATTRS];int n=me_attributes(a,attrs);struct ME_EGLReply r=me_call(ME_CREATE_IMAGE,ME_ARGS(ME_ID(d),ME_ID(c),target,ME_ID(buffer)),attrs,n,NULL,0);return (EGLImageKHR)(uintptr_t)r.value; }
EGLBoolean eglDestroyImageKHR(EGLDisplay d,EGLImageKHR i) { return eglDestroyImage(d,(EGLImage)i); }
__eglMustCastToProperFunctionPointerType eglGetProcAddress(const char *name)
{
 if(!name)return NULL;
#define ME_PROC(n) if(!strcmp(name,#n))return (__eglMustCastToProperFunctionPointerType)n
#include "gl-procs.inc"
#ifdef GL_FORWARD_QNX
#include "qnx-required-gl-procs.inc"
#endif
#include "egl-procs.inc"
#undef ME_PROC
#ifdef GL_FORWARD_QNX
 size_t n=strlen(name);if(n&&n<=128){rec(167,n);PART(name,n);}
#endif
 return NULL;
}
