#define _GNU_SOURCE
#include "fd-transport.h"
void glViewport(i32 x, i32 y, i32 w, i32 h) { i32 p[4] = { x, y, w, h }; emit_iv(2, p, 4); }
void glClearColor(float r, float g, float b, float a) { float p[4] = { r, g, b, a }; emit_iv(3, (i32 *)p, 4); }
void glClear(u32 mask) { emit_iv(4, (i32 *)&mask, 1); }
void glFinish(void) { rec(6, 0); }
void glFlush(void) { rec(6, 0); }

u32 glCreateShader(u32 type) { u32 id = __sync_add_and_fetch(&g_id,1); u32 p[2] = { id, type }; emit_iv(7, (i32 *)p, 2); return id; }
void glShaderSource(u32 sh, i32 count, const char *const *str, const i32 *len)
{
    u32 total = 0; i32 i;
    for (i = 0; i < count; i++)
        total += (len && len[i] >= 0) ? (u32)len[i] : (u32)strlen(str[i]);
    u32 h[2] = { sh, total };
    rec(8, 8 + total);
    PART(h, 8);
    for (i = 0; i < count; i++) {
        u32 l = (len && len[i] >= 0) ? (u32)len[i] : (u32)strlen(str[i]);
        PART(str[i], l);
    }
}
void glCompileShader(u32 sh) { emit_iv(9, (i32 *)&sh, 1); }
u32 glCreateProgram(void) { u32 id = __sync_add_and_fetch(&g_id,1); emit_iv(10, (i32 *)&id, 1); return id; }
void glProgramParameteri(u32 program,u32 pname,i32 value) { i32 p[3]={(i32)program,(i32)pname,value};emit_iv(143,p,3); }
void glGetProgramBinary(u32 program,i32 capacity,i32 *length,u32 *format,void *binary)
{
    i32 args[2]={(i32)program,capacity};u32 result[2]={0};
    emit_iv(145,args,2);recv_bytes(result,8,0);
    if(result[0]>(capacity>0?(u32)capacity:0))transport_failed();
    if(result[0]){
        if(binary)recv_all(binary,result[0]);
        else{
            unsigned char scratch[4096];u32 left=result[0];
            while(left){u32 n=left>sizeof(scratch)?sizeof(scratch):left;recv_bytes(scratch,n,0);left-=n;}
            unlock_record();
        }
    }else unlock_record();
    if(length)*length=(i32)result[0];if(format)*format=result[1];
}
void glAttachShader(u32 pid, u32 sid) { u32 p[2] = { pid, sid }; emit_iv(11, (i32 *)p, 2); }
void glLinkProgram(u32 pid) { emit_iv(12, (i32 *)&pid, 1); }
void glUseProgram(u32 pid) { emit_iv(13, (i32 *)&pid, 1); }
void glBindAttribLocation(u32 pid, u32 index, const char *name)
{
    u32 l = (u32)strlen(name); u32 h[3] = { pid, index, l };
    rec(14, 12 + l); PART(h, 12); PART(name, l);
}
void glDeleteShader(u32 s) { u32 p[3]={1,s,0};emit_iv(141,(i32 *)p,3); }
void glDeleteProgram(u32 p) { u32 v[3]={2,p,0};emit_iv(141,(i32 *)v,3); }
void glDetachShader(u32 p, u32 s) { u32 v[3]={3,p,s};emit_iv(141,(i32 *)v,3); }


void glGetShaderiv(u32 s, u32 pname, i32 *p) { u32 v[2]={s,pname}; emit_iv(102,(i32 *)v,2); i32 out=0; recv_all(&out,4); if(p)*p=out; }
void glGetProgramiv(u32 pr, u32 pname, i32 *p) { u32 v[2]={pr,pname}; emit_iv(103,(i32 *)v,2); i32 out=0; recv_all(&out,4); if(p)*p=out; }
void glGetShaderInfoLog(u32 s, i32 buf, i32 *len, char *log) { (void)s;(void)buf; if (len) *len = 0; if (log && buf > 0) log[0] = 0; }
void glGetProgramInfoLog(u32 p, i32 buf, i32 *len, char *log) { (void)p;(void)buf; if (len) *len = 0; if (log && buf > 0) log[0] = 0; }

