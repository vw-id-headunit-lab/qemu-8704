#define _GNU_SOURCE
#include <GLES3/gl3.h>
#ifndef GL_TEXTURE_BORDER_COLOR_EXT
#define GL_TEXTURE_BORDER_COLOR_EXT 0x1004
#endif
#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <EGL/eglext_angle.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
static int meb_qnx_strict;
static uint32_t meb_qnx_pid;
static int meb_qnx_fixture;

#include "known_shaders.h"

#define DEFAULT_PORT 5005
#define DEFAULT_W    1560
#define DEFAULT_H    700
#define MAXOBJ       (1 << 20)

static int rejected_record;
static int meb_replace_frame(const char *from, const char *to)
{ return rename(from, to); }
static int win;
static EGLDisplay display;
static EGLSurface surface;
static EGLConfig config;
static int win_w, win_h;
static int panel_w=DEFAULT_W,panel_h=DEFAULT_H;
static uint32_t current_pid;
static uint32_t primary_display_pid;
static GLuint default_gmap[MAXOBJ];
static GLuint *gmap = default_gmap;        


struct BlendMetadata {
    uint8_t shader[MAXOBJ], program[MAXOBJ];
    void *client_arrays[16];
    unsigned client_array_bytes[16];
    struct MenuTexture *menu;
    uint32_t fragment[MAXOBJ], active_program;
};
static struct BlendMetadata default_blend, *blend = &default_blend;

struct BlendState {
    GLboolean enabled;
    GLint src_rgb, dst_rgb, src_alpha, dst_alpha, equation_rgb, equation_alpha;
};

static void begin_binary_blend(unsigned mode, struct BlendState *saved)
{
    if (!mode) return;
    saved->enabled = glIsEnabled(GL_BLEND);
    glGetIntegerv(GL_BLEND_SRC_RGB, &saved->src_rgb);
    glGetIntegerv(GL_BLEND_DST_RGB, &saved->dst_rgb);
    glGetIntegerv(GL_BLEND_SRC_ALPHA, &saved->src_alpha);
    glGetIntegerv(GL_BLEND_DST_ALPHA, &saved->dst_alpha);
    glGetIntegerv(GL_BLEND_EQUATION_RGB, &saved->equation_rgb);
    glGetIntegerv(GL_BLEND_EQUATION_ALPHA, &saved->equation_alpha);
    glEnable(GL_BLEND);
    glBlendEquationSeparate(GL_FUNC_ADD, GL_FUNC_ADD);
    glBlendFuncSeparate(mode == 5 ? GL_ZERO : (mode == 3 ? GL_SRC_ALPHA : GL_ONE),
                        mode >= 4 ? GL_ZERO : GL_ONE_MINUS_SRC_ALPHA,
                        (mode == 2 || mode == 5) ? GL_ZERO : (mode == 3 ? GL_SRC_ALPHA : GL_ONE),
                        mode == 2 ? GL_ONE : (mode >= 4 ? GL_ZERO : GL_ONE_MINUS_SRC_ALPHA));
}

static void end_binary_blend(unsigned mode, const struct BlendState *saved)
{
    if (!mode) return;
    glBlendFuncSeparate(saved->src_rgb, saved->dst_rgb, saved->src_alpha, saved->dst_alpha);
    glBlendEquationSeparate(saved->equation_rgb, saved->equation_alpha);
    if (!saved->enabled) glDisable(GL_BLEND);
}


static char *load_firmware_shader(uint64_t hash, uint32_t size, unsigned *mode)
{
    const char *directory = getenv("MHI2_SHADER_CACHE");
    char path[4096];
    if (!directory) return NULL;
    int n = snprintf(path, sizeof(path), "%s/%016llx-%u.blend",
                     directory, (unsigned long long)hash, size);
    if (n < 0 || n >= sizeof(path)) return NULL;
    FILE *file = fopen(path, "r");
    if (!file) return NULL;
    int valid = fscanf(file, "%u", mode) == 1 && *mode <= 5;
    fclose(file);
    if (!valid) return NULL;
    snprintf(path, sizeof(path), "%s/%016llx-%u.glsl",
             directory, (unsigned long long)hash, size);
    file = fopen(path, "rb");
    if (!file) return NULL;
    if (fseek(file, 0, SEEK_END)) { fclose(file); return NULL; }
    long length = ftell(file);
    if (length <= 0 || length > 1024*1024) { fclose(file); return NULL; }
    rewind(file);
    char *source = malloc(length + 1);
    if (!source) { fclose(file); return NULL; }
    valid = fread(source, 1, length, file) == length;
    fclose(file);
    if (!valid) { free(source); return NULL; }
    source[length] = 0;
    return source;
}

static GLuint M(uint32_t id) { return id < MAXOBJ ? gmap[id] : 0; }


static int32_t i32(const uint8_t *p, int off) { int32_t v; memcpy(&v, p + off, 4); return v; }
static uint32_t u32(const uint8_t *p, int off) { uint32_t v; memcpy(&v, p + off, 4); return v; }

#include "menu_texture.h"

static unsigned char *display_rgba;

static const char *frame_path(void)
{ const char *p=getenv("MHI2_GL_FRAME");return p&&*p?p:"/tmp/glhost_frame.ppm"; }

static void dump_frame(const char *path)
{
    if (!win) return;
    size_t n = (size_t)win_w * win_h;
    unsigned char *rgba = malloc(n * 4);
    if (!rgba) return;
    glReadPixels(0, 0, win_w, win_h, GL_RGBA, GL_UNSIGNED_BYTE, rgba);
    if (!strcmp(path,frame_path())) {
        display_rgba=realloc(display_rgba,n*4);
        if(display_rgba)memcpy(display_rgba,rgba,n*4);
    }
    char temporary[4096];
    snprintf(temporary, sizeof(temporary), "%s.tmp", path);
    FILE *f = fopen(temporary, "wb");
    if (f) {
        fprintf(f, "P6\n%d %d\n255\n", win_w, win_h);
        unsigned char *rgb=malloc(n*3);int okay=rgb!=NULL;
        if(rgb){
            for(int y=0;y<win_h;y++)for(int x=0;x<win_w;x++){
                const unsigned char *source=rgba+((size_t)(win_h-1-y)*win_w+x)*4;
                unsigned char *target=rgb+((size_t)y*win_w+x)*3;
                target[0]=source[0];target[1]=source[1];target[2]=source[2];
            }
            okay=fwrite(rgb,1,n*3,f)==n*3;free(rgb);
        }
        if(fclose(f)==0&&okay)meb_replace_frame(temporary,path);
    }
    free(rgba);
}

static int read_full(int fd, void *buf, size_t n)
{
    uint8_t *p = buf;
    while (n) {
        ssize_t r = read(fd, p, n);
        if (r == 0) return -1;
        if (r < 0) { if (errno == EINTR) continue; return -1; }
        p += r; n -= (size_t)r;
    }
    return 0;
}

