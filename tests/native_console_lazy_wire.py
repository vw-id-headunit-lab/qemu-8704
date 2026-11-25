from pathlib import Path
import importlib.util,json,socket,struct,sys,tempfile,threading,time

r=Path.cwd();base=Path(sys.argv[1])if len(sys.argv)>1 else r;sys.path.insert(0,str(r/'tools'));sys.path.insert(0,str(r/'src/emu'))
report={'scope':'Real loopback sockets with isolated metadata fixture, not original OS acceptance','input':'Android uses its mux while dedicated Linux UART resets; preserve pending nonce; later Linux attach cannot close Android'}

class Server:
 def __init__(self,reset=False):
  self.reset=reset;self.count=0;self.received=[];self.stop=threading.Event();self.clients=[];self.s=socket.socket();self.s.bind(('127.0.0.1',0));self.s.listen();self.s.settimeout(.1);self.port=self.s.getsockname()[1];self.t=threading.Thread(target=self.run);self.t.start()
 def run(self):
  while not self.stop.is_set():
   try:c,_=self.s.accept()
   except TimeoutError:continue
   self.count+=1;self.clients.append(c)
   if self.reset:c.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0));c.close();continue
   c.settimeout(.1)
   while not self.stop.is_set():
    try:b=c.recv(1024)
    except TimeoutError:continue
    if not b:break
    self.received.append(b);c.sendall(b)
   c.close()
 def close(self):
  self.stop.set();self.t.join(3);assert not self.t.is_alive();self.s.close()

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

main=Server();direct=Server(True);bridge=None;vm=None
try:
 vm=load('original_8704_vm',base/'src/emu/original_8704_vm.py');m=load('lazy_native_under_test',base/'tools/native_guest_command.py')
 with tempfile.TemporaryDirectory(prefix='native-lazy-')as tmp:
  d=Path(tmp);(d/'launch.json').write_text(json.dumps({'command':['socket,id=oem-vlm-console,host=127.0.0.1,port='+str(main.port)+',server=on,wait=off,logfile=fixture']}));meta={'port':main.port,'focus':{g:{'vmid':i,'keys_hex':[k]}for g,i,k in [('linux',3,'1b4f52'),('android',4,'1b4f53')]}}
  def tap(self,session):self.session=Path(session);self.meta=meta;self.direct={'port':direct.port}
  vm.NativeConsoleTap.__init__=tap;vm.NativeConsoleTap.focus=lambda self:{'vmid':4,'host_monotonic':time.monotonic(),'read_only':False};m.session=lambda guest:(d,{'qemu_pid':42},0);ticket={'nonce':'UNCHANGED_PENDING_FIXTURE'};m._pending={'linux':ticket};bridge,_,_=m._controller('android');time.sleep(.2);unneeded=direct.count
  def echo(payload):
   bridge.socket.sendall(payload);deadline=time.monotonic()+3
   while time.monotonic()<deadline:
    if payload in(d/'vlm-console-rx.bin').read_bytes():return True
    time.sleep(.02)
   return False
  first=echo(b'ANDROID_MUX_FIRST\n')
  try:m._controller('linux')
  except OSError as error:report['linux_attach_error']=repr(error)
  second=echo(b'ANDROID_MUX_AFTER_LINUX_RESET\n');report.update(unneeded_linux_connections_for_android=unneeded,first_mux_reply=first,second_mux_reply=second,pending_nonce_unchanged=m._pending.get('linux')is ticket,received=[x.decode()for x in main.received])
  assert unneeded==0 and first and second and m._pending.get('linux')is ticket
 report['exit']=0
except Exception as error:report.update(exit=2,error=repr(error))
finally:
 if bridge:bridge.close()
 main.close();direct.close()
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
