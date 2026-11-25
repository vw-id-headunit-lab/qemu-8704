from pathlib import Path
import sys,json,socket,subprocess,time,struct,hashlib,tempfile,traceback
r=Path.cwd();sys.path.insert(0,str(r/'src/emu'));import original_8704_vm as vm
from ufs_platform import QTest
binary=Path(sys.argv[1]).resolve();label=sys.argv[2];t=r/'.local/mac-port/transaction';record=dict(label=label,binary=str(binary),binary_sha256=vm.sha(binary),input='Ethernet fixture MAC02:87:04:00:00:01, TX/RX independently owned normal descriptors, native GIC IRQ312/320',checks=[])
def check(name,actual,expected):
 record['checks'].append(dict(name=name,actual=actual,expected=expected));assert actual==expected,name
qmp=qt=p=wire=None
with tempfile.TemporaryDirectory(prefix='eqos-dma-')as temp:
 out=Path(temp);qp,tp,np=[vm.free_port()for _ in range(3)]
 cmd=[str(binary),'-machine','virt-8704-apm,gic-version=2,x-exynos-vlm=on,x-exynos-eqos-mac=0x010000048702','-cpu','cortex-a57','-accel','qtest','-m','2048','-display','none','-monitor','none','-nic','none','-serial','null','-serial','null','-S','-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(out/'qtest.log'),'-netdev',f'socket,id=eqos-test,listen=127.0.0.1:{np}','-global','driver=exynos-auto9.eqos,property=netdev,value=eqos-test']
 record['argv']=cmd
 try:
  with(out/'stderr').open('wb')as err:p=subprocess.Popen(cmd,stdout=err,stderr=err)
  deadline=time.monotonic()+8
  while time.monotonic()<deadline:
   if p.poll()is not None:raise RuntimeError('QEMU exit before packet model: '+str(p.returncode))
   try:qmp=vm.QMP(qp);break
   except OSError:time.sleep(.03)
  if qmp is None:raise TimeoutError('QMP startup')
  qmp.execute('cont');qt=QTest(tp);wire=socket.create_connection(('127.0.0.1',np),timeout=3);wire.settimeout(3)
  base=0x17d80000;c=base+0x18000;rxdesc=0x90000000;txdesc=0x90001000;rxbuf=0x90002000;txbuf=0x90003000
  qt.put(rxdesc,struct.pack('<4I',rxbuf,0,0,(1<<31)|(1<<30)|(1<<24)))
  qt.write(c+0x18,0);qt.write(c+0x1c,rxdesc);qt.write(c+0x30,0);qt.write(c+0x34,(1<<15)|(1<<6)|1)
  qt.write(c+8,(2048<<1)|1);qt.write(base,3)
  packet=bytes.fromhex('02870400000152540087040388b5')+b'MEB_EQOS_RX_'+bytes(range(40));packet=packet.ljust(64,b'\0')
  wire.sendall(struct.pack('>I',len(packet))+packet)
  for _ in range(100):
   status=qt.read(rxdesc+12)
   if not status&(1<<31):break
   time.sleep(.01)
  check('RX original packet bytes',qt.get(rxbuf,len(packet)).hex(),packet.hex());check('RX OWN cleared',bool(status&(1<<31)),False);check('RX full packet length including FCS',status&0x7fff,len(packet)+4);check('RX completion IRQ status',bool(qt.read(c+0x60)&(1<<6)),True)
  qt.write(c+0x60,0xffffffff);check('W1C acknowledges status',qt.read(c+0x60),0)
  tx=bytes.fromhex('52540087040302870400000188b5')+b'MEB_EQOS_TX_'+bytes(range(40));tx=tx.ljust(64,b'\0')
  qt.put(txbuf,tx);qt.put(txdesc,struct.pack('<4I',txbuf,0,len(tx)|(1<<31),len(tx)|(1<<31)|(1<<29)|(1<<28)))
  qt.write(c+0x10,0);qt.write(c+0x14,txdesc);qt.write(c+0x2c,0);qt.write(c+4,1)
  def read(n):
   b=bytearray()
   while len(b)<n:
    chunk=wire.recv(n-len(b))
    if not chunk:raise EOFError('Packet peer ended')
    b.extend(chunk)
   return bytes(b)
  n=struct.unpack('>I',read(4))[0];check('TX actual Ethernet packet',read(n).hex(),tx.hex());check('TX OWN cleared',bool(qt.read(txdesc+12)&(1<<31)),False);check('TX completion IRQ status',bool(qt.read(c+0x60)&1),True)
  path='/machine/exynos-eqos0';record['native_counters']={k:qmp.execute('qom-get',dict(path=path,property=k))for k in ['tx-packets','rx-packets','tx-descriptors','rx-descriptors','dma-errors']};check('DMA failures',record['native_counters']['dma-errors'],0);record['exit']=0
 except Exception as e:record['error']=repr(e);record['traceback']=traceback.format_exc();record['exit']=1
 finally:
  if wire:wire.close()
  if qt:record['qtest_transcript']=qt.transcript;qt.close()
  if qmp:
   try:qmp.execute('quit')
   except Exception:pass
   qmp.close()
  if p:
   try:record['qemu_exit']=p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.terminate();record['qemu_exit']=p.wait(timeout=5)
  record['stdout']='';record['stderr']=(out/'stderr').read_text(errors='replace')if(out/'stderr').exists()else''
(t/('eqos-packets-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({k:v for k,v in record.items()if k not in ['qtest_transcript','argv','traceback']},indent=2));raise SystemExit(record['exit'])