/* ---------------- buffers ---------------- */
void glGenBuffers(i32 n, u32 *b)
{
    if (!b) return;
    rec(15, 4 + (u32)n * 4);
    u32 cnt = (u32)n; PART(&cnt, 4);
    for (i32 i = 0; i < n; i++) { b[i] = __sync_add_and_fetch(&g_id,1); PART(&b[i], 4); }
}
void glBindBuffer(u32 target, u32 id) { struct Record *r=current_record();if(target==0x8892)r->array_buffer=id;if(target==0x8893)r->element_buffer=id; u32 p[2] = { target, id }; emit_iv(16, (i32 *)p, 2); }
void glBufferData(u32 target, i32 size, const void *data, u32 usage)
{
    u32 sz = size < 0 ? 0 : (u32)size;
    u32 dn = data ? sz : 0;
    u32 h[3] = { target, sz, usage };
    rec(17, 12 + dn); PART(h, 12); if (dn) PART(data, dn);
}
void glBufferSubData(u32 target, i32 offset, i32 size, const void *data)
{
    u32 sz = size < 0 ? 0 : (u32)size;
    u32 dn = data ? sz : 0;
    u32 h[3] = { target, (u32)offset, sz };
    rec(18, 12 + dn); PART(h, 12); if (dn) PART(data, dn);
}
static void delete_objects(u32 kind, i32 count, const u32 *ids)
{
    if (count <= 0 || !ids) return;
    u32 header[2] = {kind, (u32)count};
    rec(130, 8 + (u32)count * 4); PART(header, 8); PART(ids, (u32)count * 4);
}
void glDeleteBuffers(i32 n, const u32 *ids)
{
    struct Record *r = current_record();
    for (i32 i = 0; ids && i < n; i++) {
        if (r->array_buffer == ids[i]) r->array_buffer = 0;
        if (r->element_buffer == ids[i]) r->element_buffer = 0;
        for (unsigned a = 0; a < 16; a++)
            if (r->attribs[a].buffer == ids[i]) {r->attribs[a].buffer = 0; r->attribs[a].pointer = 0;}
    }
    delete_objects(1, n, ids);
}

/* ---------------- vertex attribs ---------------- */
void glVertexAttribPointer(u32 index, i32 size, u32 type, u32 norm, i32 stride, const void *ptr)
{
    if(index>=16 || size<1 || size>4 || stride<0)abort();
    struct Record *r=current_record();struct ClientAttrib *a=&r->attribs[index];
    a->size=size;a->type=type;a->norm=norm;a->stride=stride;a->pointer=ptr;a->buffer=r->array_buffer;
    if(!a->buffer)return; /* Guest pointers are copied at draw time. */
    if((uintptr_t)ptr>UINT32_MAX)abort();
    u32 p[6] = { index, (u32)size, type, norm ? 1u : 0u, (u32)stride, (u32)(uintptr_t)ptr };
    emit_iv(19, (i32 *)p, 6);
}
void glEnableVertexAttribArray(u32 index) { if(index<16)current_record()->attribs[index].enabled=1; emit_iv(20, (i32 *)&index, 1); }
void glDisableVertexAttribArray(u32 index) { if(index<16)current_record()->attribs[index].enabled=0; emit_iv(21, (i32 *)&index, 1); }

