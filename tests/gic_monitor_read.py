from pathlib import Path
import json,socket,struct,subprocess,sys,tempfile,time
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'src/emu'));import original_8704_vm as vm
binary=Path(sys.argv[1]).resolve();report={'binary':str(binary),'sha256':vm.sha(binary),'checks':[]};p=q=None
with tempfile.TemporaryDirectory(prefix='gic-monitor-')as tmp:
 d=Path(tmp);port=vm.free_port();code=struct.pack('<8I2Q',0x58000100,0xb9400001,0x58000102,0xb9000041,0x52824683,0xb9000443,0xd503207f,0x17ffffff,0x10101000,0x90000000);(d/'cpu.bin').write_bytes(code)
 cmd=[str(binary),'-machine','virt-8704-apm,gic-version=2,virtualization=on,x-exynos-vlm=on','-cpu','cortex-a57','-accel','tcg','-smp','2','-m','2048','-display','none','-monitor','none','-nic','none','-serial','null','-serial','null','-S','-qmp',f'tcp:127.0.0.1:{port},server=on,wait=off','-device',f'loader,file={d}/cpu.bin,addr=0x90010000,cpu-num=0,force-raw=on']
 try:
  with(d/'stderr').open('wb')as f:p=subprocess.Popen(cmd,stdout=f,stderr=f)
  for _ in range(100):
   if p.poll()is not None:raise RuntimeError('QEMU exited during startup')
   try:q=vm.QMP(port);break
   except OSError:time.sleep(.03)
  assert q
  result=q.execute('human-monitor-command',{'command-line':'xp /1wx 0x10101000'});report['monitor_read']=result;assert 'Cannot access memory'in result
  assert q.execute('query-status')['status']=='prelaunch';report['checks'].append('CPU-less distributor read rejected without process exit')
  children=q.execute('qom-list',{'path':'/machine/unattached'});gic=next(x['name']for x in children if x['type']=='child<arm_gic>');state=json.loads(q.execute('qom-get',{'path':'/machine/unattached/'+gic,'property':'x-auto9-eqos-irq-state'}));assert [x['id']for x in state]==list(range(344,360));report['checks'].append('Read-only QOM snapshot covers actual EQOS IRQs344..359')
  q.execute('cont');time.sleep(.2);q.execute('stop');result=q.execute('human-monitor-command',{'command-line':'xp /2wx 0x90000000'});report['guest_read_result']=result;assert '0x00001234'in result;report['checks'].append('Actual TCG CPU distributor read and marker store complete');report['exit']=0
 except Exception as e:report.update(exit=2,error=repr(e))
 finally:
  if q:
   try:q.execute('quit')
   except Exception:pass
   q.close()
  if p:
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
   report['qemu_exit']=p.returncode
  report['stderr']=(d/'stderr').read_text(errors='replace')if(d/'stderr').exists()else''
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
