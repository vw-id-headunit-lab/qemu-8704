from pathlib import Path
import sys,os,json,subprocess,tempfile,struct,select,hashlib,time
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];record={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'label':label,'input':'Real GLES3 buffer map/read/write/unmap, explicit relative flush, native invalid-offset error, size64 query; red fixture is not original GTF frame','calls':[]};p=None
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
  send(15,struct.pack('<II',1,31));send(16,struct.pack('<II',0x8892,31));initial=bytes(range(16));send(17,struct.pack('<III',0x8892,16,0x88e8)+initial)
  def map_buffer(offset,length,flags):
   send(153,struct.pack('<IIqq',0x8892,flags,offset,length));head=struct.unpack('<4I',read(16));return head,read(head[1])
  def unmap(token,data=b''):
   send(156,struct.pack('<4I',0x8892,token,len(data),0)+data);return struct.unpack('<I',read(4))[0]
  head,data=map_buffer(4,8,3);assert head[0]and data==initial[4:12];send(154,struct.pack('<II',0x8892,0x88bd));assert struct.unpack('<4I',read(16))[0]==head[0]
  changed=b'AB'+data[2:];shared=egl(7,[display,config,context],[0x3098,3,0x3038])[0][2];egl(12,[display,surface,surface,shared]);send(16,struct.pack('<II',0x8892,31));assert unmap(head[0],changed)==1;egl(12,[display,surface,surface,context])
  head,data=map_buffer(0,16,1);expected=initial[:4]+changed+initial[12:];assert data==expected;assert unmap(head[0])==1
  head,data=map_buffer(0,16,0x13);assert data==expected;send(155,struct.pack('<IIqqII',0x8892,head[0],2,2,2,0)+b'XY');assert struct.unpack('<I',read(4))[0]==1;assert unmap(head[0])==1
  head,data=map_buffer(0,16,1);expected=expected[:2]+b'XY'+expected[4:];assert data==expected;assert unmap(head[0])==1
  head,data=map_buffer(-1,8,1);assert head[0]==0 and not data;send(105);assert struct.unpack('<I',read(4))[0]==0x501
  send(157,struct.pack('<III',2,0x8892,0x8764));assert struct.unpack('<q',read(8))[0]==16
  send(157,struct.pack('<III',1,0x8892,0x88bc));assert struct.unpack('<i',read(4))[0]==0
  send(105);assert struct.unpack('<I',read(4))[0]==0
  record['native_mapping']={'initial_hex':initial.hex(),'final_hex':expected.hex(),'shared_context_unmap':True,'partial_write_preserved':True,'explicit_flush_relative_range':[2,2],'native_unmap_true':True,'invalid_offset_native_error':0x501,'buffer_size64':16,'mapped_after_unmap':False}
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