static void ensure_window(int w, int h)
{
    if (win) return;
    if (w <= 0 || w > 8192) w = DEFAULT_W;
    if (h <= 0 || h > 8192) h = DEFAULT_H;

    const EGLAttrib platform[] = {EGL_PLATFORM_ANGLE_TYPE_ANGLE,
        EGL_PLATFORM_ANGLE_TYPE_METAL_ANGLE, EGL_NONE};
    display = eglGetPlatformDisplay(EGL_PLATFORM_ANGLE_ANGLE, NULL, platform);
    EGLint major, minor, count;
    const EGLint attrs[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_DEPTH_SIZE, 24, EGL_STENCIL_SIZE, 8, EGL_NONE};
    const EGLint pb[] = {EGL_WIDTH, w, EGL_HEIGHT, h, EGL_NONE};
    const EGLint ca[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    if (!eglInitialize(display, &major, &minor) ||
        !eglChooseConfig(display, attrs, &config, 1, &count) || !count) {
        fprintf(stderr, "EGL initialization failed %x\n", eglGetError()); exit(1);
    }
    surface = eglCreatePbufferSurface(display, config, pb);
    EGLContext ctx = eglCreateContext(display, config, EGL_NO_CONTEXT, ca);
    if (!eglMakeCurrent(display, surface, surface, ctx)) {
        fprintf(stderr, "EGL context failed %x\n", eglGetError()); exit(1);
    }
    win = 1;
    win_w = w; win_h = h;
    fprintf(stderr, "glhost: window %dx%d, GLES = %s | %s\n",
            w, h, glGetString(GL_VERSION), glGetString(GL_RENDERER));
    glViewport(0, 0, w, h);
    glClearColor(0.05f, 0.05f, 0.10f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    dump_frame(frame_path());
    eglSwapBuffers(display, surface);
}


static void select_legacy_client(uint32_t pid)
{
    struct Client { uint32_t pid; EGLContext context; EGLSurface surface; GLuint *objects;
                    struct BlendMetadata *blend; };
    static struct Client clients[32];
    static unsigned count;
    if (pid == current_pid) return;
    ensure_window(0,0);
    unsigned i;
    for (i=0;i<count;i++) if (clients[i].pid==pid) break;
    if (i==count) {
        if (count==32) { fprintf(stderr,"Too many graphics clients\n"); exit(1); }
        const EGLint ca[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};
        const EGLint pb[]={EGL_WIDTH,win_w,EGL_HEIGHT,win_h,EGL_NONE};
        clients[i].pid=pid;
        clients[i].context=eglCreateContext(display,config,EGL_NO_CONTEXT,ca);
        clients[i].surface=eglCreatePbufferSurface(display,config,pb);
        clients[i].objects=calloc(MAXOBJ,sizeof(GLuint));
        clients[i].blend=calloc(1,sizeof(struct BlendMetadata));
        if (!clients[i].objects || !clients[i].blend || clients[i].context==EGL_NO_CONTEXT || clients[i].surface==EGL_NO_SURFACE) exit(1);
        count++;
    }
    surface=clients[i].surface;
    if (!eglMakeCurrent(display,surface,surface,clients[i].context)) exit(1);
    gmap=clients[i].objects;
    blend=clients[i].blend;
    current_pid=pid;
}

#include "egl-host-darwin.h"
#include "native-readback-host.h"
#include "gl-buffer-map-host.h"

struct ME_ObjectName {GLuint native;uint32_t group;EGLContext context;unsigned char kind;};
static void me_name_set(uint32_t pid,uint32_t tid,uint32_t id,GLuint native,unsigned kind)
{
 gmap[id]=native;if(!meb_qnx_strict)return;
 struct ME_Owner *o=me_owner(pid,0);struct ME_Thread *t=me_thread(pid,tid,0);if(!o||!t)exit(1);
 if(id>=o->name_count){unsigned count=o->name_count?o->name_count:64;while(count<=id&&count<MAXOBJ)count*=2;
  struct ME_ObjectName *names=realloc(o->object_names,(size_t)count*sizeof(*names));if(!names)exit(1);
  memset(names+o->name_count,0,(count-o->name_count)*sizeof(*names));o->object_names=names;o->name_count=count;
 }
 o->object_names[id]=(struct ME_ObjectName){native,me_context_group(pid,tid),t->context,(unsigned char)kind};
}
static unsigned me_binding_kind(GLenum pname)
{
 switch(pname){
  case GL_CURRENT_PROGRAM:return 2;
  case GL_ARRAY_BUFFER_BINDING:case GL_ELEMENT_ARRAY_BUFFER_BINDING:case GL_COPY_READ_BUFFER_BINDING:
  case GL_COPY_WRITE_BUFFER_BINDING:case GL_PIXEL_PACK_BUFFER_BINDING:case GL_PIXEL_UNPACK_BUFFER_BINDING:
  case GL_TRANSFORM_FEEDBACK_BUFFER_BINDING:case GL_UNIFORM_BUFFER_BINDING:return 3;
  case GL_TEXTURE_BINDING_2D:case GL_TEXTURE_BINDING_CUBE_MAP:case GL_TEXTURE_BINDING_3D:case GL_TEXTURE_BINDING_2D_ARRAY:return 4;
  case GL_DRAW_FRAMEBUFFER_BINDING:case GL_READ_FRAMEBUFFER_BINDING:return 5;
  case GL_RENDERBUFFER_BINDING:return 6;
  default:return 0;
 }
}
static uint32_t me_binding_name(uint32_t pid,uint32_t tid,GLenum pname,GLuint native)
{
 if(!native)return 0;unsigned kind=me_binding_kind(pname);struct ME_Owner *o=me_owner(pid,0);struct ME_Thread *t=me_thread(pid,tid,0);
 if(o&&t){uint32_t group=me_context_group(pid,tid);
  for(unsigned i=o->name_count;i>1;i--){struct ME_ObjectName *n=&o->object_names[i-1];
   if(n->kind==kind&&n->native==native&&(kind==5?n->context==t->context:n->group==group))return i-1;
  }
 }
 fprintf(stderr,"Untracked original GL binding pid=%u tid=%u pname=%x native=%u kind=%u\n",pid,tid,pname,native,kind);return UINT32_MAX;
}

static uint32_t request_tid;
static void select_client(uint32_t pid)
{
    if(me_owner(pid,0)){
        if(!me_select(pid,request_tid)){
            fprintf(stderr,"GL call without a current EGL context: owner=%u tid=%u\n",pid,request_tid);
            exit(2);
        }
        return;
    }
    select_legacy_client(pid);
}
#include "native_frames.h"

static void pump_events(void) {}

static void check_shader(const char *what, GLuint obj, int is_prog)
{
    GLint ok = 1;
    if (is_prog) glGetProgramiv(obj, GL_LINK_STATUS, &ok);
    else         glGetShaderiv(obj, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024]; GLsizei n = 0;
        if (is_prog) glGetProgramInfoLog(obj, sizeof(log), &n, log);
        else         glGetShaderInfoLog(obj, sizeof(log), &n, log);
        log[n < (GLsizei)sizeof(log) ? n : (GLsizei)sizeof(log) - 1] = 0;
        fprintf(stderr, "glhost: %s FAILED: %s\n", what, log);
    }
}


static int send_program_reflection(uint32_t client_program)
{
 uint32_t header[8]={0,client_program,0,0,0,0,0,0};GLuint program=M(client_program);GLint values[5]={0};
 if(!glIsProgram(program))return me_write_all(header,sizeof(header));
 glGetProgramiv(program,GL_LINK_STATUS,&values[0]);
 if(!values[0])return me_write_all(header,sizeof(header));
 glGetProgramiv(program,GL_ACTIVE_ATTRIBUTES,&values[1]);glGetProgramiv(program,GL_ACTIVE_UNIFORMS,&values[2]);
 glGetProgramiv(program,GL_ACTIVE_ATTRIBUTE_MAX_LENGTH,&values[3]);glGetProgramiv(program,GL_ACTIVE_UNIFORM_MAX_LENGTH,&values[4]);
 if(values[1]<0||values[1]>16384||values[2]<0||values[2]>16384||values[3]<0||values[3]>4096||values[4]<0||values[4]>4096)return me_write_all(header,sizeof(header));
 size_t capacity=(size_t)values[1]*(24+values[3])+(size_t)values[2]*(24+values[4]);
 if(capacity>1024*1024)return me_write_all(header,sizeof(header));
 unsigned char *data=malloc(capacity?capacity:1);if(!data)return me_write_all(header,sizeof(header));
 size_t used=0;int okay=1;
 for(unsigned kind=1;kind<=2&&okay;kind++)for(unsigned index=0;index<(unsigned)values[kind];index++){
  GLsizei length=0;GLint size=0;GLenum type=0;char name[4096]={0};
  if(kind==1)glGetActiveAttrib(program,index,sizeof(name),&length,&size,&type,name);else glGetActiveUniform(program,index,sizeof(name),&length,&size,&type,name);
  if(length<0||length>=4096||used+25+length>capacity){okay=0;break;}
  name[length]=0;GLint location=kind==1?glGetAttribLocation(program,name):glGetUniformLocation(program,name);
  uint32_t entry[6]={kind,index,(uint32_t)size,type,(uint32_t)length+1,(uint32_t)location};
  memcpy(data+used,entry,sizeof(entry));used+=sizeof(entry);memcpy(data+used,name,length+1);used+=length+1;
 }
 if(okay){header[0]=1;for(unsigned i=0;i<5;i++)header[2+i]=(uint32_t)values[i];header[7]=(uint32_t)used;}
 int result=me_write_all(header,sizeof(header))&&(!header[7]||me_write_all(data,header[7]));free(data);return result;
}

static int valid_record(uint32_t op,uint32_t len,const uint8_t *p)
{
    static const int sizes[120] = {
        [1]=9,
        [2]=17,
        [3]=17,
        [4]=5,
        [5]=1,
        [6]=1,
        [7]=9,
        [9]=5,
        [10]=5,
        [11]=9,
        [12]=5,
        [13]=5,
        [16]=9,
        [19]=25,
        [20]=5,
        [21]=5,
        [29]=9,
        [30]=9,
        [31]=13,
        [32]=17,
        [33]=5,
        [34]=5,
        [35]=9,
        [36]=5,
        [37]=5,
        [38]=5,
        [39]=5,
        [40]=5,
        [42]=5,
        [43]=9,
        [51]=9,
        [53]=13,
        [54]=5,
        [55]=9,
        [57]=5,
        [58]=13,
        [60]=9,
        [61]=9,
        [62]=21,
        [63]=17,
        [64]=17,
        [65]=17,
        [66]=17,
        [67]=5,
        [68]=5,
        [69]=13,
        [70]=13,
        [71]=17,
        [72]=5,
        [73]=9,
        [75]=9,
        [76]=17,
        [77]=17,
        [84]=21,
        [85]=9,
        [86]=17,
        [87]=9,
        [102]=9,
        [103]=9,
        [104]=5,
        [105]=1,
        [106]=5,
        [107]=5,
        [108]=9,
        [109]=5,
        [119]=5
    };
    if(op==134)return (len==12 || (len==16 && u32(p,12)<=1)) && u32(p,4)>0 && u32(p,4)<=2048 && u32(p,8)>0 && u32(p,8)<=2048;
    if(op==135)return 0;
    if(op==136)return len==68;
    if(op==137)return len==4;
    if(op==131)return len==28 && i32(p,8)>0 && i32(p,8)<=2048 &&
        i32(p,12)>0 && i32(p,12)<=2048 && u32(p,16)==GL_RGBA && u32(p,20)==GL_UNSIGNED_BYTE;
    if(op==132)return len==12 && u32(p,4)>0 && u32(p,4)<=2048 && u32(p,8)>0 && u32(p,8)<=2048;
    if(op==133)return len==4;
    if(op==128)return len>=24 && u32(p,0)<16 && u32(p,20)==len-24;
    if(op==130)return len>=8 && u32(p,0)<=3 && (uint64_t)u32(p,4)*4==len-8;
    if(op==129)return len>=16 && u32(p,12)==len-16 && (uint64_t)u32(p,4)*(u32(p,8)==GL_UNSIGNED_BYTE?1:u32(p,8)==GL_UNSIGNED_SHORT?2:4)==len-16;
    if(op==127)return len>=24 && u32(p,4)>0 && u32(p,4)<=2048 && u32(p,8)>0 && u32(p,8)<=2048 &&
        !(u32(p,4)&1) && !(u32(p,8)&1) && u32(p,12)>0 && u32(p,12)<=60 &&
        u32(p,16)>=64000 && u32(p,16)<=20000000 && u32(p,20)<=1 && (uint64_t)u32(p,4)*u32(p,8)*4==len-24;
    if(op==138)return len==12 && u32(p,0)<=16*1024*1024 &&
        (u32(p,4)==GL_UNSIGNED_BYTE || u32(p,4)==GL_UNSIGNED_SHORT || u32(p,4)==GL_UNSIGNED_INT);
    if(op==122 || op==126)return len==4;
    if(op==123 || op==124)return len==12 && u32(p,8)<=4096;
    if(op==125)return len==4 && u32(p,0)==1;
    if(op==120)return len==24 && i32(p,8)>0 && i32(p,8)<=2048 &&
        i32(p,12)>0 && i32(p,12)<=2048 && u32(p,16)==GL_RGBA && u32(p,20)==GL_UNSIGNED_BYTE;
    if(op==170||op==171)return len==0;
    if(op==172)return len==4;
    if(op==173)return len==32&&i32(p,8)>0&&i32(p,8)<=2048&&i32(p,12)>0&&i32(p,12)<=2048&&u32(p,16)==GL_RGBA&&u32(p,20)==GL_UNSIGNED_BYTE;
    if(op==200)return me_egl_record_valid(len,p);
    if(op==119)return len==4||len==8;
    if(op==158){if(len<4)return 0;int32_t n=i32(p,0);return n<0?len==4:n<=4096&&len==4+(uint32_t)n*4;}
    if(op==159)return len==4;
    if(op>=153&&op<=157)return me_buffer_record_valid(op,len,p);
    if(op==150){
        if(len<8||u32(p,0)<1||u32(p,0)>2)return 0;int32_t n=i32(p,4);
        if(n<0)return len==8;
        if(n>4096||len!=8+(uint32_t)n*4)return 0;
        if(u32(p,0)==1)for(int32_t i=0;i<n;i++)if(!u32(p,8+i*4)||u32(p,8+i*4)>=MAXOBJ)return 0;
        return 1;
    }
    if(op==151){if(len<16)return 0;uint32_t kind=u32(p,0);if(kind<1||kind>5)return 0;return len==((kind>=4&&u32(p,8)==GL_TEXTURE_BORDER_COLOR_EXT)?28:16);}
    if(op==152)return len==12&&u32(p,0)>=1&&u32(p,0)<=3;
    if(op==148)return len==12&&(u32(p,0)==1||u32(p,0)==2)&&u32(p,4)<MAXOBJ&&i32(p,8)<=1024*1024;
    if(op==160)return len==8;
    if(op==161)return len>=12&&(u32(p,0)==1||u32(p,0)==2)&&len==12+((u32(p,8)==GL_TEXTURE_BORDER_COLOR_EXT||u32(p,8)==0x8e46)?16:4);
    if(op==162)return len==12&&(u32(p,0)==1||u32(p,0)==2);
    if(op==147)return len==8&&u32(p,0)>=1&&u32(p,0)<=4;
    if(op==146)return len==4;
    if(op==140)return len==4;
    if(op==141)return len==12 && u32(p,0)>=1 && u32(p,0)<=3 && u32(p,4)<MAXOBJ && u32(p,8)<MAXOBJ;
    if(op==142)return len==8 && u32(p,0)>=1 && u32(p,0)<=2 && u32(p,4)<MAXOBJ;
    if(op==143)return len==12 && u32(p,0)<MAXOBJ;
    if(op==145)return len==8 && u32(p,0)<MAXOBJ && i32(p,4)<=16*1024*1024;
    if(op==168)return len>0&&len<=32768;
    if(op==144||op==166||op==167){
        if(!len||len>128)return 0;
        for(unsigned i=0;i<len;i++)if(!((p[i]>='a'&&p[i]<='z')||(p[i]>='A'&&p[i]<='Z')||(p[i]>='0'&&p[i]<='9')||p[i]=='_'))return 0;
        return 1;
    }
    if(op>=120)return 0;
    if(sizes[op] && len!=(unsigned)sizes[op]-1)return 0;
    if(op==7 || op==10 || op==12 || op==13)return u32(p,0)<MAXOBJ;
    if(op==11)return u32(p,0)<MAXOBJ && u32(p,4)<MAXOBJ;
    if(op==15 || op==50 || op==78 || op==79) {
        if(len<4 || (len-4)%4 || u32(p,0)!=(len-4)/4)return 0;
        for(unsigned i=4;i<len;i+=4)if(u32(p,i)>=MAXOBJ)return 0;
        return 1;
    }
    if(op==8 || op==100 || op==101)return len>=8 && u32(p,0)<MAXOBJ && u32(p,4)==len-8;
    if(op==14)return len>=12 && u32(p,8)==len-12;
    if(op==17)return len>=12 && (len==12 || u32(p,4)==len-12);
    if(op==18)return len>=12 && u32(p,8)==len-12;
    if(op>=22 && op<=25)return len>=8 && (uint64_t)u32(p,4)*(op-21)*4==len-8;
    if(op>=26 && op<=28)return len>=12 && (uint64_t)u32(p,4)*(op-24)*(op-24)*4==len-12;
    if(op>=80 && op<=83)return len>=8 && (uint64_t)u32(p,4)*(op-79)*4==len-8;
    if(op==52 || op==56)return len>=36 && u32(p,32)==len-36;
    if(op==110) {
        if(len<12 || (uint64_t)u32(p,0)*4+u32(p,8)!=len-12)return 0;
        for(unsigned i=0;i<u32(p,0);i++)if(u32(p,12+4*i)>=MAXOBJ)return 0;
        return 1;
    }
    return sizes[op]!=0;
}

static GLint me_gl_state_count(GLenum pname)
{
 GLint count=1;
 switch(pname){
  case GL_VIEWPORT:case GL_SCISSOR_BOX:case GL_COLOR_WRITEMASK:case GL_BLEND_COLOR:case GL_COLOR_CLEAR_VALUE:count=4;break;
  case GL_MAX_VIEWPORT_DIMS:case GL_DEPTH_RANGE:case GL_ALIASED_LINE_WIDTH_RANGE:case GL_ALIASED_POINT_SIZE_RANGE:count=2;break;
  case GL_COMPRESSED_TEXTURE_FORMATS:glGetIntegerv(GL_NUM_COMPRESSED_TEXTURE_FORMATS,&count);break;
  case GL_SHADER_BINARY_FORMATS:glGetIntegerv(GL_NUM_SHADER_BINARY_FORMATS,&count);break;
  case GL_PROGRAM_BINARY_FORMATS:glGetIntegerv(GL_NUM_PROGRAM_BINARY_FORMATS,&count);break;
 }
 return count;
}

static void serve(int cfd)
{
    uint8_t *p = NULL;
    size_t cap = 0;
    unsigned long frames = 0;
    uint32_t request_pid = 0;
    const char *capture_path=getenv("MHI2_GL_CAPTURE");
    FILE *capture = capture_path && *capture_path ? fopen(capture_path, "wb") : NULL;
    bool trace = getenv("MHI2_GL_TRACE") != NULL;
    bool dump_clients = getenv("MHI2_GL_DUMP_CLIENTS") != NULL;

    for (;;) {
        uint32_t hdr[2];
        if (read_full(cfd, hdr, 8) < 0) break;
        uint32_t op = hdr[0], len = hdr[1];
        if (len > 64 * 1024 * 1024) break;
        if (trace) fprintf(stderr, "glhost op=%u len=%u\n", op, len);
        if (len > cap) {
            cap = len + 4096;
            p = realloc(p, cap);
            if (!p) { fprintf(stderr, "glhost: OOM %u\n", len); break; }
        }
        if (len && read_full(cfd, p, len) < 0) break;
        if(capture){fwrite(hdr,1,8,capture);fwrite(p,1,len,capture);fflush(capture);}
        
        if(op==128 && len==12)op=138;
        if(!valid_record(op,len,p)){
            fprintf(stderr,"Invalid graphics record: op=%u len=%u\n",op,len);
            rejected_record = 1; break;
        }
        if (op == 122 || op == 126 || op == 127 || (op >= 134 && op <= 137)) {
            fprintf(stderr,"Unsupported headunit video record: op=%u\n",op);
            rejected_record = 1; break;
        }
        if(op==170){
            if(!meb_qnx_strict||request_pid!=meb_qnx_pid||!request_tid){rejected_record=1;free(p);return;}
            static unsigned sequence;struct timespec now;clock_gettime(CLOCK_MONOTONIC,&now);char path[4096],tmp[4096];snprintf(path,sizeof(path),"%s.heartbeat.json",frame_path());snprintf(tmp,sizeof(tmp),"%s.tmp",path);FILE *f=fopen(tmp,"w");
            if(!f){rejected_record=1;free(p);return;}
            fprintf(f,"{\"source\":\"%s\",\"pid\":%u,\"tid\":%u,\"sequence\":%u,\"monotonic\":%.9f}\n",meb_qnx_fixture?"qnx_gl_fixture_test":"original_qnx_client_liveness",request_pid,request_tid,++sequence,now.tv_sec+now.tv_nsec/1e9);
            if(fclose(f)||rename(tmp,path)){rejected_record=1;free(p);return;}continue;
        }
        if(op==171){
            if(!meb_qnx_strict||request_pid!=meb_qnx_pid||!request_tid){rejected_record=1;free(p);return;}
            char name[4096],claimed[4096],command[2049];uint32_t n=0;
            int a=snprintf(name,sizeof(name),"%s.command",frame_path());
            int b=snprintf(claimed,sizeof(claimed),"%s.command.claimed",frame_path());
            if(a>0&&a<(int)sizeof(name)&&b>0&&b<(int)sizeof(claimed)&&rename(name,claimed)==0){
                int fd=open(claimed,O_RDONLY|O_NOFOLLOW);struct stat st;
                if(fd>=0){if(!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&!(st.st_mode&077)&&st.st_size>0&&st.st_size<=2048){
                    ssize_t got=read(fd,command,(size_t)st.st_size);if(got==st.st_size&&!memchr(command,0,(size_t)got))n=(uint32_t)got;
                }close(fd);}unlink(claimed);
            }
            if(!me_write_all(&n,4)||(n&&!me_write_all(command,n))){rejected_record=1;free(p);return;}continue;
        }
        if(op==168){fprintf(stderr,"QNX_NATIVE_OBSERVER pid=%u tid=%u bytes=%u\n",request_pid,request_tid,len);fwrite(p,1,len,stderr);fflush(stderr);continue;}
        if(op==167){fprintf(stderr,"QNX_UNRESOLVED_PROC pid=%u tid=%u symbol=%.*s\n",request_pid,request_tid,(int)len,p);fflush(stderr);continue;}
        if(op==166){fprintf(stderr,"QNX_CLIENT_VALIDATION_ABORT pid=%u tid=%u function=%.*s\n",request_pid,request_tid,(int)len,p);fflush(stderr);continue;}
        if(op==144){
            fprintf(stderr,"ORIGINAL_HMI_NATIVE_UNSUPPORTED pid=%u tid=%u symbol=%.*s\n",request_pid,request_tid,(int)len,p);
            fflush(stderr);continue;
        }
        pump_events();
        
        if(op!=200 && op!=119 && op!=122 && op!=126 && op!=127 && !(op>=134&&op<=137))select_client(request_pid);

        if(op>=153&&op<=157){if(!me_buffer_dispatch(request_pid,request_tid,op,p)){fprintf(stderr,"Mapped-buffer transport rejected op=%u\n",op);free(p);return;}continue;}
        switch (op) {
        case 158: {GLsizei n=i32(p,0);glDrawBuffers(n,n>0?(const GLenum *)(p+4):NULL);break;}
        case 159: glReadBuffer(u32(p,0));break;
        case 150: {
            struct ME_Owner *o=me_owner(request_pid,0);if(!o){rejected_record=1;free(p);return;}
            int32_t count=i32(p,4);uint32_t n=count>0?(uint32_t)count:0,reply_count=u32(p,0)==1?n:0;
            GLuint *native=calloc(n?n:1,sizeof(*native));uint32_t *names=calloc(n?n:1,sizeof(*names));if(!native||!names){free(native);free(names);free(p);return;}
            if(u32(p,0)==1){
                glGenSamplers(count,native);
                for(uint32_t i=0;i<n;i++){uint32_t id=u32(p,8+i*4);o->samplers[id]=native[i];if(id>o->sampler_limit)o->sampler_limit=id;names[i]=native[i]?id:0;}
            }else{
                for(uint32_t i=0;i<n;i++)native[i]=me_sampler(request_pid,u32(p,8+i*4));
                glDeleteSamplers(count,native);
                for(uint32_t i=0;i<n;i++){uint32_t id=u32(p,8+i*4);if(id<MAXOBJ)o->samplers[id]=0;}
            }
            int okay=me_write_all(&reply_count,4)&&me_write_all(names,reply_count*4);free(native);free(names);if(!okay){free(p);return;}break;
        }
        case 151: {
            uint32_t kind=u32(p,0),id=u32(p,4),pname=u32(p,8);GLuint native=me_sampler(request_pid,id);
            if(kind==1){GLuint value=me_sampler(request_pid,pname);if(pname&&!value){fprintf(stderr,"Unknown virtual sampler name %u\n",pname);rejected_record=1;free(p);return;}glBindSampler(id,value);}
            else if(kind==2)glSamplerParameteri(native,pname,i32(p,12));
            else if(kind==3){GLfloat value;memcpy(&value,p+12,4);glSamplerParameterf(native,pname,value);}
            else if(kind==4)glSamplerParameteriv(native,pname,(const GLint *)(p+12));
            else glSamplerParameterfv(native,pname,(const GLfloat *)(p+12));break;
        }
        case 152: {
            uint32_t kind=u32(p,0),pname=u32(p,8),out[4]={0};GLuint native=me_sampler(request_pid,u32(p,4));
            if(kind==1)out[0]=glIsSampler(native);
            else if(kind==2)glGetSamplerParameteriv(native,pname,(GLint *)out);
            else glGetSamplerParameterfv(native,pname,(GLfloat *)out);
            if(!me_write_all(out,(kind!=1&&pname==GL_TEXTURE_BORDER_COLOR_EXT?4:1)*4)){free(p);return;}break;
        }
        case 161: {
            if(u32(p,0)==1)glTexParameteriv(u32(p,4),u32(p,8),(const GLint *)(p+12));
            else glTexParameterfv(u32(p,4),u32(p,8),(const GLfloat *)(p+12));break;
        }
        case 162: {
            uint32_t out[4]={0};if(u32(p,0)==1)glGetTexParameteriv(u32(p,4),u32(p,8),(GLint *)out);
            else glGetTexParameterfv(u32(p,4),u32(p,8),(GLfloat *)out);
            if(!me_write_all(out,16)){free(p);return;}break;
        }
        case 160: glVertexAttribDivisor(u32(p,0),u32(p,4));break;
        case 148: {
            GLsizei capacity=i32(p,8),length=0;char *log=calloc(capacity>0?(size_t)capacity:1,1);if(!log){free(p);return;}
            if(u32(p,0)==1)glGetShaderInfoLog(M(u32(p,4)),capacity,&length,log);
            else glGetProgramInfoLog(M(u32(p,4)),capacity,&length,log);
            if(length<0||length>(capacity>0?capacity-1:0)){free(log);rejected_record=1;free(p);return;}
            uint32_t n=(uint32_t)length;int sent=me_write_all(&n,4)&&me_write_all(log,n);free(log);if(!sent){free(p);return;}break;
        }
        case 147: {
            uint32_t kind=u32(p,0);GLenum pname=u32(p,4);GLint count=kind==4?1:me_gl_state_count(pname);
            if(count<0||count>4096){rejected_record=1;free(p);return;}
            size_t width=(kind==2||kind==4)?1:4;void *values=calloc(count>0?(size_t)count:1,width);
            if(!values){free(p);return;}
            if(kind==1)glGetIntegerv(pname,values);
            else if(kind==2)glGetBooleanv(pname,values);
            else if(kind==3)glGetFloatv(pname,values);
            else *(GLboolean *)values=glIsEnabled(pname);
            if(count&&pname==GL_SAMPLER_BINDING&&(kind==1||kind==3)){
                GLint native=0;glGetIntegerv(pname,&native);GLuint name=me_sampler_name(request_pid,(GLuint)native);
                if(kind==1)*(GLint *)values=(GLint)name;else *(GLfloat *)values=(GLfloat)name;
            }
            if(count&&me_binding_kind(pname)&&(kind==1||kind==3)){
                GLint native=0;glGetIntegerv(pname,&native);uint32_t name=me_binding_name(request_pid,request_tid,pname,(GLuint)native);
                if(name==UINT32_MAX){free(values);rejected_record=1;free(p);return;}
                if(kind==1)*(GLint *)values=(GLint)name;else *(GLfloat *)values=(GLfloat)name;
            }
            if(trace)fprintf(stderr,"GL native query kind=%u pname=%x count=%d first=%08x\n",kind,pname,count,count&&width==4?*(uint32_t *)values:count?*(unsigned char *)values:0);
            uint32_t n=(uint32_t)count;int sent=me_write_all(&n,4)&&me_write_all(values,(size_t)count*width);
            free(values);if(!sent){free(p);return;}break;
        }
        case 146: {
            GLenum pname=u32(p,0);GLint count=me_gl_state_count(pname);
            if(count<0||count>4096){rejected_record=1;free(p);return;}
            GLint64 *values=calloc(count>0?(size_t)count:1,sizeof(*values));if(!values){free(p);return;}
            glGetInteger64v(pname,values);if(pname==GL_SAMPLER_BINDING&&count)values[0]=me_sampler_name(request_pid,(GLuint)values[0]);
            if(count&&me_binding_kind(pname)){uint32_t name=me_binding_name(request_pid,request_tid,pname,(GLuint)values[0]);if(name==UINT32_MAX){free(values);rejected_record=1;free(p);return;}values[0]=name;}uint32_t n=(uint32_t)count;
            int sent=me_write_all(&n,4)&&me_write_all(values,(size_t)count*8);free(values);if(!sent){free(p);return;}break;
        }
        case 140: {
            const GLubyte *value=glGetString(u32(p,0));
            uint32_t length=value?(uint32_t)strlen((const char *)value):0;
            if(length>16384){rejected_record=1;free(p);return;}
            if(write(STDOUT_FILENO,&length,4)!=4)return;
            size_t done=0;
            while(done<length){ssize_t n=write(STDOUT_FILENO,value+done,length-done);
                if(n<0&&errno==EINTR)continue;
                if(n<=0)return;done+=n;}
            break;
        }
        case 142: {
            uint32_t value=u32(p,0)==1?glIsShader(M(u32(p,4))):glIsProgram(M(u32(p,4)));
            if(write(STDOUT_FILENO,&value,4)!=4)return;
            break;
        }
        case 143: glProgramParameteri(M(u32(p,0)),u32(p,4),i32(p,8));break;
        case 145: {
            GLsizei capacity=i32(p,4),length=0;GLenum format=0;
            unsigned char *binary=malloc(capacity>0?(size_t)capacity:1);
            if(!binary){free(p);return;}
            glGetProgramBinary(M(u32(p,0)),capacity,&length,&format,binary);
            if(length<0||length>(capacity>0?capacity:0)){rejected_record=1;free(binary);break;}
            uint32_t header[2]={(uint32_t)length,format};
            int sent=me_write_all(header,sizeof(header))&&me_write_all(binary,(size_t)length);
            free(binary);if(!sent){free(p);return;}break;
        }
        case 141:
            if(u32(p,0)==1)glDeleteShader(M(u32(p,4)));
            else if(u32(p,0)==2)glDeleteProgram(M(u32(p,4)));
            else glDetachShader(M(u32(p,4)),M(u32(p,8)));
            break;
        case 200:
            current_pid=0;
            if(!me_egl_dispatch(request_pid,request_tid,p,len)){free(p);return;}
            break;
        case 119: request_pid=u32(p,0);request_tid=len==8?u32(p,4):0;if(meb_qnx_strict&&request_pid!=meb_qnx_pid){rejected_record=1;free(p);return;}break;
        case 125: primary_display_pid=current_pid;break;
        case 172: if(!send_program_reflection(u32(p,0))){free(p);return;}break;
        case 173: {
            GLint buffer=0;EGLint version=2;struct ME_Thread *thread=me_thread(request_pid,request_tid,0);if(thread)eglQueryContext(thread->display,thread->context,EGL_CONTEXT_CLIENT_VERSION,&version);if(version>=3)glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING,&buffer);uint64_t offset;memcpy(&offset,p+24,8);uint32_t bytes=0;
            if(buffer){
                glReadPixels(i32(p,0),i32(p,4),i32(p,8),i32(p,12),u32(p,16),u32(p,20),(void *)(uintptr_t)offset);
                me_readback_request(request_pid,request_tid,(GLuint)buffer,offset,u32(p,8),u32(p,12));
                if(!me_write_all(&bytes,4)){free(p);return;}
            }else if(offset){
                bytes=u32(p,8)*u32(p,12)*4;unsigned char *pixels=calloc(1,bytes);if(!pixels){free(p);return;}
                GLint alignment=4;glGetIntegerv(GL_PACK_ALIGNMENT,&alignment);glPixelStorei(GL_PACK_ALIGNMENT,1);
                glReadPixels(i32(p,0),i32(p,4),i32(p,8),i32(p,12),u32(p,16),u32(p,20),pixels);glPixelStorei(GL_PACK_ALIGNMENT,alignment);
                int okay=me_write_all(&bytes,4)&&me_write_all(pixels,bytes);free(pixels);if(!okay){free(p);return;}
            }else if(!me_write_all(&bytes,4)){free(p);return;}
            break;
        }
        case 123: case 124: {
            uint32_t count=u32(p,8),header[3]={0};
            char name[4096]={0};GLsizei length=0;GLint size=0;GLenum type=0;
            if(op==123)glGetActiveAttrib(M(u32(p,0)),u32(p,4),count,&length,&size,&type,name);
            else glGetActiveUniform(M(u32(p,0)),u32(p,4),count,&length,&size,&type,name);
            header[0]=length;header[1]=size;header[2]=type;
            if(write(STDOUT_FILENO,header,12)!=12)return;
            unsigned done=0;while(done<count){ssize_t n=write(STDOUT_FILENO,name+done,count-done);
                if(n<0&&errno==EINTR)continue;if(n<=0)return;done+=n;}
            break;
        }
        case 132: {
            uint32_t hit=native_frame_upload(u32(p,0),u32(p,4),u32(p,8));
            if(write(STDOUT_FILENO,&hit,4)!=4)return;
            break;
        }
        case 133: native_frame_remove(u32(p,0));break;
        case 120: case 131: {
            size_t size=(size_t)u32(p,8)*u32(p,12)*4;
            unsigned char *pixels=calloc(1,size);if(!pixels)return;
            GLint alignment;glGetIntegerv(GL_PACK_ALIGNMENT,&alignment);
            glPixelStorei(GL_PACK_ALIGNMENT,1);
            glReadPixels(i32(p,0),i32(p,4),i32(p,8),i32(p,12),GL_RGBA,GL_UNSIGNED_BYTE,pixels);
            glPixelStorei(GL_PACK_ALIGNMENT,alignment);
            size_t done=0;
            while(done<size){ssize_t n=write(STDOUT_FILENO,pixels+done,size-done);
                if(n<0&&errno==EINTR)continue;
                if(n<=0){free(pixels);return;}done+=n;}
            if(op==131)native_frame_store(u32(p,24),u32(p,8),u32(p,12),pixels);
            else free(pixels);
            break;
        }
        case 1:  ensure_window(len >= 8 ? i32(p, 0) : 0, len >= 8 ? i32(p, 4) : 0); break;
        case 2:  ensure_window(0, 0); glViewport(i32(p,0), i32(p,4), i32(p,8), i32(p,12)); break;
        case 3:  ensure_window(0, 0); { float c[4]; memcpy(c, p, 16);
                 glClearColor(c[0], c[1], c[2], c[3]); } break;
        case 4:  ensure_window(0, 0); menu_texture_reset_draw(); glClear(u32(p, 0)); break;
        case 5:  ensure_window(0, 0);
                 menu_texture_reset_draw();
                 if(!primary_display_pid || current_pid==primary_display_pid)
                     dump_frame(frame_path());
                 if(dump_clients){char path[128];snprintf(path,sizeof(path),"/tmp/mhi2-client-%u.ppm",current_pid);dump_frame(path);}
                 eglSwapBuffers(display, surface);
                 if (frames < 3 || (frames % 120) == 0)
                     fprintf(stderr, "glhost: %lu frames swapped\n", frames + 1);
                 frames++; break;
        case 6:  ensure_window(0, 0); glFinish(); break;

        case 7: { 
            uint32_t id = u32(p,0), type = u32(p,4);
            blend->shader[id]=0;
            me_name_set(request_pid,request_tid,id,glCreateShader(type),1); } break;
        case 8: { 
            uint32_t id = u32(p,0); GLint sl = (GLint)u32(p,4);
            const GLchar *src = (const GLchar *)(p + 8);
            char path[128];snprintf(path,sizeof(path),"/tmp/mhi2-source-%u.glsl",id);
            FILE *f=fopen(path,"wb");if(f){fwrite(src,1,sl,f);fclose(f);}
            blend->shader[id]=0;
            glShaderSource(M(id), 1, &src, &sl); } break;
        case 9:  glCompileShader(M(u32(p,0))); check_shader("compile", M(u32(p,0)), 0); break;
        case 10:
            blend->program[u32(p,0)]=0;blend->fragment[u32(p,0)]=0;
            me_name_set(request_pid,request_tid,u32(p,0),glCreateProgram(),2); break;
        case 11: {
            GLint type=0;glGetShaderiv(M(u32(p,4)),GL_SHADER_TYPE,&type);
            if(type==GL_FRAGMENT_SHADER)blend->fragment[u32(p,0)]=u32(p,4);
            glAttachShader(M(u32(p,0)), M(u32(p,4))); break;
        }
        case 12:
            glLinkProgram(M(u32(p,0))); check_shader("link", M(u32(p,0)), 1);
            blend->program[u32(p,0)]=blend->shader[blend->fragment[u32(p,0)]];break;
        case 13: {
            uint32_t id=u32(p,0);GLint active=0;
            glUseProgram(M(id));glGetIntegerv(GL_CURRENT_PROGRAM,&active);
            if((GLuint)active==M(id))blend->active_program=id;break;
        }
        case 14: { 
            uint32_t pid = u32(p,0), idx = u32(p,4), nl = u32(p,8);
            char nm[256]; uint32_t k = nl < 255 ? nl : 255; memcpy(nm, p+12, k); nm[k]=0;
            glBindAttribLocation(M(pid), idx, nm); } break;

        case 15: { 
            uint32_t n = u32(p,0);
            for (uint32_t i = 0; i < n; i++) { GLuint b; glGenBuffers(1, &b); me_name_set(request_pid,request_tid,u32(p,4+i*4),b,3); }
            } break;
        case 16: glBindBuffer(u32(p,0), M(u32(p,4))); break;
        case 17: { 
            uint32_t tgt = u32(p,0), sz = u32(p,4), usage = u32(p,8);
            const void *data = (len >= 12 + sz && sz) ? (p + 12) : NULL;
            glBufferData(tgt, sz, data, usage); } break;
        case 18: { 
            uint32_t tgt = u32(p,0), off = u32(p,4), sz = u32(p,8);
            glBufferSubData(tgt, off, sz, (len >= 12 + sz && sz) ? (p + 12) : NULL); } break;

        case 19: { 
            GLint binding = 0;
            glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &binding);
            if (!binding) {
                fprintf(stderr, "glhost: vertex array lacks transferred buffer\n");
                return;
            }
            glVertexAttribPointer(u32(p,0), i32(p,4), u32(p,8), u32(p,12) ? GL_TRUE : GL_FALSE,
                                  i32(p,16), (const void *)(uintptr_t)u32(p,20)); break;
        }
        case 138: { 
            uint32_t maximum = UINT32_MAX, count = u32(p,0), type = u32(p,4), offset = u32(p,8);
            unsigned width = type == GL_UNSIGNED_BYTE ? 1 : (type == GL_UNSIGNED_SHORT ? 2 : (type == GL_UNSIGNED_INT ? 4 : 0));
            GLint size = 0;
            glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE, &size);
            void *(*map)(GLenum, GLintptr, GLsizeiptr, GLbitfield) = (void *)eglGetProcAddress("glMapBufferRange");
            GLboolean (*unmap)(GLenum) = (void *)eglGetProcAddress("glUnmapBuffer");
            if (width && count && map && unmap && (uint64_t)offset+(uint64_t)count*width <= (uint32_t)size) {
                const uint8_t *indices = map(GL_ELEMENT_ARRAY_BUFFER, offset, (size_t)count*width, 1 );
                if (indices) {
                    maximum = 0;
                    for (uint32_t i = 0; i < count; i++) {
                        uint32_t value = 0;
                        memcpy(&value, indices+(size_t)i*width, width);
                        if (value > maximum) maximum = value;
                    }
                    unmap(GL_ELEMENT_ARRAY_BUFFER);
                }
            }
            fwrite(&maximum, 4, 1, stdout); fflush(stdout);
            break;
        }
        case 20: glEnableVertexAttribArray(u32(p,0)); break;
        case 21: glDisableVertexAttribArray(u32(p,0)); break;

        case 22: glUniform1fv(i32(p,0), i32(p,4), (const GLfloat *)(p+8)); break;
        case 23: glUniform2fv(i32(p,0), i32(p,4), (const GLfloat *)(p+8)); break;
        case 24: glUniform3fv(i32(p,0), i32(p,4), (const GLfloat *)(p+8)); break;
        case 25: glUniform4fv(i32(p,0), i32(p,4), (const GLfloat *)(p+8)); break;
        case 26: glUniformMatrix2fv(i32(p,0), i32(p,4), u32(p,8)?GL_TRUE:GL_FALSE, (const GLfloat *)(p+12)); break;
        case 27: glUniformMatrix3fv(i32(p,0), i32(p,4), u32(p,8)?GL_TRUE:GL_FALSE, (const GLfloat *)(p+12)); break;
        case 28: glUniformMatrix4fv(i32(p,0), i32(p,4), u32(p,8)?GL_TRUE:GL_FALSE, (const GLfloat *)(p+12)); break;
        case 29: { float v; memcpy(&v, p+4, 4); glUniform1f(i32(p,0), v); } break;
        case 30: glUniform1i(i32(p,0), i32(p,4)); break;

        case 128: {
            unsigned index=u32(p,0),bytes=u32(p,20);GLint binding;
            glGetIntegerv(GL_ARRAY_BUFFER_BINDING,&binding);
            void *data=realloc(blend->client_arrays[index],bytes);
            if(!data && bytes)exit(1);blend->client_arrays[index]=data;
            blend->client_array_bytes[index]=bytes;
            memcpy(data,p+24,bytes);glBindBuffer(GL_ARRAY_BUFFER,0);
            glVertexAttribPointer(index,i32(p,4),u32(p,8),u32(p,12),i32(p,16),data);
            glBindBuffer(GL_ARRAY_BUFFER,binding);break;
        }
        case 31: case 32: case 129: {
            GLuint menu_binding=menu_texture_begin_draw(op,p,len);
            unsigned mode=blend->program[blend->active_program];
            struct BlendState saved;
            begin_binary_blend(mode,&saved);
            if(op==31)glDrawArrays(u32(p,0),i32(p,4),i32(p,8));
            else if(op==129){GLint binding;glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING,&binding);
                glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,0);glDrawElements(u32(p,0),i32(p,4),u32(p,8),p+16);
                glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,binding);
            }else glDrawElements(u32(p,0),i32(p,4),u32(p,8),(const void *)(uintptr_t)u32(p,12));
            end_binary_blend(mode,&saved);
            if(menu_binding)glBindTexture(GL_TEXTURE_2D,menu_binding);
            break;
        }

        case 33: glEnable(u32(p,0)); break;
        case 34: glDisable(u32(p,0)); break;
        case 35: glBlendFunc(u32(p,0), u32(p,4)); break;
        case 36: glDepthFunc(u32(p,0)); break;
        case 37: glCullFace(u32(p,0)); break;
        case 38: glFrontFace(u32(p,0)); break;
        case 39: glDepthMask(u32(p,0) ? GL_TRUE : GL_FALSE); break;
        case 40: { float d; memcpy(&d, p, 4); glClearDepthf(d); } break;
        case 42: { float w; memcpy(&w, p, 4); glLineWidth(w); } break;
        case 43: { float f[2]; memcpy(f, p, 8); glPolygonOffset(f[0], f[1]); } break;

        case 130: { 
            if (len < 8 || u32(p,4) > (len - 8) / 4) break;
            unsigned kind = u32(p,0), count = u32(p,4);
            if (kind > 3) break;
            for (unsigned i = 0; i < count; i++) {
                unsigned id = u32(p,8+4*i);
                if (!id || id >= MAXOBJ || !gmap[id]) continue;
                GLuint object = gmap[id];
                switch (kind) {
                case 0: menu_texture_deleted(object); glDeleteTextures(1, &object); break;
                case 1: glDeleteBuffers(1, &object); break;
                case 2: glDeleteFramebuffers(1, &object); break;
                case 3: glDeleteRenderbuffers(1, &object); break;
                }
                gmap[id] = 0;struct ME_Owner *owner=me_owner(request_pid,0);if(owner&&id<owner->name_count)owner->object_names[id].native=0;
            }
            break;
        }

        case 50: { uint32_t n = u32(p,0);
            for (uint32_t i = 0; i < n; i++) { GLuint t; glGenTextures(1, &t); me_name_set(request_pid,request_tid,u32(p,4+i*4),t,4); }
            } break;
        case 51: glBindTexture(u32(p,0), M(u32(p,4))); break;
        case 52: { 
            uint32_t sz = u32(p,32);
            if (menu_texture_upload(52,p,len)) break;
            glTexImage2D(u32(p,0), i32(p,4), i32(p,8), i32(p,12), i32(p,16), i32(p,20),
                         u32(p,24), u32(p,28), (sz && len >= 36 + sz) ? (p + 36) : NULL); } break;
        case 53: glTexParameteri(u32(p,0), u32(p,4), i32(p,8)); break;
        case 54: glActiveTexture(u32(p,0)); break;
        case 55: glPixelStorei(u32(p,0), i32(p,4)); break;
        case 56: { 
            uint32_t sz = u32(p,32);
            if (menu_texture_upload(56,p,len)) break;
            glTexSubImage2D(u32(p,0), i32(p,4), i32(p,8), i32(p,12), i32(p,16), i32(p,20),
                            u32(p,24), u32(p,28), (sz && len >= 36 + sz) ? (p + 36) : NULL); } break;
        case 57: glGenerateMipmap(u32(p,0)); break;
        case 58: { float v; memcpy(&v, p+8, 4); glTexParameterf(u32(p,0), u32(p,4), v); } break;

        case 110: {
            if(meb_qnx_strict){unsigned count=u32(p,0);GLuint *ids=malloc(count*sizeof(*ids));if(!ids){free(p);return;}for(unsigned i=0;i<count;i++)ids[i]=M(u32(p,12+4*i));glShaderBinary(count,ids,u32(p,4),p+12+count*4,u32(p,8));free(ids);break;}

            static unsigned binary_id;
            char path[128];
            uint32_t count=u32(p,0), format=u32(p,4), size=u32(p,8);
            snprintf(path,sizeof(path),"/tmp/mhi2-shader-%u-%x.bin",binary_id++,format);
            FILE *f=fopen(path,"wb");
            if(f){fwrite(p+12+count*4,1,size,f);fclose(f);}
            fprintf(stderr,"shader binary %s count=%u size=%u\n",path,count,size);
            const uint8_t *binary = p+12+count*4;
            uint64_t hash = UINT64_C(14695981039346656037);
            for (unsigned i=0;i<size;i++) hash=(hash^binary[i])*UINT64_C(1099511628211);
            bool matched=false;
            const struct ShaderTranslation *translation = count == 1 ? find_shader(hash, size) : NULL;
            if (translation) {
                GLuint shader=M(u32(p,12));
                blend->shader[u32(p,12)]=translation->blend;
                glShaderSource(shader,1,&translation->source,NULL);
                glCompileShader(shader);check_shader("translated shader",shader,0);
                matched=true;
            }
            if (!matched && count == 1) {
                unsigned mode;
                char *source = load_firmware_shader(hash, size, &mode);
                if (source) {
                    GLuint shader = M(u32(p,12));
                    blend->shader[u32(p,12)] = mode;
                    const char *text = source;
                    glShaderSource(shader, 1, &text, NULL);
                    glCompileShader(shader);
                    check_shader("firmware source shader", shader, 0);
                    free(source);
                    matched = true;
                }
            }
            if (!matched) fprintf(stderr,"UNSUPPORTED NVIDIA shader hash=%016llx\n",(unsigned long long)hash);

            break;
        }
        case 104: {
            GLint out[16]={0};
            GLenum pname = u32(p,0);
            if (pname != GL_COMPRESSED_TEXTURE_FORMATS &&
                pname != GL_SHADER_BINARY_FORMATS &&
                pname != GL_NUM_COMPRESSED_TEXTURE_FORMATS &&
                pname != GL_NUM_SHADER_BINARY_FORMATS) {
                glGetIntegerv(pname,out);
            }
            if(pname==GL_SAMPLER_BINDING)out[0]=me_sampler_name(request_pid,(GLuint)out[0]);
            if(write(STDOUT_FILENO,out,sizeof(out)) != sizeof(out))return;
            break;
        }
        case 106: {
            GLboolean out[16]={0};
            GLenum pname = u32(p,0);
            if (pname != GL_COMPRESSED_TEXTURE_FORMATS &&
                pname != GL_SHADER_BINARY_FORMATS &&
                pname != GL_NUM_COMPRESSED_TEXTURE_FORMATS &&
                pname != GL_NUM_SHADER_BINARY_FORMATS) {
                glGetBooleanv(pname,out);
            }
            if(write(STDOUT_FILENO,out,sizeof(out))!=sizeof(out))return;
            break;
        }
        case 107: {
            GLfloat out[16]={0};
            GLenum pname = u32(p,0);
            if (pname != GL_COMPRESSED_TEXTURE_FORMATS &&
                pname != GL_SHADER_BINARY_FORMATS &&
                pname != GL_NUM_COMPRESSED_TEXTURE_FORMATS &&
                pname != GL_NUM_SHADER_BINARY_FORMATS) {
                glGetFloatv(pname,out);
            }
            if(pname==GL_SAMPLER_BINDING){GLint native=0;glGetIntegerv(pname,&native);out[0]=(GLfloat)me_sampler_name(request_pid,(GLuint)native);}
            if(write(STDOUT_FILENO,out,sizeof(out))!=sizeof(out))return;
            break;
        }
        case 108: {
            GLint out[3]={0};glGetShaderPrecisionFormat(u32(p,0),u32(p,4),out,out+2);
            if(write(STDOUT_FILENO,out,sizeof(out))!=sizeof(out))return;
            break;
        }
        case 105: {
            int32_t out=glGetError();if(trace)fprintf(stderr,"GL native get-error=%x\n",out);
            if(write(STDOUT_FILENO,&out,4)!=4)return;
            break;
        }
        case 102: case 103: {
            GLint out = 0;
            if (op == 102) glGetShaderiv(M(u32(p,0)),u32(p,4),&out);
            else glGetProgramiv(M(u32(p,0)),u32(p,4),&out);
            if (write(STDOUT_FILENO,&out,4) != 4) return;
            break;
        }
        case 60: glBindFramebuffer(u32(p,0),M(u32(p,4))); break;
        case 61: glBindRenderbuffer(u32(p,0),M(u32(p,4))); break;
        case 62: glFramebufferTexture2D(u32(p,0),u32(p,4),u32(p,8),M(u32(p,12)),i32(p,16)); break;
        case 63: glRenderbufferStorage(u32(p,0),u32(p,4),i32(p,8),i32(p,12)); break;
        case 64: glFramebufferRenderbuffer(u32(p,0),u32(p,4),u32(p,8),M(u32(p,12))); break;
        case 65: glScissor(i32(p,0),i32(p,4),i32(p,8),i32(p,12)); break;
        case 66: glColorMask(u32(p,0),u32(p,4),u32(p,8),u32(p,12)); break;
        case 67: glClearStencil(i32(p,0)); break;
        case 68: glStencilMask(u32(p,0)); break;
        case 69: glStencilFunc(u32(p,0),i32(p,4),u32(p,8)); break;
        case 70: glStencilOp(u32(p,0),u32(p,4),u32(p,8)); break;
        case 71: glBlendFuncSeparate(u32(p,0),u32(p,4),u32(p,8),u32(p,12)); break;
        case 72: glBlendEquation(u32(p,0)); break;
        case 73: glBlendEquationSeparate(u32(p,0),u32(p,4)); break;
        case 75: glStencilMaskSeparate(u32(p,0),u32(p,4)); break;
        case 76: glStencilFuncSeparate(u32(p,0),u32(p,4),i32(p,8),u32(p,12)); break;
        case 77: glStencilOpSeparate(u32(p,0),u32(p,4),u32(p,8),u32(p,12)); break;
        case 78: {unsigned count=u32(p,0);for(unsigned i=0;i<count;i++){GLuint o;glGenFramebuffers(1,&o);me_name_set(request_pid,request_tid,u32(p,4+4*i),o,5);}break;}
        case 79: {unsigned count=u32(p,0);for(unsigned i=0;i<count;i++){GLuint o;glGenRenderbuffers(1,&o);me_name_set(request_pid,request_tid,u32(p,4+4*i),o,6);}break;}
        case 109: {uint32_t out=glCheckFramebufferStatus(u32(p,0));if(trace)fprintf(stderr,"GL native framebuffer target=%x status=%x\n",u32(p,0),out);if(write(STDOUT_FILENO,&out,4)!=4)return;break;}
        case 80: glUniform1iv(i32(p,0),i32(p,4),(const GLint *)(p+8));break;
        case 81: glUniform2iv(i32(p,0),i32(p,4),(const GLint *)(p+8));break;
        case 82: glUniform3iv(i32(p,0),i32(p,4),(const GLint *)(p+8));break;
        case 83: glUniform4iv(i32(p,0),i32(p,4),(const GLint *)(p+8));break;
        case 84: {float v[4];memcpy(v,p+4,16);glVertexAttrib4fv(u32(p,0),v);break;}
        case 85: {float v[2];memcpy(v,p,8);glDepthRangef(v[0],v[1]);break;}
        case 86: {float v[4];memcpy(v,p,16);glBlendColor(v[0],v[1],v[2],v[3]);break;}
        case 87: glHint(u32(p,0),u32(p,4));break;
        case 100: case 101: { 
            uint32_t pid = u32(p,0), nl = u32(p,4);
            char nm[256]; uint32_t k = nl < 255 ? nl : 255; memcpy(nm, p+8, k); nm[k]=0;
            GLint loc = (op == 100) ? glGetAttribLocation(M(pid), nm)
                                    : glGetUniformLocation(M(pid), nm);
            int32_t out = loc;
            if (write(STDOUT_FILENO, &out, 4) != 4) { fprintf(stderr, "glhost: getter reply failed\n"); }
            } break;

        default:
            fprintf(stderr,"Unsupported graphics record: op=%u\n",op);
            rejected_record = 1; free(p); return;
        }
    }
    if(capture)fclose(capture);
    free(p);
    fprintf(stderr, "glhost: client disconnected after %lu frames\n", frames);
}