/* ---------------- uniforms ---------------- */
static void uni_vec(u32 op, i32 loc, i32 count, u32 per, const float *v)
{
    u32 h[2] = { (u32)loc, (u32)count };
    u32 fn = (u32)count * per * 4;
    rec(op, 8 + fn); PART(h, 8); if (fn) PART(v, fn);
}
static void uni_mat(u32 op, i32 loc, i32 count, u32 transpose, u32 elems, const float *v)
{
    u32 h[3] = { (u32)loc, (u32)count, transpose };
    u32 fn = (u32)count * elems * 4;
    rec(op, 12 + fn); PART(h, 12); if (fn) PART(v, fn);
}
void glUniform1fv(i32 loc, i32 count, const float *v) { uni_vec(22, loc, count, 1, v); }
void glUniform2fv(i32 loc, i32 count, const float *v) { uni_vec(23, loc, count, 2, v); }
void glUniform3fv(i32 loc, i32 count, const float *v) { uni_vec(24, loc, count, 3, v); }
void glUniform4fv(i32 loc, i32 count, const float *v) { uni_vec(25, loc, count, 4, v); }
void glUniformMatrix2fv(i32 loc, i32 count, u32 t, const float *v) { uni_mat(26, loc, count, t, 4, v); }
void glUniformMatrix3fv(i32 loc, i32 count, u32 t, const float *v) { uni_mat(27, loc, count, t, 9, v); }
void glUniformMatrix4fv(i32 loc, i32 count, u32 t, const float *v) { uni_mat(28, loc, count, t, 16, v); }
void glUniform1f(i32 loc, float v) { float p[2]; ((i32 *)p)[0] = loc; p[1] = v; rec(29, 8); PART(p, 8); }
void glUniform1i(i32 loc, i32 v) { i32 p[2] = { loc, v }; emit_iv(30, p, 2); }

/* ---------------- draws ---------------- */
static unsigned scalar_bytes(u32 type)
{
    switch(type){case 0x1400:case 0x1401:return 1;
    case 0x1402:case 0x1403:return 2;
    case 0x1404:case 0x1405:case 0x1406:case 0x140c:return 4;
    default:abort();return 0;}
}
static int has_client_arrays(void)
{
    struct Record *r=current_record();
    for(unsigned i=0;i<16;i++)
        if(r->attribs[i].enabled && !r->attribs[i].buffer)return 1;
    return 0;
}
static void upload_client_arrays(unsigned vertices)
{
    struct Record *r=current_record();
    for(unsigned i=0;i<16;i++){
        struct ClientAttrib *a=&r->attribs[i];
        if(!a->enabled||a->buffer||!vertices)continue;
        unsigned element=a->size*scalar_bytes(a->type),stride=a->stride?a->stride:element;
        unsigned long long bytes=(unsigned long long)(vertices-1)*stride+element;
        if(!a->pointer || bytes>16*1024*1024-24)abort();
        u32 h[6]={i,a->size,a->type,a->norm,a->stride,(u32)bytes};
        rec(128,24+bytes);PART(h,24);PART(a->pointer,bytes);
    }
}
void glDrawArrays(u32 mode, i32 first, i32 count)
{
    if(first<0 || count<0 || (u32)count>0x7fffffffU-(u32)first)abort();
    if(count)upload_client_arrays((u32)first+count);
    i32 p[3]={(i32)mode,first,count};emit_iv(31,p,3);
}
void glDrawElements(u32 mode, i32 count, u32 type, const void *indices)
{
    if(count<0 || (type!=0x1401 && type!=0x1403 && type!=0x1405))abort();
    if(!count)return;
    struct Record *r=current_record();
    unsigned n=scalar_bytes(type),maximum=0;
    if(!r->element_buffer){
        if(!indices || (unsigned long long)count*n>16*1024*1024-16)abort();
        if(has_client_arrays()){
            const unsigned char *b=indices;
            for(i32 i=0;i<count;i++){u32 v=0;memcpy(&v,b+i*n,n);if(v>maximum)maximum=v;}
            if(maximum==0xffffffffU)abort();
            upload_client_arrays(maximum+1);
        }
        u32 h[4]={mode,(u32)count,type,(u32)count*n};rec(129,16+h[3]);PART(h,16);PART(indices,h[3]);
    }else{
        if((uintptr_t)indices>UINT32_MAX)abort();
        if(has_client_arrays()){
            /* Audi EAL combines guest client vertices with a host EBO. */
            u32 query[3]={(u32)count,type,(u32)(uintptr_t)indices};
            maximum=0xffffffffU;
            emit_iv(138,(i32 *)query,3);
            if(recv_all(&maximum,4)<0 || maximum==0xffffffffU)abort();
            upload_client_arrays(maximum+1);
        }
        u32 p[4]={mode,(u32)count,type,(u32)(uintptr_t)indices};emit_iv(32,(i32 *)p,4);
    }
}

