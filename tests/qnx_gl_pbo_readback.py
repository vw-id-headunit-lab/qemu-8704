from pathlib import Path
import sys,os,struct,subprocess,tempfile,json,select,hashlib,importlib.util
r=Path(__file__).resolve().parents[1];binary=Path(sys.argv[1]).resolve();report={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'input':'Real ANGLE1140x114 red/green framebuffer -> bound PBO offset16 -> original MapBufferRange read path; no synthetic HUD pixels'}
with tempfile.TemporaryDirectory(prefix='qnx-pbo-')as tmp:
 d=Path(tmp);env={k:v for k,v in os.environ.items()if not k.startswith(('MHI2_','MEB_QNX_GL_'))};env.update(DYLD_LIBRARY_PATH=str(r/'.local/angle-sdk/lib'),MEB_QNX_GL_STRICT='original-gtf-v1',MEB_QNX_GL_ORIGINAL_PID='11',MEB_QNX_GL_FIXTURE='controlled-test-v1',MHI2_GL_FRAME=str(d/'frame.ppm'));stderr=(d/'stderr').open('wb');p=subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,env=env,bufsize=0)
 def read(n):
  data=bytearray()
  while len(data)<n:
   if not select.select([p.stdout],[],[],15)[0]:raise TimeoutError('same PBO response pending')
   b=os.read(p.stdout.fileno(),n-len(data))
   if not b:raise EOFError('host protocol closed')
   data.extend(b)
  return bytes(data)
 def send(op,data=b''):
  p.stdin.write(struct.pack('<6I',119,8,11,1,op,len(data))+data);p.stdin.flush()
 def words(op,*args):send(op,struct.pack('<'+'I'*len(args),*args))
 def egl(op,args=(),attrs=()):
  send(200,struct.pack('<II8Q',op,len(attrs),*(tuple(args)+(0,)*(8-len(args))))+struct.pack('<'+'q'*len(attrs),*attrs));h=struct.unpack('<IIQII',read(24));extra=read(h[4]);assert h[0],h;return h[2],extra
 def mapped(offset,length):
  send(153,struct.pack('<IIqq',0x88eb,1,offset,length));h=struct.unpack('<4I',read(16));assert h[0]and h[1]==length;data=read(length);words(156,0x88eb,h[0],0,0);assert struct.unpack('<I',read(4))[0]==1;return data
 try:
  display=egl(1)[0];egl(2,[display]);_,extra=egl(5,[display,1,1],[0x3033,1,0x3040,4,0x3024,8,0x3023,8,0x3022,8,0x3021,8,0x3038]);config=struct.unpack('<Q',extra)[0];context=egl(7,[display,config,0],[0x3098,3,0x3038])[0];width,height=1140,114;n=width*height*4;surface=egl(9,[display,config,0x90123456,width,height,10])[0];egl(12,[display,surface,surface,context]);send(3,struct.pack('<4f',1,0,0,1));words(4,0x4000);words(33,0xc11);words(65,0,0,width//2,height//2);send(3,struct.pack('<4f',0,1,0,1));words(4,0x4000);words(34,0xc11)
  words(15,1,7);words(16,0x88eb,7);send(17,struct.pack('<III',0x88eb,n+32,0x88e1)+b'\x7b'*(n+32));send(173,struct.pack('<6iQ',0,0,width,height,0x1908,0x1401,16));assert struct.unpack('<I',read(4))[0]==0
  data=mapped(16,n);expected=b''.join((b'\0\xff\0\xff'*(width//2)+b'\xff\0\0\xff'*(width-width//2))if y<height//2 else b'\xff\0\0\xff'*width for y in range(height));assert data==expected
  pad=mapped(0,n+32);assert pad[:16]==b'\x7b'*16 and pad[-16:]==b'\x7b'*16
  frames=list(d.glob('*.readback.*.ppm.json'));assert len(frames)==1;meta=json.loads(frames[0].read_bytes());assert meta['source']=='qnx_gl_fixture_test'and meta['sequence']==1 and meta['offset']==16 and meta['width']==width and meta['height']==height and meta['color_variation']==1
  raw=frames[0].with_suffix('').read_bytes();header,pixels=raw.split(b'\n255\n',1);assert header==b'P6\n1140 114';rgb=b''.join(expected[(y*width+x)*4:(y*width+x)*4+3]for y in range(height-1,-1,-1)for x in range(width));assert pixels==rgb
  send(173,struct.pack('<6iQ',0,0,width,height,0x1908,0x1401,n+32));assert struct.unpack('<I',read(4))[0]==0;words(105);assert struct.unpack('<I',read(4))[0]==0x502;assert mapped(16,n)==expected;assert json.loads(frames[0].read_bytes())['sequence']==1
  words(16,0x88eb,0);send(173,struct.pack('<6iQ',0,0,1,1,0x1908,0x1401,0x1000));assert struct.unpack('<I',read(4))[0]==4;assert read(4)==b'\0\xff\0\xff';words(105);assert struct.unpack('<I',read(4))[0]==0
  sys.path.insert(0,str(r/'tools'));import hvf_qnx_gl_frame as vf
  channel={'qnx_pid':11};valid=dict(meta,source='original_qnx_gl_pbo_readback');vf.validate_hud_metadata(valid,channel,0);bad=[dict(valid,source='qnx_gl_fixture_test'),dict(valid,pid=12),dict(valid,width=800),dict(valid,height=400),dict(valid,context_id=0),dict(valid,pixel_pack_buffer=0),dict(valid,sequence=0),dict(valid,nonzero_rgb_pixels=0),dict(valid,color_variation=0)];rejected=0
  for item in bad:
   try:vf.validate_hud_metadata(item,channel,0)
   except ValueError:rejected+=1
  try:vf.validate_hud_metadata(valid,channel,16)
  except ValueError:rejected+=1
  assert rejected==len(bad)+1
  report.update(exit=0,metadata=meta,actual_pbo_rgba_sha256=hashlib.sha256(data).hexdigest(),actual_image_rgb_sha256=hashlib.sha256(rgb).hexdigest(),driver_invalid_offset_error='0x502',padding_unchanged=True,repeated_map_without_new_read_not_freshened=True,invalid_sources_rejected=rejected,cpu_readback_unchanged=True)
 except EOFError as e:report.update(exit=2,error=str(e))
 except Exception as e:report.update(exit=1,error=repr(e))
 finally:
  p.stdin.close()
  try:p.wait(timeout=3)
  except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=3)
  stderr.close();report['host_exit']=p.returncode;report['stderr']=(d/'stderr').read_text(errors='replace')
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
