from pathlib import Path
import sys,json,struct,subprocess,tempfile,time,hashlib
r=Path.cwd();sys.path.insert(0,str(r/'src/emu'));import original_8704_vm as vm;from ufs_platform import QTest
binary=Path(sys.argv[1]).resolve();label=sys.argv[2];q=qt=p=None;record=dict(binary=str(binary),sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),label=label,input='Original 64B preframe TEXC/BLEND program, nonuniform R8/RGBA8 padded linear source -> linear AFBC12 raw subblocks; Fuchsia display-test independent raw byte oracle, padded edges, rejected shader/YTR/tiled/alias/unmapped preserve outputs')
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
  program=bytes.fromhex('290000b043806680033307c01c08c648210005017c58cb80936567020052c458440086410f000000000030e0017301c04e0830cc6b18cb8083a5020004004802');rsd=bytearray(bytes.fromhex('000f010001000000010001000000000000221800000000000000000000000000ffff000700000000ffff0400ffff040080080000000000000000000000000000000a0000222112f01900000000d00e01'));struct.pack_into('<Q',rsd,0,GPU+0x5000)
  checks=[];record['checks']=checks
  def check(name,value,expected):
   checks.append(dict(name=name,actual=value,expected=expected));assert value==expected,name
  def submit(job):
   put(GPU,job);qt.write(G+0x1004,0x10001);qt.write(G+0x1840,GPU);qt.write(G+0x1844,0);qt.write(G+0x1858,0);qt.write(G+0x1860,1);return qt.read(G+0x1824)
  for bpp,width,height in [(1,227,16),(1,31,33),(4,80,40),(4,31,17)]:
   wh=(width-1)|((height-1)<<16);tx=(width+15)//16;ty=(height+15)//16;stride=tx if ty>1 else 0;header_bytes=tx*ty*16;pitch=width*bpp+12;output=GPU+0x20000;body=output+((header_bytes+63)&~63)+64;payload_bytes=tx*ty*256*bpp
   def pixel(x,y):return bytes(((x*7+y*13+c*37)%251)+1 for c in range(bpp))
   source=b''.join(b''.join(pixel(x,y)for x in range(width))+b'\xa5'*12 for y in range(height))
   job=bytearray(64);struct.pack_into('<I',job,16,1|(9<<1)|(1<<16));struct.pack_into('<I',job,36,((height-1)//16)<<16|((width-1)//16));struct.pack_into('<Q',job,40,(GPU+0x1000)|1)
   fb=bytearray(192);struct.pack_into('<I',fb,0,8);struct.pack_into('<QQ',fb,16,GPU+0x6000,GPU+0x2000);struct.pack_into('<I',fb,32,wh);struct.pack_into('<III',fb,40,wh,0x01001000,0x10000);struct.pack_into('<II',fb,128,0x4000000,0x86888c99 if bpp==4 else 0x88888c81);struct.pack_into('<Q',fb,144,output);struct.pack_into('<IIQ',fb,152,stride,0x100000,body)
   draw=bytearray(128);struct.pack_into('<I',draw,0,3);struct.pack_into('<Q',draw,16,0xdead)
   for off,address in [(32,GPU+0x7000),(40,GPU+0x8000),(56,GPU+0x3000),(96,GPU+0xa000),(112,GPU+0xb000)]:struct.pack_into('<Q',draw,off,address)
   texture=struct.pack('<IIIIQII',0x2ec00042 if bpp==4 else 0x28c04042,wh,0x2688,0,GPU+0x9000,0,wh);surface=struct.pack('<QII',GPU+0xc000,pitch,len(source));viewport=struct.pack('<6fII',0,0,width,height,0,1,0,wh)
   for va,data in [(GPU+0x1000,fb),(GPU+0x2080,draw),(GPU+0x3000,rsd),(GPU+0x5000,program),(GPU+0x6000,b'\0'*16),(GPU+0x7000,texture),(GPU+0x8000,struct.pack('<I',0x50099901)+b'\0'*28),(GPU+0x9000,surface),(GPU+0xa000,viewport),(GPU+0xb000,b'\0'*32),(GPU+0xc000,source),(output,b'\xa5'*(body-output)),(body,b'\x5a'*(payload_bytes+64))]:put(va,data)
   check(f'R{bpp*8} {width}x{height} nonuniform completed',submit(job),1)
   expected_headers=b''.join(struct.pack('<I',body-output+tile*256*bpp)+bytes.fromhex('411004'*4)for tile in range(tx*ty))
   inverse=[(1,1),(0,1),(0,0),(1,0),(2,0),(3,0),(3,1),(2,1),(2,2),(3,2),(3,3),(2,3),(1,3),(0,3),(0,2),(1,2)]
   reference=bytearray()
   for tile_y in range(ty):
    for tile_x in range(tx):
     for sub_x,sub_y in inverse:
      for local_y in range(4):
       for local_x in range(4):
        x=tile_x*16+sub_x*4+local_x;y=tile_y*16+sub_y*4+local_y;reference.extend(pixel(x,y)if x<width and y<height else b'\0'*bpp)
   check('raw header sizes and absolute relative pointers',qt.get(PA+output-GPU,header_bytes).hex(),expected_headers.hex())
   check('all raw body bytes incl padding match Fuchsia oracle',hashlib.sha256(qt.get(PA+body-GPU,payload_bytes)).hexdigest(),hashlib.sha256(reference).hexdigest())
   check('header/body gap retained',qt.get(PA+output-GPU+header_bytes,body-output-header_bytes).hex(),(b'\xa5'*(body-output-header_bytes)).hex());check('after-body guard retained',qt.get(PA+body-GPU+payload_bytes,64).hex(),(b'\x5a'*64).hex())
   check('linear source and row padding retained',hashlib.sha256(qt.get(PA+0xc000,len(source))).hexdigest(),hashlib.sha256(source).hexdigest())
   guard=bytes(i%251 for i in range(payload_bytes))
   def guarded_reject(name):
    put(output,b'\xa5'*header_bytes);put(body,guard);check(name+' rejected',submit(job)!=1,True);check(name+' header preserved',qt.get(PA+output-GPU,header_bytes).hex(),(b'\xa5'*header_bytes).hex());check(name+' body preserved',hashlib.sha256(qt.get(PA+body-GPU,payload_bytes)).hexdigest(),hashlib.sha256(guard).hexdigest())
   if bpp==4:
    put(GPU+0x1000+156,struct.pack('<I',0x120000));guarded_reject('unestablished YTR encoding');put(GPU+0x1000+156,struct.pack('<I',0x100000))
   put(GPU+0x5000,bytes([program[0]^1])+program[1:]);guarded_reject('shader mutation');put(GPU+0x5000,program)
   put(GPU+0x1000+160,struct.pack('<Q',0x7000000000));guarded_reject('unmapped body');put(GPU+0x1000+160,struct.pack('<Q',body))
   put(GPU+0x9000,struct.pack('<QII',body,pitch,len(source)));guarded_reject('source/body alias');put(GPU+0x9000,surface)
  record['exit']=0;time.sleep(.01);record['log']=(d/'native.log').read_text(errors='replace')
 except Exception as e:record.update(exit=1,error=repr(e))
 finally:
  if qt:qt.close()
  if q:
   try:q.execute('quit')
   except Exception:pass
   q.close()
  if p:record['qemu_exit']=p.wait(timeout=5)
  record['log']=(d/'native.log').read_text(errors='replace')
  record['stdout']='';record['stderr']=(d/'stderr').read_text(errors='replace')
(r/'.local/mac-port/transaction'/('qnx-raw-preframe-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
