from pathlib import Path
import sys,os,json,subprocess,tempfile,struct,select,hashlib,time
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];record={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'label':label,'input':'Native typed object binding queries preserve guest names across overlapping GL object namespaces and framebuffer restore; not original GUI proof','calls':[]};p=None
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
  def binding(pname,expected):
   observed=[]
   for kind in [1,3]:
    send(147,struct.pack('<II',kind,pname));n=struct.unpack('<I',read(4))[0];assert n==1;value=struct.unpack('<i'if kind==1 else'<f',read(4))[0];observed.append(value);assert value==expected,(hex(pname),value,expected)
   send(146,struct.pack('<I',pname));n=struct.unpack('<I',read(4))[0];assert n==1;value=struct.unpack('<q',read(8))[0];assert value==expected;return observed
  send(15,struct.pack('<II',1,501));send(16,struct.pack('<II',0x8892,501));binding(0x8894,501)
  send(50,struct.pack('<II',1,601));send(51,struct.pack('<II',0xde1,601));binding(0x8069,601);binding(0x8894,501)
  send(52,struct.pack('<9I',0xde1,0,0x8058,4,4,0,0x1908,0x1401,0))
  send(78,struct.pack('<II',1,701));send(60,struct.pack('<II',0x8d40,701));binding(0x8ca6,701)
  send(62,struct.pack('<5I',0x8d40,0x8ce0,0xde1,601,0));send(109,struct.pack('<I',0x8d40));assert struct.unpack('<I',read(4))[0]==0x8cd5
  send(79,struct.pack('<II',1,801));send(61,struct.pack('<II',0x8d41,801));binding(0x8ca7,801)
  send(60,struct.pack('<II',0x8d40,0));binding(0x8ca6,0)
  send(60,struct.pack('<II',0x8d40,701));send(109,struct.pack('<I',0x8d40));assert struct.unpack('<I',read(4))[0]==0x8cd5
  send(60,struct.pack('<II',0x8d40,0));send(105);assert struct.unpack('<I',read(4))[0]==0
  isolated=egl(7,[display,config,0],[0x3098,3,0x3038])[0][2];egl(12,[display,surface,surface,isolated])
  send(15,struct.pack('<II',1,502));send(16,struct.pack('<II',0x8892,502));binding(0x8894,502)
  send(50,struct.pack('<II',1,602));send(51,struct.pack('<II',0xde1,602));binding(0x8069,602)
  send(78,struct.pack('<II',1,702));send(60,struct.pack('<II',0x8d40,702));binding(0x8ca6,702)
  egl(12,[display,surface,surface,context]);binding(0x8894,501);binding(0x8069,601);binding(0x8ca6,0)
  shared=egl(7,[display,config,context],[0x3098,3,0x3038])[0][2];egl(12,[display,surface,surface,shared])
  send(16,struct.pack('<II',0x8892,501));binding(0x8894,501)
  egl(12,[display,surface,surface,context]);binding(0x8894,501)
  record['native_virtual_bindings']={'buffer':501,'texture':601,'framebuffer':701,'renderbuffer':801,'typed_queries':['int32','float','int64'],'rebound_fbo_native_status':0x8cd5,'colliding_native_names_distinguished':True,'isolated_and_shared_contexts_verified':True}
  send(2,struct.pack('<4i',0,0,80,40))
  send(3,struct.pack('<4f',1,0,0,1));send(4,struct.pack('<I',0x4000));egl(13,[display,surface]);frames=list(d.glob('*.ppm.json'));assert len(frames)==1;meta=json.loads(frames[0].read_text());assert meta['source']=='qnx_gl_fixture_test'and meta['width']==80 and meta['height']==40 and meta['swap_count']==1;raw=frames[0].with_suffix('').read_bytes();header,bpp=raw.split(b'\n255\n',1);assert header==b'P6\n80 40'and bpp==b'\xff\0\0'*(80*40);record.update(metadata=meta,pixels_sha256=hashlib.sha256(bpp).hexdigest(),exit=0)
 except Exception as e:
  import traceback;record.update(exit=1,error=repr(e),traceback=traceback.format_exc())
 finally:
  if p:
   p.stdin.close()
   try:p.wait(timeout=3)
   except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=3)
   record['host_exit']=p.returncode
  record['stderr']=(d/'stderr').read_text(errors='replace')
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
