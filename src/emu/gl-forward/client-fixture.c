#include <stdio.h>
#include <string.h>
#include <stdint.h>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif
extern const char *glGetString(unsigned);
extern void glViewport(int,int,int,int);
extern void glClearColor(float,float,float,float);
extern void glClear(unsigned);
extern unsigned glGetError(void);
extern void glReadPixels(int,int,int,int,unsigned,unsigned,void *);
extern unsigned glCreateShader(unsigned);
extern void glShaderSource(unsigned,int,const char **,const int *);
extern void glCompileShader(unsigned);
extern void glGetShaderiv(unsigned,unsigned,int *);
extern unsigned glCreateProgram(void);
extern void glAttachShader(unsigned,unsigned);
extern void glLinkProgram(unsigned);
extern void glGetProgramiv(unsigned,unsigned,int *);
extern void glUseProgram(unsigned);
extern int glGetAttribLocation(unsigned,const char *);
extern void glGenBuffers(int,unsigned *);
extern void glBindBuffer(unsigned,unsigned);
extern void glBufferData(unsigned,unsigned,const void *,unsigned);
extern void glVertexAttribPointer(unsigned,int,unsigned,unsigned,int,const void *);
extern void glEnableVertexAttribArray(unsigned);
extern void glDrawArrays(unsigned,int,int);
extern void glDeleteBuffers(int,const unsigned *);
extern void glDeleteShader(unsigned);
extern void glDeleteProgram(unsigned);
extern void glDetachShader(unsigned,unsigned);
extern unsigned glIsShader(unsigned);
extern unsigned glIsProgram(unsigned);

int main(void)
{
#ifdef _WIN32
    if(_setmode(_fileno(stdin),_O_BINARY)<0||_setmode(_fileno(stdout),_O_BINARY)<0)return 2;
#endif
    const char *version=glGetString(0x1f02);
    const char *renderer=glGetString(0x1f01);
    if(!version||!renderer)return 3;
    fprintf(stderr,"ACTUAL_VERSION=%s\nACTUAL_RENDERER=%s\n",version,renderer);
    const char *vs="attribute vec2 p;void main(){gl_Position=vec4(p,0.0,1.0);}";
    const char *fs="precision mediump float;void main(){gl_FragColor=vec4(0.25,0.5,0.75,1.0);}";
    unsigned v=glCreateShader(0x8b31),f=glCreateShader(0x8b30),program=glCreateProgram();
    glShaderSource(v,1,&vs,0);glCompileShader(v);
    glShaderSource(f,1,&fs,0);glCompileShader(f);int okay=0;
    glGetShaderiv(v,0x8b81,&okay);if(!okay)return 4;
    glGetShaderiv(f,0x8b81,&okay);if(!okay)return 5;
    glAttachShader(program,v);glAttachShader(program,f);glLinkProgram(program);
    glGetProgramiv(program,0x8b82,&okay);if(!okay)return 6;
    glUseProgram(program);int attribute=glGetAttribLocation(program,"p");if(attribute<0)return 7;
    float vertices[]={-1,-1,1,-1,0,1};unsigned buffer;
    glGenBuffers(1,&buffer);glBindBuffer(0x8892,buffer);glBufferData(0x8892,sizeof(vertices),vertices,0x88e4);
    glVertexAttribPointer(attribute,2,0x1406,0,0,0);glEnableVertexAttribArray(attribute);
    glViewport(0,0,1560,700);glClearColor(0,0,0,1);glClear(0x4000);glDrawArrays(4,0,3);
    unsigned char center[4]={0},corner[4]={0};
    glReadPixels(780,350,1,1,0x1908,0x1401,center);
    glReadPixels(0,699,1,1,0x1908,0x1401,corner);
    if(memcmp(center,(unsigned char[]){64,128,191,255},4)||memcmp(corner,(unsigned char[]){0,0,0,255},4))return 8;
    if(!glIsShader(v)||!glIsShader(f)||!glIsProgram(program))return 10;
    glUseProgram(0);glDeleteBuffers(1,&buffer);glDetachShader(program,v);glDetachShader(program,f);
    glDeleteShader(v);glDeleteShader(f);glDeleteProgram(program);
    if(glIsShader(v)||glIsShader(f)||glIsProgram(program)||glGetError())return 9;
    fprintf(stderr,"TRIANGLE_CENTER=4080bfff CORNER=000000ff REAL_DELETE=1 GL_ERROR=0\n");
    return 0;
}