int main(int argc, char **argv)
{
    const char *panel=getenv("MHI2_GL_PANEL");
    if(panel&&*panel){
        if(!strcmp(panel,"1920x1080")){panel_w=1920;panel_h=1080;}
        else if(strcmp(panel,"1560x700")){fprintf(stderr,"Invalid explicit GL panel geometry\n");return 64;}
    }
    meb_qnx_strict=getenv("MEB_QNX_GL_STRICT")&&!strcmp(getenv("MEB_QNX_GL_STRICT"),"original-gtf-v1");
    if(meb_qnx_strict){const char *pid=getenv("MEB_QNX_GL_ORIGINAL_PID");meb_qnx_pid=pid?(uint32_t)strtoul(pid,NULL,10):0;meb_qnx_fixture=getenv("MEB_QNX_GL_FIXTURE")&&!strcmp(getenv("MEB_QNX_GL_FIXTURE"),"controlled-test-v1");if(meb_qnx_pid<2){fprintf(stderr,"QNX strict profile needs an owned producer\n");return 64;}}
    if(meb_qnx_strict&&(getenv("MHI2_PORSCHE_MENU_RGBA")||getenv("MHI2_PORSCHE_MENU_PRESSED_RGBA")||getenv("MHI2_SHADER_CACHE"))){fprintf(stderr,"QNX strict profile rejects foreign resources\n");return 64;}
    if(!meb_qnx_strict)menu_texture_init();
    if (argc == 2 && strcmp(argv[1], "--check-shaders") == 0) {
        ensure_window(DEFAULT_W, DEFAULT_H);
        int failed = 0;
        for (size_t i = 0; i < sizeof(shaders) / sizeof(shaders[0]); i++) {
            GLuint shader = glCreateShader(strstr(shaders[i].source, "gl_Position") ?
                                           GL_VERTEX_SHADER : GL_FRAGMENT_SHADER);
            glShaderSource(shader, 1, &shaders[i].source, NULL);
            glCompileShader(shader);
            GLint compiled = 0;
            glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
            check_shader("self-check", shader, 0);
            failed += !compiled;
            glDeleteShader(shader);
        }
        fprintf(stderr, "glhost: %zu translated shaders checked, %d failed\n",
                sizeof(shaders) / sizeof(shaders[0]), failed);
        return failed != 0;
    }
    serve(STDIN_FILENO);
    return rejected_record ? 2 : 0;
}
