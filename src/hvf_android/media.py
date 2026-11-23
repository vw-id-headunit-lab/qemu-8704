from pathlib import Path
import sys,json,threading,socket,io,bisect
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src/firmware'),str(ROOT/'src/hvf_bench/src')]
from android_sparse import partitions
from hmi_bench.sparse_nbd import SparseNBDServer
class Region(io.RawIOBase):
 def __init__(self,file,offset,size):self.file,self.offset,self.size,self.pos=file,offset,size,0
 def readable(self):return True
 def seekable(self):return True
 def tell(self):return self.pos
 def seek(self,n,whence=0):
  n=n if whence==0 else self.pos+n if whence==1 else self.size+n
  if not 0<=n<=self.size:raise ValueError('Outside source region')
  self.pos=n;return n
 def read(self,n=-1):
  n=self.size-self.pos if n<0 else min(n,self.size-self.pos)
  if not 0<=n<=64*1024*1024:raise ValueError('Unbounded source read')
  self.file.seek(self.offset+self.pos);b=self.file.read(n)
  if len(b)!=n:raise EOFError('Short source')
  self.pos+=n;return b
class LogicalImage:
 def __init__(self,base,name):
  self.path=Path(base).resolve();self.file=self.path.open('rb');self.lock=threading.Lock();layout=json.loads(self.path.with_suffix('.json').read_bytes());assert layout['profile']=='virtual-8704-archive-lun0-android-dsp-v1'and layout['sector_bytes']==4096
  row=next(p for p in layout['partitions']if p['slot']==48);assert row['label']=='super'and row['payload']['sha256']=='b36d6ecb91aca84b5335ef2b7c2a37f025fdfbe69a6883a2e11b32c44b6abdba';self.base=row['first']*4096;v=Region(self.file,self.base,row['payload']['bytes']);self.partition=next(p for p in partitions(v)if p['name']==name);self.size=self.partition['size'];self.runs=[];offset=0
  for sectors,kind,physical,device in self.partition['extents']:
   length=sectors*512
   if kind not in(0,1)or device!=0 or physical*512+length>v.size:raise ValueError('Invalid LP extent')
   self.runs.append((offset,offset+length,kind,self.base+physical*512));offset+=length
  assert offset==self.size and offset>0;self.starts=[x[0]for x in self.runs]
 def read(self,offset,length):
  if offset<0 or length<0 or offset+length>self.size:raise ValueError('Outside logical partition')
  out=bytearray()
  with self.lock:
   while length:
    start,end,kind,physical=self.runs[bisect.bisect_right(self.starts,offset)-1];n=min(length,end-offset)
    if kind==0:self.file.seek(physical+offset-start);b=self.file.read(n)
    else:b=bytes(n)
    if len(b)!=n:raise EOFError('Short source extent')
    out.extend(b);offset+=n;length-=n
  return bytes(out)
 def close(self):self.file.close()
class LogicalNBDServer(SparseNBDServer):
 def __init__(self,base,name):
  self.image=LogicalImage(base,name);self.listener=socket.socket();self.listener.bind(('127.0.0.1',0));self.listener.listen(4);self.port=self.listener.getsockname()[1];self.closed=False;self.clients=[];self.stats=dict(source=str(self.image.path),partition=name,export_bytes=self.image.size,source_open_mode='rb',read_requests=0,read_bytes=0,write_requests_denied=0,options=[],errors=[]);threading.Thread(target=self.accept,daemon=True).start()
