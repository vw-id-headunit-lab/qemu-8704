from pathlib import Path
import sys,json,importlib.util,struct,tempfile,subprocess,socket,time,hashlib
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'src/emu'));import original_8704_vm as vm
from ufs_platform import QTest
p=Path(sys.argv[1]);spec=importlib.util.spec_from_file_location('mcu_candidate',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);report={'source':str(p.resolve()),'input':'Explicit virtual MCU CFA packet, original USI09 RX FIFO; exact bytes, route/checksum rejection and duplicate-delivery identity. No PSO business acceptance inferred.'};q=qt=proc=wire=None
try:
 if not hasattr(m,'cfa_input'):raise AssertionError('No isolated CFA UART input path')
 with tempfile.TemporaryDirectory(prefix='meb-cfa-uart-')as tmp:
  d=Path(tmp);payload=struct.pack('>I4sIHH',1,b'\xff\xff\xff\xfe',14,1,10)+bytes.fromhex('73090000000000000000');body=bytes([3,0])+(10+len(payload)).to_bytes(2,'big')+bytes.fromhex('0000088000020098')+len(payload).to_bytes(2,'big')+payload;frame=b'\xff\xfe'+body+(sum(body)&65535).to_bytes(2,'big');value={'id':'0123456789abcdef','frame':frame.hex()};path=d/'mcu-uart-cfa-input.json';path.write_text(json.dumps(value));ident,selected=m.cfa_input(d);assert selected==frame
  for bad in [frame[:-1]+bytes([frame[-1]^1]),frame[:8]+b'\x03'+frame[9:],frame[:-1]]:
   path.write_text(json.dumps(dict(value,frame=bad.hex())))
   try:m.cfa_input(d)
   except ValueError:pass
   else:raise AssertionError('Invalid UART frame accepted')
  path.write_text(json.dumps(value));(d/'mcu-uart-cfa-delivery.json').write_text(json.dumps(value));assert m.cfa_input(d)is None
  path.write_text(json.dumps(dict(value,frame=frame.replace(b'\x73\x09',b'\x73\x08').hex())))
  try:m.cfa_input(d)
  except ValueError:pass
  else:raise AssertionError('Mutated identity accepted')
  qp,tp,np=[vm.free_port()for _ in range(3)];serial=m.serial_arguments(d,True,{'MEB_QNX_MCU_UART_PORT':str(np)});cmd=[str(r/'runtime/darwin/qemu-system-aarch64'),'-machine','virt-8704-apm,gic-version=2,x-exynos-vlm=on','-cpu','cortex-a57','-accel','qtest','-m','2048','-display','none','-monitor','none','-nic','none','-serial','null','-serial','null',*serial,'-S','-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(d/'qtest.log')];report['qemu_command']=cmd
  try:
   with(d/'stderr').open('wb')as err:proc=subprocess.Popen(cmd,stdout=err,stderr=err)
   for _ in range(100):
    if proc.poll()is not None:raise RuntimeError('Native QEMU ended')
    try:q=vm.QMP(qp);break
    except OSError:time.sleep(.03)
   assert q;q.execute('cont');qt=QTest(tp);qt.write(0x10821018,1);qt.write(0x10960000,3);qt.write(0x10960004,0x3c5);qt.write(0x10960008,0x111);wire=socket.create_connection(('127.0.0.1',np),timeout=2);wire.sendall(selected)
   for _ in range(100):
    if qt.read(0x10960010)&1:break
    time.sleep(.01)
   assert qt.read(0x10960010)&1;actual=bytes(qt.read(0x10960024)&255 for _ in selected);assert actual==selected;assert not qt.read(0x10960010)&1;report.update(native_fifo=actual.hex(),packet_sha256=hashlib.sha256(actual).hexdigest(),exit=0)
  finally:
   if wire:wire.close()
   if qt:qt.close()
   if q:
    try:q.execute('quit')
    except Exception:pass
    q.close()
   if proc:report['qemu_exit']=proc.wait(timeout=5)
   report['stderr']=(d/'stderr').read_text(errors='replace')
except Exception as error:report.update(error=repr(error),exit=2)
print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(report['exit'])
