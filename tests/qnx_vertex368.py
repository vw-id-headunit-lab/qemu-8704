from pathlib import Path
import sys,json,struct,socket,subprocess,time,tempfile,hashlib,traceback
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/emu'))
import original_8704_vm as vm
from ufs_platform import QTest
binary=Path(sys.argv[1]).resolve();label=sys.argv[2]
PROGRAM=bytes.fromhex('4a000020021ccb8023a000c0c80880102a88837f7a2b062044a100c00810881021883c7f08200620a0654f2c0a60003f0186ff6ff8280200a065070864d9012461863c5f08e00100bc653f2c8a5f000fc989ff7f082806209065070064d90100598e006058200c00ac00000000000000298c001040000b00906507c048279030268a823f844101604500680c022004588b8f0451fe020160a50000000b600100510002007c18cb8093650700000000004e003c5f081ecb80236a02c0984098112e43872ffa018720442100c0880980002043001086058d0084001a0200c0026801810020580007001c8021001f00000c61810412784007209065070400402c680700cf3388189e709165070089010004765d632e03000000000000000000000028000070100c06206c400000800080012300002086458221dd0b1d0400c0023a0180861128400d0090650708270000246182007058300a009065172c8af34440470084c39a19cb80237a020064d90138')
record=dict(label=label,binary=str(binary),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),input='Original 368B QNX vertex program, RG32F XY/UV, nonidentity FAU matrix, four vertices',checks=[])
def check(name,actual,expected):
 record['checks'].append(dict(name=name,actual=actual,expected=expected));assert actual==expected,name
