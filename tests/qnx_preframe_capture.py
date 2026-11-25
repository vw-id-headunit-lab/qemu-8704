from pathlib import Path
import sys,json,struct,subprocess,tempfile,time,hashlib
r=Path.cwd();sys.path.insert(0,str(r/'src/emu'));import original_8704_vm as vm;from ufs_platform import QTest
binary=Path(sys.argv[1]).resolve();label=sys.argv[2];q=qt=p=None;record=dict(binary=str(binary),sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),label=label,input='Original-shaped AFBC/no-tiler preframe1 descriptor with identifiable draw/state/shader; same read-only rejection and output canary')
with tempfile.TemporaryDirectory(prefix='preframe-native-')as d:
 d=Path(d);qp,tp=vm.free_port(),vm.free_port();cmd=[str(binary),'-machine','virt-8704-apm,gic-version=2,x-exynos-vlm=on,x-exynos-qnx-g3d00=1,x-exynos-qnx-g3d00-jm-topology=2','-cpu','cortex-a57','-accel','qtest','-m','2048','-display','none','-monitor','none','-nic','none','-serial','null','-S','-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(d/'qtest.log'),'-d','guest_errors','-D',str(d/'native.log')]
 try:
  with(d/'stderr').open('wb')as f:p=subprocess.Popen(cmd,stdout=f,stderr=f)
  for _ in range(200):
   if p.poll()is not None:raise RuntimeError('native early exit')
   try:q=vm.QMP(qp);break
   except OSError:time.sleep(.025)
  qt=QTest(tp);q.execute('cont');G=0x19300000;GPU=0x10010000;PA=0x90010000
  def put(va,data):qt.put(PA+va-GPU,data)
  qt.put(0x90000000,struct.pack('<Q',0x90001003));qt.put(0x90001000,struct.pack('<Q',0x80000441))
  for off,v in [(0x2400,0x90000000),(0x2404,0),(0x2408,0x88888888),(0x240c,0x4c88),(0x2430,6),(0x2434,0),(0x2418,1)]:qt.write(G+off,v)
  job=bytearray(64);struct.pack_into('<I',job,16,1|(9<<1)|(1<<16));struct.pack_into('<Q',job,40,(GPU+0x1000)|1);fb=bytearray(256);struct.pack_into('<I',fb,0,8);struct.pack_into('<Q',fb,24,GPU+0x2000);struct.pack_into('<I',fb,32,0xf000f);struct.pack_into('<I',fb,40,0xf000f);struct.pack_into('<II',fb,44,0x01001000,0x10000);struct.pack_into('<II',fb,128,0x4000000,0x88888d81);draw=bytearray(128);struct.pack_into('<I',draw,0,3);struct.pack_into('<Q',draw,56,GPU+0x3000);state=bytearray(80);struct.pack_into('<Q',state,0,GPU+0x5000);shader=bytes(range(256))
  put(GPU,job);put(GPU+0x1000,fb);put(GPU+0x2080,draw);put(GPU+0x3000,state);put(GPU+0x5000,shader);put(GPU+0x8000,b'\xa5'*4096)
  qt.write(G+0x1840,GPU);qt.write(G+0x1844,0);qt.write(G+0x1858,0);qt.write(G+0x1860,1);record['job_status']=qt.read(G+0x1824);record['canary_unchanged']=qt.get(PA+0x8000,4096)==b'\xa5'*4096;time.sleep(.02)
  record['log']=(d/'native.log').read_text(errors='replace');record['captured_draw']='object=preframe-draw'in record['log'];record['captured_shader']='object=shader'in record['log'];record['exit']=0 if record['job_status']==0x40 and record['canary_unchanged']and record['captured_draw']and record['captured_shader']else 1
 except Exception as e:record.update(exit=1,error=repr(e))
 finally:
  if qt:qt.close()
  if q:
   try:q.execute('quit')
   except Exception:pass
   q.close()
  if p:record['qemu_exit']=p.wait(timeout=5)
  record['stdout']='';record['stderr']=(d/'stderr').read_text(errors='replace')
(r/'.local/mac-port/transaction'/('qnx-preframe-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
