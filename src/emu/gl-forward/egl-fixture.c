#define EGL_EGLEXT_PROTOTYPES
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif

static EGLDisplay dpy;
static EGLConfig cfg;
static EGLContext main_context;
static EGLSurface main_surface;
static int failed;
#define REQUIRE(x) do { if(!(x)){fprintf(stderr,"FAIL line=%d EGL_ERROR=%x\n",__LINE__,eglGetError());return 1;} } while(0)
static int pixel(unsigned char r,unsigned char g,unsigned char b)
{
 unsigned char p[4]={0};glReadPixels(0,0,1,1,GL_RGBA,GL_UNSIGNED_BYTE,p);
 return p[0]==r&&p[1]==g&&p[2]==b&&p[3]==255&&glGetError()==GL_NO_ERROR;
}
static int worker_checks(void)
{
 REQUIRE(eglGetCurrentContext()==EGL_NO_CONTEXT);
 REQUIRE(!eglMakeCurrent(dpy,main_surface,main_surface,main_context));
 REQUIRE(eglGetError()==EGL_BAD_ACCESS&&eglGetError()==EGL_SUCCESS);
 EGLint sa[]={EGL_WIDTH,16,EGL_HEIGHT,16,EGL_NONE},ca[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};
 EGLSurface s=eglCreatePbufferSurface(dpy,cfg,sa);EGLContext c=eglCreateContext(dpy,cfg,EGL_NO_CONTEXT,ca);
 REQUIRE(s&&c&&eglMakeCurrent(dpy,s,s,c));
 glClearColor(0,1,0,1);glClear(GL_COLOR_BUFFER_BIT);REQUIRE(pixel(0,255,0));
 REQUIRE(eglDestroyContext(dpy,c)&&eglDestroySurface(dpy,s));
 glClearColor(0,0,1,1);glClear(GL_COLOR_BUFFER_BIT);REQUIRE(pixel(0,0,255));
 REQUIRE(eglReleaseThread()&&eglGetCurrentContext()==EGL_NO_CONTEXT);
 REQUIRE(!eglMakeCurrent(dpy,s,s,c)&&eglGetError()==EGL_BAD_SURFACE);
 REQUIRE(eglReleaseThread());return 0;
}
static void *worker(void *unused)
{ (void)unused;failed=worker_checks();return NULL; }
int main(void)
{
#ifdef _WIN32
 REQUIRE(_setmode(_fileno(stdin),_O_BINARY)>=0&&_setmode(_fileno(stdout),_O_BINARY)>=0);
#endif
 dpy=eglGetDisplay(EGL_DEFAULT_DISPLAY);REQUIRE(dpy!=EGL_NO_DISPLAY);
 EGLint count=0,major=0,minor=0;
 REQUIRE(!eglGetConfigs(dpy,NULL,0,&count)&&eglGetError()==EGL_NOT_INITIALIZED);
 REQUIRE(eglInitialize(dpy,&major,&minor)&&major>=1);
 fprintf(stderr,"ACTUAL_EGL=%d.%d VENDOR=%s\n",major,minor,eglQueryString(dpy,EGL_VENDOR));
 EGLint attrs[]={EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_ALPHA_SIZE,8,
  EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_ES2_BIT,EGL_NONE};
 REQUIRE(eglChooseConfig(dpy,attrs,&cfg,1,&count)&&count==1);
 REQUIRE(eglBindAPI(EGL_OPENGL_ES_API)&&eglQueryAPI()==EGL_OPENGL_ES_API);
 EGLint ca[]={EGL_CONTEXT_CLIENT_VERSION,3,EGL_NONE};
 main_context=eglCreateContext(dpy,cfg,EGL_NO_CONTEXT,ca);
 main_surface=eglCreateWindowSurface(dpy,cfg,(EGLNativeWindowType)(uintptr_t)0x1234,NULL);
 REQUIRE(main_context&&main_surface&&eglMakeCurrent(dpy,main_surface,main_surface,main_context));
 REQUIRE(eglGetCurrentDisplay()==dpy&&eglGetCurrentContext()==main_context&&eglGetCurrentSurface(EGL_DRAW)==main_surface);
 EGLint w=0,h=0;REQUIRE(eglQuerySurface(dpy,main_surface,EGL_WIDTH,&w)&&eglQuerySurface(dpy,main_surface,EGL_HEIGHT,&h)&&w==1560&&h==700);
 fprintf(stderr,"ACTUAL_RENDERER=%s\n",glGetString(GL_RENDERER));
 REQUIRE(eglGetProcAddress("glClear")&&eglGetProcAddress("eglMakeCurrent")&&!eglGetProcAddress("not_a_GL_or_EGL_function"));
 glViewport(0,0,w,h);glClearColor(1,0,0,1);glClear(GL_COLOR_BUFFER_BIT);REQUIRE(pixel(255,0,0));
 REQUIRE(eglSwapBuffers(dpy,main_surface));
 pthread_t t;REQUIRE(!pthread_create(&t,NULL,worker,NULL)&&!pthread_join(t,NULL)&&!failed);
 REQUIRE(eglGetCurrentContext()==main_context&&pixel(255,0,0));
 const char *extensions=eglQueryString(dpy,EGL_EXTENSIONS);REQUIRE(extensions);
 EGLSync sync=eglCreateSync(dpy,EGL_SYNC_FENCE,NULL);
 if(strstr(extensions,"EGL_KHR_fence_sync")){
  REQUIRE(sync!=EGL_NO_SYNC);
  EGLint wait=eglClientWaitSync(dpy,sync,EGL_SYNC_FLUSH_COMMANDS_BIT,1000000000);
  REQUIRE(wait==EGL_CONDITION_SATISFIED&&eglDestroySync(dpy,sync));
 }else REQUIRE(sync==EGL_NO_SYNC&&eglGetError()==EGL_BAD_MATCH);
 fprintf(stderr,"ACTUAL_FENCE_SYNC=%s\n",sync?"supported":"not-advertised-native-BAD_MATCH");
 REQUIRE(!eglQuerySurface(dpy,(EGLSurface)(uintptr_t)0x7fffffff,EGL_WIDTH,&w)&&eglGetError()==EGL_BAD_SURFACE);
 REQUIRE(!eglCreatePixmapSurface(dpy,cfg,(EGLNativePixmapType)0,NULL)&&eglGetError()==EGL_BAD_NATIVE_PIXMAP);
 REQUIRE(eglMakeCurrent(dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT));
 REQUIRE(eglDestroyContext(dpy,main_context)&&eglDestroySurface(dpy,main_surface));
 REQUIRE(eglTerminate(dpy)&&!eglGetConfigs(dpy,NULL,0,&count)&&eglGetError()==EGL_NOT_INITIALIZED);
 REQUIRE(eglInitialize(dpy,&major,&minor)&&eglTerminate(dpy)&&eglReleaseThread());
 fprintf(stderr,"EGL_REAL_PIXELS=RED_GREEN_BLUE THREAD_ISOLATION=1 DEFERRED_DESTROY=1 REAL_ERRORS=1\n");
 return 0;
}
