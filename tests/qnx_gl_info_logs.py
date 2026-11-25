from pathlib import Path
import sys,os,json,subprocess,tempfile,struct,select,hashlib,time
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];record={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'label':label,'input':'Native original GLSL compile/link diagnostic logs including truncated/zero/negative capacities; not original GUI proof','calls':[]};p=None
with tempfile.TemporaryDirectory(prefix='qnx-gl-host-')as d:
 d=Path(d);env={k:v for k,v in os.environ.items()if not k.startswith(('MHI2_','MEB_QNX_GL_'))};env.update(DYLD_LIBRARY_PATH=str(r/'.local/angle-sdk/lib'),MEB_QNX_GL_STRICT='original-gtf-v1',MEB_QNX_GL_ORIGINAL_PID='11',MEB_QNX_GL_FIXTURE='controlled-test-v1',MHI2_GL_FRAME=str(d/'frame.ppm'))
 try:
  p=subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(d/'stderr').open('wb'),env=env,bufsize=0)
  def read(n):
   data=bytearray()
   while len(data)<n:
    if not select.select([p.stdout],[],[],15)[0]:raise TimeoutError('same host call response pending')
    b=os.read(p.stdout.fileno(),n-len(data))
    if not b:raise ConnectionError('host exited')
    data.extend(b)
   return bytes(data)
  def send(op,data=b''):
   p.stdin.write(struct.pack('<6I',119,8,11,1,op,len(data))+data);p.stdin.flush()
  def egl(op,args=(),attrs=()):
   a=tuple(args)+(0,)*(8-len(args));data=struct.pack('<II8Q',op,len(attrs),*a)+struct.pack('<'+'q'*len(attrs),*attrs);send(200,data);reply=struct.unpack('<IIQII',read(24));extra=read(reply[4]);record['calls'].append({'operation':op,'args':list(args),'reply':reply,'extra_hex':extra.hex()});assert reply[0],reply;return reply,extra
  display=egl(1)[0][2];egl(2,[display]);rep,extra=egl(5,[display,1,1],[0x3033,1,0x3040,4,0x3024,8,0x3023,8,0x3022,8,0x3021,8,0x3038]);config=struct.unpack('<Q',extra)[0];context=egl(7,[display,config,0],[0x3098,3,0x3038])[0][2];surface=egl(9,[display,config,0x90123456,80,40,10])[0][2];egl(12,[display,surface,surface,context]);width=egl(15,[display,surface,0x3057])[0][2];height=egl(15,[display,surface,0x3056])[0][2];record['actual_dimensions']=[width,height];assert(width,height)==(80,40),'native window size not preserved'
  send(160,struct.pack('<II',0,7));send(105);assert struct.unpack('<I',read(4))[0]==0
  send(160,struct.pack('<II',0xffffffff,1));send(105);assert struct.unpack('<I',read(4))[0]==0x501
  send(160,struct.pack('<II',0,0));record['native_vertex_divisor']={'valid':7,'invalid_index_native_error':0x501}
  def scalar(op,obj,pname):
   send(op,struct.pack('<II',obj,pname));return struct.unpack('<i',read(4))[0]
  def log(kind,obj,capacity):
   send(148,struct.pack('<IIi',kind,obj,capacity));n=struct.unpack('<I',read(4))[0];assert n<=max(0,capacity-1);return read(n)
  send(7,struct.pack('<II',101,0x8b31));source=b'#version 300 es\nthis is invalid GLSL;\n';send(8,struct.pack('<II',101,len(source))+source);send(9,struct.pack('<I',101));assert scalar(102,101,0x8b81)==0
  full=log(1,101,65536);assert full and len(full)+1==scalar(102,101,0x8b84)
  truncated=log(1,101,8);assert truncated==full[:7];assert log(1,101,0)==b''
  send(10,struct.pack('<I',102));send(12,struct.pack('<I',102));assert scalar(103,102,0x8b82)==0
  program=log(2,102,65536);assert program and len(program)+1==scalar(103,102,0x8b84)
  assert log(2,102,8)==program[:7];assert log(2,102,0)==b''
  send(105);assert struct.unpack('<I',read(4))[0]==0
  assert log(1,101,-1)==b'';send(105);assert struct.unpack('<I',read(4))[0]==0x501
  record['native_info_logs']={'shader':full.decode(),'program':program.decode(),'truncated_shader':truncated.decode(),'zero_capacity_empty':True,'negative_capacity_native_error':0x501}
  send(2,struct.pack('<4i',0,0,80,40))
  send(3,struct.pack('<4f',1,0,0,1));send(4,struct.pack('<I',0x4000));egl(13,[display,surface]);frames=list(d.glob('*.ppm.json'));assert len(frames)==1;meta=json.loads(frames[0].read_text());assert meta['source']=='qnx_gl_fixture_test'and meta['width']==80 and meta['height']==40 and meta['swap_count']==1;raw=frames[0].with_suffix('').read_bytes();header,bpp=raw.split(b'\n255\n',1);assert header==b'P6\n80 40'and bpp==b'\xff\0\0'*(80*40);record.update(metadata=meta,pixels_sha256=hashlib.sha256(bpp).hexdigest(),exit=0)
 except Exception as e:record.update(exit=1,error=repr(e))
 finally:
  if p:
   p.stdin.close()
   try:p.wait(timeout=3)
   except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=3)
   record['host_exit']=p.returncode
  record['stderr']=(d/'stderr').read_text(errors='replace')
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
