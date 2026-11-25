import os,glob,struct,json,sys,hashlib,socket,threading,time,errno
TOKEN=sys.argv[1].encode();assert len(TOKEN)==32
paths=[p for p in glob.glob('/sys/class/virtio-ports/*/name')if open(p).read().strip()=='bench.qnx-gl'];assert len(paths)==1
DEVICE='/dev/'+paths[0].split('/')[-2]
fd=os.open(DEVICE,os.O_RDWR);channels={};counter=0;write_lock=threading.Lock();channels_lock=threading.Lock();closed=threading.Event();counts={'tcp_received':0,'tcp_sent':0,'opened':0}
def send(kind,ident,data=b''):
 wire=struct.pack('>BII',kind,ident,len(data))+data
 with write_lock:
  while wire:
   n=os.write(fd,wire)
   if n<=0:raise ConnectionError('virtio stream closed')
   wire=wire[n:]
def read_exact(source,n):
 out=bytearray()
 while len(out)<n:
  b=source(n-len(out))
  if not b:raise ConnectionError('stream closed')
  out.extend(b)
 return bytes(out)
def incoming(client,ident):
 try:
  client.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1);client.settimeout(10);header=read_exact(client.recv,48);magic,pid,version,token=struct.unpack('<8sII32s',header)
  if magic!=b'QNXGLV1\0'or version!=1 or token!=TOKEN or not 1<pid<0x7fffffff:raise ValueError('QNX GL identity mismatch')
  client.settimeout(None)
  with channels_lock:channels[ident]=client
  counts['opened']+=1;send(1,ident,header)
  while True:
   data=client.recv(65536)
   if not data:break
   counts['tcp_received']+=len(data);send(2,ident,data)
 except (OSError,ValueError,ConnectionError)as e:print('CLIENT',ident,repr(e),flush=True)
 finally:
  with channels_lock:channels.pop(ident,None)
  client.close()
  if not closed.is_set():send(4,ident)
def from_host():
 try:
  while True:
   kind,ident,n=struct.unpack('>BII',read_exact(lambda n:os.read(fd,n),9))
   if kind not in [2,4]or n>65536 or(kind==4 and n):raise ValueError('Host frame invalid')
   data=read_exact(lambda n:os.read(fd,n),n)if n else b''
   with channels_lock:client=channels.get(ident)
   if not client:continue
   if kind==4:
    try:client.shutdown(socket.SHUT_RDWR)
    except OSError:pass
   else:client.sendall(data);counts['tcp_sent']+=n
 except (OSError,ValueError,ConnectionError)as e:print('HOST',repr(e),flush=True)
 finally:closed.set()
def listen_native_bridge(address):
 deadline=time.monotonic()+60
 while True:
  server=socket.socket(socket.AF_INET6,socket.SOCK_STREAM)
  try:server.bind((address,0))
  except OSError as error:
   server.close()
   if error.errno!=errno.EADDRNOTAVAIL or time.monotonic()>=deadline:raise
   time.sleep(.1)
   continue
  port=server.getsockname()[1]
  if not any(low<=port<=high for low,high in ((29700,29799),(29816,29816),(47000,49151),(49200,49799))):
   server.listen(8);server.settimeout(1);return server
  server.close()
  if time.monotonic()>=deadline:raise RuntimeError('No available bridge port outside original QNX VIWI redirects')
server=listen_native_bridge('fd53:7cb8:383:3::108');port=server.getsockname()[1]
open('/tmp/qnx-gl-relay-ready.json','w').write(json.dumps(dict(pid=os.getpid(),port=port,device=DEVICE,token_sha256=hashlib.sha256(TOKEN).hexdigest())))
print('READY',port,DEVICE,flush=True);threading.Thread(target=from_host,daemon=True).start()
while not closed.is_set():
 try:client,_=server.accept()
 except socket.timeout:continue
 counter+=1;threading.Thread(target=incoming,args=(client,counter),daemon=True).start()
server.close();print('CLOSED',json.dumps(counts),flush=True)