qmp=qt=process=None
with tempfile.TemporaryDirectory(prefix='qnx-v368-')as tmp:
 t=Path(tmp);qp,tp=vm.free_port(),vm.free_port()
 argv=[str(binary),'-machine','virt-8704-apm,gic-version=2,x-exynos-vlm=on,x-exynos-qnx-g3d00=1,x-exynos-qnx-g3d00-jm-topology=2','-cpu','cortex-a57','-accel','qtest','-m','2048','-display','none','-monitor','none','-nic','none','-serial','null','-S','-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(t/'qtest.log')]
 try:
  with(t/'stderr').open('wb')as stream:process=subprocess.Popen(argv,stdout=stream,stderr=stream)
  deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   if process.poll()is not None:raise RuntimeError('Native QEMU early exit '+str(process.returncode))
   try:qmp=vm.QMP(qp);break
   except OSError:time.sleep(.02)
  qt=QTest(tp);qmp.execute('cont')
  G=0x19300000;GPU=0x10010000;PA=0x90010000
  def put(va,data):qt.put(PA+va-GPU,data)
  qt.put(0x90000000,struct.pack('<Q',0x90001003));qt.put(0x90001000,struct.pack('<Q',0x80000441))
  for off,value in [(0x2400,0x90000000),(0x2404,0),(0x2408,0x88888888),(0x240c,0x4c88),(0x2430,6),(0x2434,0),(0x2418,1)]:qt.write(G+off,value)
  head=GPU;rsd=GPU+0x1000;pc=GPU+0x2000;fau=GPU+0x3000;attr=GPU+0x4000;vary=GPU+0x5000;ab=GPU+0x6000;vb=GPU+0x7000;xy=GPU+0x8000;uv=GPU+0x9000;out_uv=GPU+0xa000;out_pos=GPU+0xb000
  job=bytearray(192);struct.pack_into('<I',job,16,1|(5<<1)|(1<<16));struct.pack_into('<II',job,32,3,0x38000000);struct.pack_into('<I',job,64,2)
  for off,value in [(48,fau),(56,rsd),(64,ab),(72,attr),(80,vb),(88,vary)]:struct.pack_into('<Q',job,64+off,value)
  put(head,job);state=bytearray(64);struct.pack_into('<Q',state,0,pc);struct.pack_into('<I',state,12,0x20002);struct.pack_into('<I',state,48,0x86000);put(rsd,state);put(pc,PROGRAM)
  uniforms=[0.0]*32
  for i,v in {0:1,1:1,2:1,12:2,16:3,30:5,13:7,17:11,21:13,31:.5,29:1,26:.5,27:.25,24:2,25:-.5}.items():uniforms[i]=v
  put(fau,struct.pack('<32f',*uniforms));put(attr,struct.pack('<IIII',0x2bc04200,0,0x2bc04201,0));put(vary,struct.pack('<IIII',0x33c00000,0,0x17800001,0));put(ab,struct.pack('<QIIQII',xy|1,8,32,uv|1,8,32));put(vb,struct.pack('<QIIQII',out_uv|1,4,16,out_pos|1,16,64));put(xy,struct.pack('<8f',0,0,1,0,0,1,2,3));put(uv,struct.pack('<8f',0,0,1,1,2,0,.5,.5));put(out_uv,b'\xa5'*16);put(out_pos,b'\xa5'*64)
  def submit():
   qt.write(G+0x18c0,head);qt.write(G+0x18c4,0);qt.write(G+0x18d8,0);qt.write(G+0x18e0,1)
  submit();record['job_status']=qt.read(G+0x18a4);record['actual_uv']=qt.get(PA+out_uv-GPU,16).hex();record['actual_position']=qt.get(PA+out_pos-GPU,64).hex();check('original vertex job completed',record['job_status'],1)
  expected_pos=struct.pack('<16f',5,13,.5,1,7,20,.5,1,8,24,.5,1,18,60,.5,1);expected_uv=struct.pack('<8e',.25,-.5,.75,1.5,1.25,-.5,.5,.5)
  check('transformed XYZW',record['actual_position'],expected_pos.hex());check('transformed UV half',record['actual_uv'],expected_uv.hex())
  qt.write(G+0x1004,0x20002);put(head,job);put(pc,bytes([PROGRAM[0]^1])+PROGRAM[1:]);put(out_uv,b'\xa5'*16);put(out_pos,b'\xa5'*64);submit();check('changed shader rejected',qt.read(G+0x18a4),0x40);check('rejected shader preserves outputs',qt.get(PA+out_pos-GPU,64).hex(),(b'\xa5'*64).hex())
  qt.write(G+0x1004,0x20002);put(head,job);put(pc,PROGRAM);put(vb,struct.pack('<QIIQII',fau|1,4,16,out_pos|1,16,64));saved=qt.get(PA+fau-GPU,128);submit();check('FAU output alias rejected',qt.read(G+0x18a4),0x43);check('FAU alias preserves uniforms',qt.get(PA+fau-GPU,128).hex(),saved.hex());check('FAU alias preserves position',qt.get(PA+out_pos-GPU,64).hex(),(b'\xa5'*64).hex())
  qt.write(G+0x1004,0x20002);put(head,job);put(vb,struct.pack('<QIIQII',out_uv|1,4,16,out_pos|1,16,64));put(ab,struct.pack('<QIIQII',xy|1,8,31,uv|1,8,32));submit();check('short attribute rejected',qt.read(G+0x18a4),0x40);check('short attribute preserves outputs',qt.get(PA+out_pos-GPU,64).hex(),(b'\xa5'*64).hex());record['exit']=0
 except Exception as error:record.update(exit=1,error=repr(error),traceback=traceback.format_exc())
 finally:
  if qt:record['qtest_transcript']=qt.transcript;qt.close()
  if qmp:
   try:qmp.execute('quit')
   except Exception:pass
   qmp.close()
  if process:
   try:record['qemu_exit']=process.wait(timeout=5)
   except subprocess.TimeoutExpired:process.terminate();record['qemu_exit']=process.wait(timeout=5)
  record['stdout']='';record['stderr']=(t/'stderr').read_text(errors='replace')
(ROOT/'.local/mac-port/transaction'/('qnx-vertex368-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({k:v for k,v in record.items()if k not in ['qtest_transcript','traceback']},indent=2));raise SystemExit(record['exit'])
