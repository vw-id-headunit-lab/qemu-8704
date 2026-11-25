from pathlib import Path
import sys,json,socket,subprocess,time,struct,tempfile,importlib.util,hashlib,traceback
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/emu'))
import original_8704_vm as vm
from ufs_platform import QTest
role=Path(sys.argv[1]).resolve();label=sys.argv[2];binary=ROOT/'runtime/darwin/qemu-system-aarch64'
record=dict(label=label,role_root=str(role),input='Original USI09 RX FIFO, fixed A0 pattern, stationary power9 and ignition true',checks=[])
def check(name,actual,expected):
 record['checks'].append(dict(name=name,actual=actual,expected=expected));assert actual==expected,name
qmp=qt=process=wire=None
with tempfile.TemporaryDirectory(prefix='qnx-mcu-uart-')as td:
 temp=Path(td);qp,tp,np=[vm.free_port()for _ in range(3)]
 source=(role/'src/emu/vlm_offline_live_darwin.py').read_text()
 if 'from qnx_mcu_uart import serial_arguments' in source:
  path=role/'src/emu/qnx_mcu_uart.py';spec=importlib.util.spec_from_file_location('selected_mcu_uart',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);serial=module.serial_arguments(temp,True,{'MEB_QNX_MCU_UART_PORT':str(np)})
  request=bytes.fromhex('feff0000001280020000020000a0000801aa55aa55aa550b0447');reply=module.transport_reply(request);check('source-backed reset reply',reply.hex(),'fffe0000001200020080020000a0000881aa55aa55aa550b04c7');check('bad checksum not acknowledged',module.transport_reply(request[:-1]+b'\0'),None);check('non-reset application not acknowledged',module.transport_reply(module.power_frame(9)),None);packet=module.power_frame(9)
 else:
  assert "command+=['-serial','file:'+str(output/'exynos-secondary-uart.bin')]" in source;serial=['-serial','file:'+str(temp/'exynos-secondary-uart.bin')];packet=bytes.fromhex('fffe0100001100020080020000910007010009000000000138')
 record['serial_arguments']=serial
 argv=[str(binary),'-machine','virt-8704-apm,gic-version=2,x-exynos-vlm=on','-cpu','cortex-a57','-accel','qtest','-m','2048','-display','none','-monitor','none','-nic','none','-serial','null','-serial','null',*serial,'-S','-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(temp/'qtest.log')]
 try:
  with(temp/'stderr').open('wb')as stream:process=subprocess.Popen(argv,stdout=stream,stderr=stream)
  deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   if process.poll()is not None:raise RuntimeError('Native QEMU early exit '+str(process.returncode))
   try:qmp=vm.QMP(qp);break
   except OSError:time.sleep(.02)
  qmp.execute('cont');qt=QTest(tp);qt.write(0x10821018,1);qt.write(0x10960000,3);qt.write(0x10960004,0x3c5);qt.write(0x10960008,0x111)
  wire=socket.create_connection(('127.0.0.1',np),timeout=1);wire.sendall(packet)
  for _ in range(100):
   if qt.read(0x10960010)&1:break
   time.sleep(.01)
  check('native RX data-ready',bool(qt.read(0x10960010)&1),True);actual=bytes(qt.read(0x10960024)&255 for _ in packet);check('native guest FIFO exact packet',actual.hex(),packet.hex());check('original power route000100/800100',packet.hex(),'fffe010000110001008001000091000701090900000000013f');check('FIFO drained',bool(qt.read(0x10960010)&1),False)
  check('stationary vehicle input available',hasattr(module,'vehicle_frame'),True)
  vehicle=module.vehicle_frame(True,2);wire.sendall(vehicle)
  for _ in range(100):
   if qt.read(0x10960010)&1:break
   time.sleep(.01)
  check('vehicle RX data-ready',bool(qt.read(0x10960010)&1),True);actual=bytes(qt.read(0x10960024)&255 for _ in vehicle);check('native vehicle FIFO exact packet',actual.hex(),vehicle.hex());check('stationary vehicle payload',vehicle[6:-2].hex(),'000100800100009100080500000000010101');check('vehicle FIFO drained',bool(qt.read(0x10960010)&1),False);record['exit']=0
 except Exception as e:record.update(exit=1,error=repr(e),traceback=traceback.format_exc())
 finally:
  if wire:wire.close()
  if qt:record['qtest_transcript']=qt.transcript;qt.close()
  if qmp:
   try:qmp.execute('quit')
   except Exception:pass
   qmp.close()
  if process:
   try:record['qemu_exit']=process.wait(timeout=5)
   except subprocess.TimeoutExpired:process.terminate();record['qemu_exit']=process.wait(timeout=5)
  record['stderr']=(temp/'stderr').read_text(errors='replace');record['stdout']=''
(ROOT/'.local/mac-port/transaction'/('mcu-uart-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({k:v for k,v in record.items()if k not in ['qtest_transcript','traceback']},indent=2));raise SystemExit(record['exit'])