/* ---------------- pipeline state ---------------- */
void glEnable(u32 cap) { emit_iv(33, (i32 *)&cap, 1); }
void glDisable(u32 cap) { emit_iv(34, (i32 *)&cap, 1); }
void glBlendFunc(u32 s, u32 d) { u32 p[2] = { s, d }; emit_iv(35, (i32 *)p, 2); }
void glDepthFunc(u32 f) { emit_iv(36, (i32 *)&f, 1); }
void glCullFace(u32 m) { emit_iv(37, (i32 *)&m, 1); }
void glFrontFace(u32 m) { emit_iv(38, (i32 *)&m, 1); }
void glDepthMask(u32 flag) { emit_iv(39, (i32 *)&flag, 1); }
void glClearDepthf(float d) { rec(40, 4); PART(&d, 4); }
void glClearDepth(double d) { glClearDepthf((float)d); }   
void glLineWidth(float w) { rec(42, 4); PART(&w, 4); }
void glPolygonOffset(float factor, float units) { float p[2] = { factor, units }; rec(43, 8); PART(p, 8); }

/* ---------------- textures ---------------- */
void glGenTextures(i32 n, u32 *t)
{
    if (!t) return;
    rec(50, 4 + (u32)n * 4);
    u32 cnt = (u32)n; PART(&cnt, 4);
    for (i32 i = 0; i < n; i++) { t[i] = __sync_add_and_fetch(&g_id,1); PART(&t[i], 4); }
}
void glBindTexture(u32 target, u32 id) { u32 p[2] = { target, id }; emit_iv(51, (i32 *)p, 2); }
static u32 tex_bytes(u32 fmt, u32 type, i32 w, i32 h)
{
    u32 ch = 4;
    if (fmt == 0x1907) ch = 3;        /* GL_RGB */
    else if (fmt == 0x1909) ch = 1;   /* GL_LUMINANCE / DEPTH */
    else if (fmt == 0x190A) ch = 2;   /* GL_LUMINANCE_ALPHA */
    else if (fmt == 0x1906) ch = 1;   /* GL_ALPHA */
    u32 bp = ch;
    if (type == 0x8363 || type == 0x8033 || type == 0x8034) bp = 2; /* 565/4444/5551 packed */
    if (w < 0) w = 0; if (h < 0) h = 0;
    return (u32)w * (u32)h * bp;
}
void glTexImage2D(u32 target, i32 level, i32 ifmt, i32 w, i32 h, i32 border, u32 fmt, u32 type, const void *data)
{
    u32 sz = data ? tex_bytes(fmt, type, w, h) : 0;
    u32 hd[9] = { target, (u32)level, (u32)ifmt, (u32)w, (u32)h, (u32)border, fmt, type, sz };
    rec(52, 36 + sz); PART(hd, 36); if (sz) PART(data, sz);
}
void glTexParameteri(u32 target, u32 pname, i32 param) { i32 p[3] = { (i32)target, (i32)pname, param }; emit_iv(53, p, 3); }
void glTexParameterf(u32 target, u32 pname, float param) { u32 h[2] = { target, pname }; rec(58, 12); PART(h, 8); PART(&param, 4); }
void glActiveTexture(u32 texture) { emit_iv(54, (i32 *)&texture, 1); }
void glPixelStorei(u32 pname, i32 param) { i32 p[2] = { (i32)pname, param }; emit_iv(55, p, 2); }
void glTexSubImage2D(u32 target, i32 level, i32 x, i32 y, i32 w, i32 h, u32 fmt, u32 type, const void *data)
{
    u32 sz = data ? tex_bytes(fmt, type, w, h) : 0;
    u32 hd[8] = { target, (u32)level, (u32)x, (u32)y, (u32)w, (u32)h, fmt, type };
    /* pack sz as a 9th word for symmetry with glhost */
    u32 hd2[9]; for (int i=0;i<8;i++) hd2[i]=hd[i]; hd2[8]=sz;
    rec(56, 36 + sz); PART(hd2, 36); if (sz) PART(data, sz);
}
void glGenerateMipmap(u32 target) { emit_iv(57, (i32 *)&target, 1); }

