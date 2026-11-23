from pathlib import Path
import socket,struct,threading,time,json,hashlib
from PIL import Image
class FrameSink:
 def __init__(self,port,output):
  self.port=port;self.output=Path(output);self.closed=False;self.socket=None;self.frames=0;self.last_save=0;self.thread=threading.Thread(target=self.read,daemon=True);self.thread.start()
 def close(self):
  self.closed=True
  if self.socket:
   try:self.socket.shutdown(socket.SHUT_RDWR)
   except OSError:pass
   self.socket.close()
 def take(self,s,n):
  out=bytearray()
  while len(out)<n:
   if self.closed:raise EOFError()
   try:b=s.recv(min(n-len(out),1048576))
   except socket.timeout:continue
   if not b:raise EOFError()
   out.extend(b)
  return bytes(out)
 def read(self):
  while not self.closed:
   try:
    with socket.create_connection(('127.0.0.1',self.port),timeout=1)as s:
     self.socket=s;s.settimeout(.5)
     while not self.closed:
      header=self.take(s,32);magic,w,h,stride,fmt,size,index,reserved=struct.unpack('<8I',header)
      if magic!=0x4d524642 or not 1<=w<=4096 or not 1<=h<=4096 or not w<=stride<=8192 or fmt not in(1,2,4)or size!=stride*h*(2 if fmt==4 else 4)or size>32*1024*1024:raise ValueError('Invalid Android framebuffer packet')
      data=self.take(s,size);self.frames+=1
      if time.monotonic()-self.last_save>=1:
       mode='RGB'if fmt==4 else'RGBA';decoder='BGR;16'if fmt==4 else'RGBX'if fmt==2 else'RGBA';image=Image.frombytes(mode,(w,h),data,'raw',decoder,stride*(2 if fmt==4 else 4));tmp=self.output/'android-frame.tmp';image.save(tmp,format='PNG');dest=self.output/'android-frame.png';tmp.replace(dest);report=dict(width=w,height=h,stride=stride,format=fmt,frame_index=index,received_frames=self.frames,sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),source='Original Android SurfaceFlinger composite via open-source gralloc adapter',host_time=time.time());(self.output/'android-frame.json').write_text(json.dumps(report,indent=2)+'\n');self.last_save=time.monotonic()
   except(OSError,EOFError):
    if not self.closed:time.sleep(.1)
   except Exception as error:
    (self.output/'android-frame-error.txt').write_text(repr(error));return
