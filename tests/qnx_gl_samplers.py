from pathlib import Path
import sys,os,json,subprocess,tempfile,struct,select,hashlib,time
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];record={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'label':label,'input':'Controlled GLES3 sampler lifecycle: virtual101, native parameters, virtual binding, delete/unbind, actual GL error0 and red swap; not original GTF pixels','calls':[]};p=None
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
  def sampler_query(kind,obj,pname=0):
   send(152,struct.pack('<3I',kind,obj,pname));return read(4 if kind==1 or pname!=0x1004 else 16)
  send(150,struct.pack('<3I',1,1,101));assert struct.unpack('<II',read(8))==(1,101);assert struct.unpack('<I',sampler_query(1,101))[0]==1
  send(151,struct.pack('<4I',2,101,0x2801,0x2600));assert struct.unpack('<i',sampler_query(2,101,0x2801))[0]==0x2600
  send(151,struct.pack('<4I',4,101,0x2802,0x812f));assert struct.unpack('<i',sampler_query(2,101,0x2802))[0]==0x812f
  send(151,struct.pack('<3If',5,101,0x813a,2.5));assert struct.unpack('<f',sampler_query(3,101,0x813a))[0]==2.5
  send(151,struct.pack('<3If',3,101,0x813b,7.5));assert struct.unpack('<f',sampler_query(3,101,0x813b))[0]==7.5
  send(151,struct.pack('<4I',1,0,101,0));send(104,struct.pack('<I',0x8919));binding=struct.unpack('<16i',read(64))[0];assert binding==101,('virtual sampler name lost',binding)
  send(150,struct.pack('<3I',2,1,101));assert struct.unpack('<I',read(4))[0]==0;assert struct.unpack('<I',sampler_query(1,101))[0]==0
  send(104,struct.pack('<I',0x8919));assert struct.unpack('<16i',read(64))[0]==0
  send(105);assert struct.unpack('<I',read(4))[0]==0
  record['sampler_native_results']={'virtual_name':101,'is_after_gen':True,'min_filter':0x2600,'wrap_s':0x812f,'min_lod':2.5,'max_lod':7.5,'binding_virtual_name':binding,'is_after_delete':False,'binding_after_delete':0,'gl_error':0}
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