/* ---------------- synchronous getters ---------------- */
i32 glGetAttribLocation(u32 pid, const char *name)
{
    u32 l = (u32)strlen(name); u32 h[2] = { pid, l };
    rec(100, 8 + l); PART(h, 8); PART(name, l);
    i32 loc = -1; recv_all(&loc, 4); return loc;
}
i32 glGetUniformLocation(u32 pid, const char *name)
{
    u32 l = (u32)strlen(name); u32 h[2] = { pid, l };
    rec(101, 8 + l); PART(h, 8); PART(name, l);
    i32 loc = -1; recv_all(&loc, 4); return loc;
}

/* misc still faked locally */
i32 glGetError(void) {rec(105,0);i32 e=0;recv_all(&e,4);return e;}
const char *glGetString(u32 name)
{
    static __thread char info[5][16385];
    unsigned slot;
    switch(name){case 0x1f00:slot=0;break;case 0x1f01:slot=1;break;
        case 0x1f02:slot=2;break;case 0x1f03:slot=3;break;case 0x8b8c:slot=4;break;
        default:return 0;}
    emit_iv(140,(const i32 *)&name,1);u32 length=0;
    recv_bytes(&length,4,0);if(length>16384)transport_failed();
    recv_bytes(info[slot],length,1);info[slot][length]=0;
    return length?info[slot]:0;
}

void glGenFramebuffers(i32 n,u32 *objects){if(n<0||!objects)return;rec(78,4+4*n);PART(&n,4);for(i32 i=0;i<n;i++){objects[i]=__sync_add_and_fetch(&g_id,1);PART(&objects[i],4);}}
void glGenRenderbuffers(i32 n,u32 *objects){if(n<0||!objects)return;rec(79,4+4*n);PART(&n,4);for(i32 i=0;i<n;i++){objects[i]=__sync_add_and_fetch(&g_id,1);PART(&objects[i],4);}}
void glGetIntegerv(u32 pname, i32 *p) {
    emit_iv(104,(i32 *)&pname,1);i32 v[16];if(recv_all(v,64)<0)return;
    unsigned n=(pname==0xba2 || pname==0xc10 || pname==0xc23)?4:(pname==0xd3a?2:1);
    if(p)for(unsigned i=0;i<n;i++)p[i]=v[i];
}
void glGetFloatv(u32 pname,float *p) {
 emit_iv(107,(i32 *)&pname,1);float v[16];if(recv_all(v,64)<0)return;
 unsigned n=(pname==0xc22 || pname==0x8005)?4:(pname==0xb70 || pname==0x846d || pname==0x846e?2:1);
 if(p)for(unsigned i=0;i<n;i++)p[i]=v[i];
}


int slog2f(void) { return 0; }
int slog2c(void) { return 0; }
int slog2_register(void) { return 0; }


/* ---- noop stubs for the rest of the exported ABI ---- */
void glBindFramebuffer(u32 target,u32 object){u32 p[]={(u32)target,(u32)object};emit_iv(60,(i32 *)p,2);}
void glBindRenderbuffer(u32 target,u32 object){u32 p[]={(u32)target,(u32)object};emit_iv(61,(i32 *)p,2);}
void glBlendColor(float r,float g,float b,float a){float v[4]={r,g,b,a};rec(86,16);PART(v,16);}
void glBlendEquation(u32 mode){u32 p[]={(u32)mode};emit_iv(72,(i32 *)p,1);}
void glBlendEquationSeparate(u32 rgb,u32 alpha){u32 p[]={(u32)rgb,(u32)alpha};emit_iv(73,(i32 *)p,2);}
void glBlendFuncSeparate(u32 sr,u32 dr,u32 sa,u32 da){u32 p[]={(u32)sr,(u32)dr,(u32)sa,(u32)da};emit_iv(71,(i32 *)p,4);}
u32 glCheckFramebufferStatus(u32 target){emit_iv(109,(i32 *)&target,1);u32 out=0;recv_all(&out,4);return out;}
void glClearStencil(i32 value){u32 p[]={(u32)value};emit_iv(67,(i32 *)p,1);}
void glColorMask(u32 r,u32 g,u32 b,u32 a){u32 p[]={(u32)r,(u32)g,(u32)b,(u32)a};emit_iv(66,(i32 *)p,4);}
void glDeleteFramebuffers(i32 n, const u32 *ids) { delete_objects(2, n, ids); }
void glDeleteRenderbuffers(i32 n, const u32 *ids) { delete_objects(3, n, ids); }
void glDeleteTextures(i32 n, const u32 *ids) { delete_objects(0, n, ids); }
void glDepthRangef(float near,float far){float v[2]={near,far};rec(85,8);PART(v,8);}

