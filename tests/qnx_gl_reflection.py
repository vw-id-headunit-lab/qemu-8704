from pathlib import Path
import os,sys,json,subprocess,tempfile,struct,select,hashlib
r=Path(__file__).resolve().parents[1];binary=Path(sys.argv[1]).resolve();record={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'input':'Real ANGLE linked program reflection, old individual queries versus op172; relink, errors, array aliases, capacity and context isolation'}
with tempfile.TemporaryDirectory(prefix='qnx-reflection-') as temporary:
 d=Path(temporary);env={k:v for k,v in os.environ.items() if not k.startswith(('MHI2_','MEB_QNX_GL_'))};env.update(DYLD_LIBRARY_PATH=str(r/'.local/angle-sdk/lib'),MEB_QNX_GL_STRICT='original-gtf-v1',MEB_QNX_GL_ORIGINAL_PID='11',MEB_QNX_GL_FIXTURE='controlled-test-v1',MHI2_GL_FRAME=str(d/'frame.ppm'))
 error=(d/'stderr').open('wb');p=subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=error,env=env,bufsize=0)
 def read(n):
  data=bytearray()
  while len(data)<n:
   if not select.select([p.stdout],[],[],15)[0]:raise TimeoutError('host reflection response pending')
   raw=os.read(p.stdout.fileno(),n-len(data))
   if not raw:raise EOFError('host closed protocol')
   data.extend(raw)
  return bytes(data)
 def send(op,data=b''):
  p.stdin.write(struct.pack('<6I',119,8,11,1,op,len(data))+data);p.stdin.flush()
 def words(op,*v):send(op,struct.pack('<'+'I'*len(v),*v))
 def egl(op,args=(),attrs=()):
  send(200,struct.pack('<II8Q',op,len(attrs),*(tuple(args)+(0,)*(8-len(args))))+struct.pack('<'+'q'*len(attrs),*attrs));h=struct.unpack('<IIQII',read(24));extra=read(h[4]);assert h[0],h;return h[2],extra
 def parameter(program,pname):words(103,program,pname);return struct.unpack('<i',read(4))[0]
 def location(program,kind,name):
  raw=name.encode();send(100 if kind==1 else 101,struct.pack('<II',program,len(raw))+raw);return struct.unpack('<i',read(4))[0]
 def active(program,kind,index,capacity):
  words(123 if kind==1 else 124,program,index,capacity);h=struct.unpack('<3I',read(12));return h,read(capacity)
 def reflection(program):
  words(172,program);raw=read(32);h=struct.unpack('<8I',raw);payload=read(h[7]);return h,payload,raw+payload
 def shader(shader_id,kind,source):
  words(7,shader_id,kind);raw=source.encode();send(8,struct.pack('<II',shader_id,len(raw))+raw);words(9,shader_id);words(102,shader_id,0x8b81);assert struct.unpack('<I',read(4))[0]==1
 try:
  display=egl(1)[0];egl(2,[display]);_,extra=egl(5,[display,1,1],[0x3033,1,0x3040,4,0x3024,8,0x3023,8,0x3022,8,0x3021,8,0x3038]);config=struct.unpack('<Q',extra)[0];context=egl(7,[display,config,0],[0x3098,3,0x3038])[0];surface=egl(9,[display,config,0x90123456,80,40,10])[0];egl(12,[display,surface,surface,context])
  vertex='#version 300 es\nin vec4 aPosition;in vec2 aOffset;uniform mat4 uMatrix;uniform vec4 uBias[2];void main(){gl_Position=uMatrix*aPosition+vec4(aOffset,0.,0.)+uBias[0]+uBias[1];}'
  fragment='#version 300 es\nprecision mediump float;uniform vec4 uColor;out vec4 color;void main(){color=uColor;}'
  shader(1,0x8b31,vertex);shader(2,0x8b30,fragment);words(10,3);words(11,3,1);words(11,3,2);words(12,3);assert parameter(3,0x8b82)==1
  h,data,packet=reflection(3);assert h[:3]==(1,3,1)
  expected=[parameter(3,x) for x in [0x8b82,0x8b89,0x8b86,0x8b8a,0x8b87]];assert list(h[2:7])==expected
  offset=0;entries=[]
  while offset<len(data):
   e=struct.unpack_from('<6I',data,offset);name=data[offset+24:offset+24+e[4]-1].decode();offset+=24+e[4];assert location(3,e[0],name)==struct.unpack('<i',struct.pack('<I',e[5]))[0]
   for capacity in [0,1,3,4096]:
    out,namebytes=active(3,e[0],e[1],capacity);n=min(len(name),max(0,capacity-1));assert out==(n,e[2],e[3]);assert namebytes[:n]==name.encode()[:n];assert not capacity or namebytes[n:]==b'\0'*(capacity-n)
   entries.append({'kind':e[0],'index':e[1],'size':e[2],'type':e[3],'name':name,'location':e[5]})
  assert location(3,2,'uBias[1]')>=0;assert location(3,2,'not_present')==-1
  (d/'first.bin').write_bytes(packet)
  words(141,3,3,1);shader(4,0x8b31,'#version 300 es\nin vec4 aNewPosition;void main(){gl_Position=aNewPosition;}');words(11,3,4);words(12,3);assert parameter(3,0x8b82)==1
  h2,data2,packet2=reflection(3);assert h2[3]==1 and h2[4]==1 and data2!=data;(d/'second.bin').write_bytes(packet2)
  words(10,5);invalid,_,_=reflection(5);assert invalid[0]==0 and parameter(5,0x8b82)==0
  invalid,_,_=reflection(9000);assert invalid[0]==0;parameter(9000,0x8b82);words(105);assert struct.unpack('<I',read(4))[0]==0x501;words(105);assert struct.unpack('<I',read(4))[0]==0
  if len(sys.argv)<3 or sys.argv[2]!='host-only':
   client=(r/'src/emu/gl-forward/gles-client.c').read_text();helper=client.split('#ifdef GL_FORWARD_QNX\n',1)[1].split('#define REFLECTION_INVALIDATE()',1)[0]
   transport=(r/'src/emu/gl-forward/qnx-transport.h').read_text();shapes=transport.split('struct ProgramReflection {',1)[1].split('static unsigned record_key;',1)[0];shapes='struct ProgramReflection {'+shapes
   freefn=transport.split('static void release_reflections(',1)[1].split('static void release_record(',1)[0];freefn='static void release_reflections('+freefn
   source='''#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
typedef unsigned u32;typedef int i32;
struct ClientAttrib {u32 size,type,norm,stride,buffer,enabled;const void *pointer;};
'''+shapes+freefn+'''
static struct Record records[2];static unsigned thread_index,requests;static unsigned char wire[1048576];static unsigned wire_size,wire_offset;
static struct Record *current_record(void){return &records[thread_index];}
static void transport_failed(void){abort();}
static void emit_iv(u32 op,const i32 *args,u32 n){assert(op==172&&n==1&&*args==3);wire_offset=0;requests++;}
static void recv_bytes(void *out,unsigned n,int unlock){assert(wire_offset+n<=wire_size);memcpy(out,wire+wire_offset,n);wire_offset+=n;}
static void recv_all(void *out,unsigned n){recv_bytes(out,n,1);}
static void unlock_record(void){}
static void load(const char *path){FILE *f=fopen(path,"rb");assert(f);wire_size=fread(wire,1,sizeof(wire),f);fclose(f);}
'''+helper+'''
int main(int argc,char **argv){
 assert(argc==3);load(argv[1]);records[0].egl_context=(void *)1;records[1].egl_context=(void *)1;
 struct ProgramReflection *p=program_reflection(3);assert(p&&requests==1);i32 value=-9;
 const u32 names[5]={0x8b82,0x8b89,0x8b86,0x8b8a,0x8b87};unsigned queries=0;
 for(unsigned repeat=0;repeat<50;repeat++){
  for(unsigned i=0;i<5;i++){assert(reflection_parameter(3,names[i],&value)&&value==(i32)p->header[i+2]);queries++;}
  for(unsigned offset=0;offset<p->header[7];){unsigned char *e=p->data+offset;u32 kind=reflection_word(e,0),index=reflection_word(e,4),n=reflection_word(e,16);char *name=(char *)e+24;
   assert(reflection_location(3,kind,name,&value)&&value==(i32)reflection_word(e,20));queries++;
   const int capacities[4]={0,1,3,4096};for(unsigned c=0;c<4;c++){char result[4096];memset(result,0x5a,sizeof(result));i32 length=-1,size=-1;u32 type=0;
    assert(reflection_active(3,kind,index,capacities[c],&length,&size,&type,result));u32 length_expected=capacities[c]?((n-1<(u32)capacities[c]-1)?n-1:(u32)capacities[c]-1):0;
    assert(length==length_expected&&size==(i32)reflection_word(e,8)&&type==reflection_word(e,12));assert(!memcmp(name,result,length));
    for(unsigned j=length;j<(unsigned)capacities[c];j++)assert(result[j]==0);queries++;
   }offset+=24+n;
  }
 }
 assert(requests==1);assert(!reflection_location(3,2,"uBias[1]",&value));assert(!reflection_location(3,2,"missing",&value));assert(!reflection_parameter(3,0x8b84,&value));assert(!reflection_active(3,1,100000,10,0,0,0,0));
 thread_index=1;assert(program_reflection(3)&&requests==2);thread_index=0;records[0].egl_context=(void *)2;assert(program_reflection(3)&&requests==3);
 load(argv[2]);__sync_add_and_fetch(&reflection_epoch,1);assert(reflection_parameter(3,0x8b89,&value)&&value==1&&requests==4);assert(reflection_location(3,1,"aNewPosition",&value));assert(!reflection_location(3,1,"aPosition",&value));
 __sync_add_and_fetch(&reflection_epoch,1);assert(program_reflection(3)&&requests==5);
 release_reflections(&records[0]);release_reflections(&records[1]);printf("real_metadata_queries=%u initial_network_requests=1 total_requests_with_thread_context_relink_delete=5\\n",queries);return 0;
}
'''
   (d/'client.c').write_text(source);c=subprocess.run(['clang','-O1','-fsanitize=address,undefined',str(d/'client.c'),'-o',str(d/'client')],capture_output=True,text=True);assert c.returncode==0,c.stderr
   q=subprocess.run([str(d/'client'),str(d/'first.bin'),str(d/'second.bin')],capture_output=True,text=True);record['native_client_helper']={'exit':q.returncode,'stdout':q.stdout,'stderr':q.stderr};assert q.returncode==0,q.stderr
  record.update(exit=0,entries=entries,header=h,relinked_header=h2)
 except EOFError as e:record.update(exit=2,error=str(e))
 except Exception as e:record.update(exit=1,error=repr(e))
 finally:
  p.stdin.close()
  try:p.wait(timeout=3)
  except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=3)
  error.close();record['host_exit']=p.returncode;record['stderr']=(d/'stderr').read_text(errors='replace')
print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
