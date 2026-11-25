from pathlib import Path
import ctypes as C,os,select,struct,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/hvf_bench/src'))
from hmi_bench.navigation_fixture import uplink
p,_=uplink(sys.argv[1])
lib=C.CDLL('/opt/homebrew/opt/libslirp/lib/libslirp.0.dylib')
class Addr(C.Structure):_fields_=[('value',C.c_uint32)]
class Addr6(C.Structure):_fields_=[('value',C.c_uint32*4)]
class Config(C.Structure):
 _fields_=[('version',C.c_uint32),('restricted',C.c_int),('in_enabled',C.c_bool),('vnetwork',Addr),('vnetmask',Addr),('vhost',Addr),('in6_enabled',C.c_bool),('vprefix_addr6',Addr6),('vprefix_len',C.c_uint8),('vhost6',Addr6),('vhostname',C.c_char_p),('tftp_server_name',C.c_char_p),('tftp_path',C.c_char_p),('bootfile',C.c_char_p),('vdhcp_start',Addr),('vnameserver',Addr),('vnameserver6',Addr6),('vdnssearch',C.c_void_p),('vdomainname',C.c_char_p),('if_mtu',C.c_size_t),('if_mru',C.c_size_t),('disable_host_loopback',C.c_bool),('enable_emu',C.c_bool),('outbound_addr',C.c_void_p),('outbound_addr6',C.c_void_p),('disable_dns',C.c_bool),('disable_dhcp',C.c_bool),('mfr_id',C.c_uint32),('oob_eth_addr',C.c_ubyte*6)]
WRITE=C.CFUNCTYPE(C.c_ssize_t,C.c_void_p,C.c_size_t,C.c_void_p);ERROR=C.CFUNCTYPE(None,C.c_char_p,C.c_void_p);CLOCK=C.CFUNCTYPE(C.c_int64,C.c_void_p);NEW=C.CFUNCTYPE(C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p);FREE=C.CFUNCTYPE(None,C.c_void_p,C.c_void_p);MOD=C.CFUNCTYPE(None,C.c_void_p,C.c_int64,C.c_void_p);FD=C.CFUNCTYPE(None,C.c_int,C.c_void_p);NOTIFY=C.CFUNCTYPE(None,C.c_void_p);ADD=C.CFUNCTYPE(C.c_int,C.c_int,C.c_int,C.c_void_p);EVENT=C.CFUNCTYPE(C.c_int,C.c_int,C.c_void_p)
class Callbacks(C.Structure):_fields_=[('send_packet',WRITE),('guest_error',ERROR),('clock_get_ns',CLOCK),('timer_new',NEW),('timer_free',FREE),('timer_mod',MOD),('register_poll_fd',FD),('unregister_poll_fd',FD),('notify',NOTIFY),('init_completed',C.c_void_p),('timer_new_opaque',C.c_void_p),('register_poll_socket',C.c_void_p),('unregister_poll_socket',C.c_void_p)]
lib.slirp_new.argtypes=[C.POINTER(Config),C.POINTER(Callbacks),C.c_void_p];lib.slirp_new.restype=C.c_void_p
lib.slirp_input.argtypes=[C.c_void_p,C.c_void_p,C.c_int];lib.slirp_pollfds_fill_socket.argtypes=[C.c_void_p,C.POINTER(C.c_uint32),ADD,C.c_void_p];lib.slirp_pollfds_poll.argtypes=[C.c_void_p,C.c_int,EVENT,C.c_void_p];lib.slirp_cleanup.argtypes=[C.c_void_p]
timers={};sequence=0
@WRITE
def send(buf,length,opaque):
 try:
  frame=struct.pack('>I',length)+C.string_at(buf,length);offset=0
  while offset<len(frame):offset+=os.write(1,frame[offset:])
  return length
 except OSError:return -1
@ERROR
def error(msg,opaque):print(msg.decode(errors='replace'),file=sys.stderr,flush=True)
@CLOCK
def clock(opaque):return time.monotonic_ns()
@NEW
def new(cb,data,opaque):
 global sequence
 sequence+=1;timers[sequence]=[C.CFUNCTYPE(None,C.c_void_p)(cb),data,None];return sequence
@FREE
def free(handle,opaque):timers.pop(handle,None)
@MOD
def mod(handle,expiration,opaque):timers[handle][2]=expiration
@FD
def fd(number,opaque):pass
@NOTIFY
def notify(opaque):pass
config=Config();config.version=4;config.in_enabled=True;config.disable_host_loopback=True;config.disable_dhcp=True
for key,value in [('vnetwork',p['network'].split('/')[0]),('vnetmask','255.255.255.0'),('vhost',p['gateway']),('vdhcp_start',p['address']),('vnameserver',p['dns'])]:setattr(config,key,Addr.from_buffer_copy(bytes(map(int,value.split('.')))))
callbacks=Callbacks(send,error,clock,new,free,mod,fd,fd,notify,None,None,None,None);slirp=lib.slirp_new(C.byref(config),C.byref(callbacks),None)
if not slirp:raise RuntimeError('libslirp initialization failed')
pollfds=[];results={}
@ADD
def add(fd,events,opaque):
 pollfds.append((fd,events));return len(pollfds)-1
@EVENT
def events(index,opaque):
 event=results.get(pollfds[index][0],0);return sum(bit for bit,flag in [(1,select.POLLIN),(2,select.POLLOUT),(4,select.POLLPRI),(8,select.POLLERR),(16,select.POLLHUP)]if event&flag)
packet=bytearray();os.set_blocking(0,False)
print('NAVIGATION_REAL_NAT_READY '+p['network'],file=sys.stderr,flush=True)
try:
 while True:
  pollfds.clear();timeout=C.c_uint32(100);lib.slirp_pollfds_fill_socket(slirp,C.byref(timeout),add,None);poll=select.poll();poll.register(0,select.POLLIN)
  for number,mask in pollfds:poll.register(number,sum(flag for bit,flag in [(1,select.POLLIN),(2,select.POLLOUT),(4,select.POLLPRI)]if mask&bit))
  for timer in timers.values():
   if timer[2]is not None:timeout.value=min(timeout.value,max(0,timer[2]-time.monotonic_ns()//1000000))
  results=dict(poll.poll(timeout.value));lib.slirp_pollfds_poll(slirp,0,events,None)
  if results.get(0,0)&(select.POLLIN|select.POLLHUP|select.POLLERR|select.POLLNVAL):
   data=os.read(0,262144)
   if not data:break
   packet.extend(data)
   while len(packet)>=4:
    length=struct.unpack('>I',packet[:4])[0]
    if not 14<=length<=65535:raise ValueError('Invalid Ethernet frame')
    if len(packet)<length+4:break
    frame=bytes(packet[4:length+4]);del packet[:length+4]
    if frame[6:12]==bytes.fromhex(p['mac'].replace(':','')):lib.slirp_input(slirp,frame,length)
  for handle in list(timers):
   timer=timers.get(handle)
   if timer and timer[2]is not None and timer[2]<=time.monotonic_ns()//1000000:timer[2]=None;timer[0](timer[1])
finally:lib.slirp_cleanup(slirp)