void glFramebufferRenderbuffer(u32 target,u32 attachment,u32 rbTarget,u32 object){u32 p[]={(u32)target,(u32)attachment,(u32)rbTarget,(u32)object};emit_iv(64,(i32 *)p,4);}
void glFramebufferTexture2D(u32 target,u32 attachment,u32 textureTarget,u32 object,i32 level){u32 p[]={(u32)target,(u32)attachment,(u32)textureTarget,(u32)object,(u32)level};emit_iv(62,(i32 *)p,5);}
static void get_active(u32 op,u32 program,u32 index,i32 capacity,i32 *length,i32 *size,u32 *type,char *name)
{
    if(capacity<0)return;
    u32 count=capacity>4096?4096:(u32)capacity;
    u32 args[3]={program,index,count},result[3]={0};
    emit_iv(op,(i32 *)args,3);
    if(recv_bytes(result,12,0))return;
    char scratch[4096];
    if(count){if(recv_all(scratch,count))return;if(name)memcpy(name,scratch,count);}
    else unlock_record();
    if(length)*length=result[0];if(size)*size=result[1];if(type)*type=result[2];
}
void glGetActiveAttrib(u32 program,u32 index,i32 capacity,i32 *length,i32 *size,u32 *type,char *name)
{get_active(123,program,index,capacity,length,size,type,name);}
void glGetActiveUniform(u32 program,u32 index,i32 capacity,i32 *length,i32 *size,u32 *type,char *name)
{get_active(124,program,index,capacity,length,size,type,name);}
void glGetBooleanv(u32 pname,unsigned char *p) {
 emit_iv(106,(i32 *)&pname,1);unsigned char v[16];if(recv_all(v,16)<0)return;
 unsigned n=pname==0xc23?4:1;if(p)for(unsigned i=0;i<n;i++)p[i]=v[i];
}
void glGetShaderPrecisionFormat(u32 shader,u32 precision,i32 *range,i32 *bits) {
 u32 p[2]={shader,precision};emit_iv(108,(i32 *)p,2);i32 v[3]={0,0,0};recv_all(v,12);
 if(range){range[0]=v[0];range[1]=v[1];}if(bits)*bits=v[2];
}
void glHint(u32 target,u32 mode){u32 v[2]={target,mode};emit_iv(87,(i32 *)v,2);}
void glReadPixels(i32 x,i32 y,i32 w,i32 h,u32 format,u32 type,void *pixels)
{
    /* RGBA8 is the native CWM handoff format used by this bridge. */
    if(w<0||h<0||w>2048||h>2048||format!=0x1908||type!=0x1401||!pixels)return;
    if(!w||!h)return;
    i32 args[6]={x,y,w,h,(i32)format,(i32)type};
    emit_iv(120,args,6);recv_all(pixels,(unsigned)w*h*4);
}
void glRenderbufferStorage(u32 target,u32 format,i32 width,i32 height){u32 p[]={(u32)target,(u32)format,(u32)width,(u32)height};emit_iv(63,(i32 *)p,4);}
void glScissor(i32 x,i32 y,i32 width,i32 height){u32 p[]={(u32)x,(u32)y,(u32)width,(u32)height};emit_iv(65,(i32 *)p,4);}
void glShaderBinary(i32 count,const u32 *shaders,u32 format,const void *binary,i32 length)
{
    if(count < 0 || length < 0) return;
    u32 h[3]={(u32)count,format,(u32)length};
    rec(110,12+4*count+length);PART(h,12);PART(shaders,4*count);PART(binary,length);
}
void glStencilFunc(u32 func,i32 ref,u32 mask){u32 p[]={(u32)func,(u32)ref,(u32)mask};emit_iv(69,(i32 *)p,3);}
void glStencilFuncSeparate(u32 face,u32 func,i32 ref,u32 mask){u32 p[]={(u32)face,(u32)func,(u32)ref,(u32)mask};emit_iv(76,(i32 *)p,4);}
void glStencilMask(u32 mask){u32 p[]={(u32)mask};emit_iv(68,(i32 *)p,1);}
void glStencilMaskSeparate(u32 face,u32 mask){u32 p[]={(u32)face,(u32)mask};emit_iv(75,(i32 *)p,2);}
void glStencilOp(u32 fail,u32 zfail,u32 pass){u32 p[]={(u32)fail,(u32)zfail,(u32)pass};emit_iv(70,(i32 *)p,3);}
void glStencilOpSeparate(u32 face,u32 fail,u32 zfail,u32 pass){u32 p[]={(u32)face,(u32)fail,(u32)zfail,(u32)pass};emit_iv(77,(i32 *)p,4);}
void glUniform1iv(i32 loc,i32 count,const i32 *v){if(count<0)return;u32 h[2]={(u32)loc,(u32)count};rec(80,8+count*4);PART(h,8);PART(v,count*4);}
void glUniform2f(i32 loc,float x,float y){float v[]={x,y};uni_vec(23,loc,1,2,v);}
void glUniform2i(i32 loc,i32 x,i32 y){i32 v[]={x,y};glUniform2iv(loc,1,v);}
void glUniform2iv(i32 loc,i32 count,const i32 *v){if(count<0)return;u32 h[2]={(u32)loc,(u32)count};rec(81,8+count*8);PART(h,8);PART(v,count*8);}
void glUniform3f(i32 loc,float x,float y,float z){float v[]={x,y,z};uni_vec(24,loc,1,3,v);}
void glUniform3i(i32 loc,i32 x,i32 y,i32 z){i32 v[]={x,y,z};glUniform3iv(loc,1,v);}
void glUniform3iv(i32 loc,i32 count,const i32 *v){if(count<0)return;u32 h[2]={(u32)loc,(u32)count};rec(82,8+count*12);PART(h,8);PART(v,count*12);}
void glUniform4f(i32 loc,float x,float y,float z,float w){float v[]={x,y,z,w};uni_vec(25,loc,1,4,v);}
void glUniform4i(i32 loc,i32 x,i32 y,i32 z,i32 w){i32 v[]={x,y,z,w};glUniform4iv(loc,1,v);}
void glUniform4iv(i32 loc,i32 count,const i32 *v){if(count<0)return;u32 h[2]={(u32)loc,(u32)count};rec(83,8+count*16);PART(h,8);PART(v,count*16);}
void glVertexAttrib1f(u32 index,float x){float v[]={x,0.0,0.0,1.0};rec(84,20);PART(&index,4);PART(v,16);}
void glVertexAttrib1fv(u32 index,const float *v){glVertexAttrib1f(index,v[0]);}
void glVertexAttrib2f(u32 index,float x,float y){float v[]={x,y,0.0,1.0};rec(84,20);PART(&index,4);PART(v,16);}
void glVertexAttrib2fv(u32 index,const float *v){glVertexAttrib2f(index,v[0],v[1]);}
void glVertexAttrib3f(u32 index,float x,float y,float z){float v[]={x,y,z,1.0};rec(84,20);PART(&index,4);PART(v,16);}
void glVertexAttrib3fv(u32 index,const float *v){glVertexAttrib3f(index,v[0],v[1],v[2]);}
void glVertexAttrib4f(u32 index,float x,float y,float z,float w){float v[]={x,y,z,w};rec(84,20);PART(&index,4);PART(v,16);}
void glVertexAttrib4fv(u32 index,const float *v){glVertexAttrib4f(index,v[0],v[1],v[2],v[3]);}


static u32 object_query(u32 kind,u32 object)
{ u32 p[2]={kind,object},value=0;emit_iv(142,(i32 *)p,2);recv_all(&value,4);return value; }
u32 glIsShader(u32 shader) { return object_query(1,shader); }
u32 glIsProgram(u32 program) { return object_query(2,program); }

#include "egl-client.h"
#ifdef OEM_GL_NATIVE_BINDINGS
#include "native-aliases.inc"
#endif
