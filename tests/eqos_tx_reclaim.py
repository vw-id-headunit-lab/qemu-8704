from pathlib import Path
import sys,json,socket,subprocess,time,struct,hashlib,tempfile,io
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'src/emu'));import original_8704_vm as vm
from ufs_platform import QTest
from elftools.elf.elffile import ELFFile
binary=Path(sys.argv[1]).resolve();report={'binary':str(binary),'sha256':vm.sha(binary),'input':'Two native normal TX descriptors, no RX traffic, original QNX interrupt mask TBU bit2, mask/unmask/W1C/repost; actual wire bytes and TX IRQ pin1','checks':[]}
def check(name,actual,expected):
 report['checks'].append({'name':name,'actual':actual,'expected':expected});assert actual==expected,name
raw=(r/'var/inputs/support/归档/虚拟机平台对照/ufs-pma-native--devnp-dwceqos.so').read_bytes();assert hashlib.sha256(raw).hexdigest()=='7d2ca75ebc2c4e7548bb8e1dffd73e3acc966439fde0bf291b3db8bfce8bca15';elf=ELFFile(io.BytesIO(raw))
def code(va,n):
 seg=next(s for s in elf.iter_segments()if s['p_type']=='PT_LOAD'and s['p_vaddr']<=va and va+n<=s['p_vaddr']+s['p_filesz']);a=seg['p_offset']+va-seg['p_vaddr'];return raw[a:a+n]
word=struct.unpack('<I',code(0x1db48,4))[0];check('Original ISR branches on TBU bit2',(word>>19)&31,2);check('Original ISR TBNZ opcode',word&0x7f000000,0x37000000);report['original_driver_sites']={'status_read':hex(0x1db24),'TBU_test':hex(0x1db48),'ack_and_reclaim':hex(0x1db80),'reclaim_call':hex(0x1dbc0),'handler_sha256':hashlib.sha256(code(0x1db44,0xa0)).hexdigest()}
p=qmp=qt=wire=None
with tempfile.TemporaryDirectory(prefix='eqos-tx-reclaim-')as tmp:
 d=Path(tmp);qp,tp,np=[vm.free_port()for _ in range(3)];cmd=[str(binary),'-machine','virt-8704-apm,gic-version=2,x-exynos-vlm=on,x-exynos-eqos-mac=0x010000048702','-cpu','cortex-a57','-accel','qtest','-m','2048','-display','none','-monitor','none','-nic','none','-serial','null','-serial','null','-S','-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(d/'qtest.log'),'-netdev',f'socket,id=eqos-test,listen=127.0.0.1:{np}','-global','driver=exynos-auto9.eqos,property=netdev,value=eqos-test']
 try:
  with(d/'stderr').open('wb')as err:p=subprocess.Popen(cmd,stdout=err,stderr=err)
  for i in range(100):
   if p.poll()is not None:raise RuntimeError('QEMU startup failed')
   try:qmp=vm.QMP(qp);break
   except OSError:time.sleep(.03)
  assert qmp;qmp.execute('cont');qt=QTest(tp);qt.call('irq_intercept_out /machine/exynos-eqos0 sysbus-irq');wire=socket.create_connection(('127.0.0.1',np),timeout=3);wire.settimeout(3)
  c=0x17d98000;desc=0x90001000;buf=0x90003000;packet=bytes.fromhex('52540087040302870400000188b5')+b'ORIGINAL_QNX_TX_RECLAIM';packet=packet.ljust(64,b'\0')
  qt.put(buf,packet);qt.put(desc,struct.pack('<4I',buf,0,len(packet),len(packet)|(1<<31)|(1<<29)|(1<<28))+bytes(16));qt.write(c+0x10,0);qt.write(c+0x14,desc);qt.write(c+0x2c,1);qt.write(c+0x34,0);qt.write(0x17d80000,3);qt.write(c+4,1)
  def recv():
   def exact(n):
    data=b''
    while len(data)<n:
     b=wire.recv(n-len(data));assert b;data+=b
    return data
   return exact(struct.unpack('>I',exact(4))[0])
  check('TX actual Ethernet bytes',recv().hex(),packet.hex());check('TX ownership returned',qt.read(desc+12)>>31,0);status=qt.read(c+0x60);check('Empty next descriptor produces TBU',bool(status&4),True);check('Normal summary accompanies TBU',bool(status&(1<<15)),True);check('Masked TBU does not raise IRQ',sum(x=='IRQ raise 1'for x in qt.events),0)
  qt.write(c+0x34,4);check('Unmask pending TBU raises TX IRQ1',sum(x=='IRQ raise 1'for x in qt.events),1);qt.write(c+0x60,4);check('W1C clears TBU',qt.read(c+0x60)&4,0);check('W1C lowers TX IRQ1',sum(x=='IRQ lower 1'for x in qt.events),1)
  qt.put(desc+16,struct.pack('<4I',buf,0,len(packet),len(packet)|(1<<31)|(1<<29)|(1<<28)));qt.write(c+0x20,desc+32);check('Reposted descriptor sends actual bytes',recv().hex(),packet.hex());check('Repost ownership returned',qt.read(desc+28)>>31,0);check('Ring wrap raises next TBU',qt.read(c+0x60)&4,4);check('Second TX IRQ',sum(x=='IRQ raise 1'for x in qt.events),2)
  qt.write(c+0x34,0);check('Mask lowers pending IRQ',sum(x=='IRQ lower 1'for x in qt.events),2);qt.write(c+0x34,4);check('Reenable reasserts pending IRQ',sum(x=='IRQ raise 1'for x in qt.events),3);qt.write(c+0x60,0xffffffff);check('All status W1C',qt.read(c+0x60),0)
  report['events']=qt.events;report['counters']={n:qmp.execute('qom-get',{'path':'/machine/exynos-eqos0','property':n})for n in ['tx-packets','rx-packets','dma-errors','tx-errors']};check('No incoming RX needed',report['counters']['rx-packets'],0);check('Two actual packets',report['counters']['tx-packets'],2);check('No TX errors',report['counters']['tx-errors'],0);report['exit']=0
 except AssertionError as e:report.update(exit=2,error=str(e))
 except Exception as e:report.update(exit=1,error=repr(e))
 finally:
  if wire:wire.close()
  if qt:qt.close()
  if qmp:
   try:qmp.execute('quit')
   except Exception:pass
   qmp.close()
  if p:
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
   report['qemu_exit']=p.returncode
  report['stderr']=(d/'stderr').read_text(errors='replace')if(d/'stderr').exists()else''
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
