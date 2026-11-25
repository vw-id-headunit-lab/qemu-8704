from pathlib import Path
import sys,os,json,subprocess,tempfile,struct,select,hashlib,time
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];record={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'label':label,'input':'Real native bool/int/float/capability state with variable-length format arrays and native invalid-cap error; red swap fixture not original GTF pixels','calls':[]};p=None
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
  def query(kind,pname):
   send(147,struct.pack('<II',kind,pname));count=struct.unpack('<I',read(4))[0];assert count<=4096;data=read(count*(1 if kind in [2,4]else 4));return count,data
  assert query(4,0xbe2)==(1,b'\0');send(33,struct.pack('<I',0xbe2));assert query(4,0xbe2)==(1,b'\1');send(34,struct.pack('<I',0xbe2));assert query(4,0xbe2)==(1,b'\0')
  send(2,struct.pack('<4i',1,2,70,30));count,data=query(1,0xba2);assert count==4 and struct.unpack('<4i',data)==(1,2,70,30)
  send(3,struct.pack('<4f',.25,.5,.75,1));count,data=query(3,0xc22);assert count==4 and struct.unpack('<4f',data)==(.25,.5,.75,1)
  count,data=query(2,0xc23);assert count==4 and data==b'\1'*4
  count,data=query(1,0x86a2);assert count==1;formats_count=struct.unpack('<i',data)[0];count,data=query(1,0x86a3);assert count==formats_count;formats=list(struct.unpack('<'+str(count)+'i',data))
  count,data=query(1,0x8df9);binary_count=struct.unpack('<i',data)[0];count,data=query(1,0x8df8);assert count==binary_count
  assert query(4,0xffffffff)==(1,b'\0');send(105);assert struct.unpack('<I',read(4))[0]==0x500;send(105);assert struct.unpack('<I',read(4))[0]==0
  send(2,struct.pack('<4i',0,0,80,40));record['native_state_query']={'enabled_false_true_false':True,'viewport':[1,2,70,30],'clear_color':[.25,.5,.75,1],'write_mask':[True]*4,'compressed_formats_count':formats_count,'compressed_formats':formats,'shader_binary_formats_count':binary_count,'invalid_cap_native_error':0x500}
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
