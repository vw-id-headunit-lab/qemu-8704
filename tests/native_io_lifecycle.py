from pathlib import Path
import ast,json,struct,subprocess,sys,tempfile,threading,time
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'src/emu'));import original_8704_vm as vm
from ufs_platform import QTest
source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'src/emu/vlm_offline_live_darwin.py';report={'source':str(source),'input':'Actual virtio block write gets one ENOSPC, VM enters io-error; persistent observer retains original process until explicit cont retries same write','scope':'Actual QEMU block/lifecycle fixture, not original firmware boot acceptance'};p=q=qt=None
try:
 tree=ast.parse(source.read_bytes());probe=next(x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name=='probe');node=next(x for x in ast.walk(probe)if isinstance(x,ast.If)and ast.unparse(x.test)=="not status_before_stop.get('running', False)")
 code='def observe(read,deadline,lifetime):\n watch=panic_watch=abox_watch=None;stop_reason=None;states=[]\n while time.monotonic()<deadline:\n  time.sleep(.02)\n  status_before_stop=read();states.append(status_before_stop[\'status\'])\n'+''.join('  '+line+'\n'for line in ast.unparse(node).splitlines())+'  if status_before_stop.get(\'running\'):return dict(result=\'resumed\',states=states)\n return dict(result=\'ended\',reason=stop_reason,states=states)\n';env={'time':time};exec(code,env)
 with tempfile.TemporaryDirectory(prefix='native-io-')as tmp:
  d=Path(tmp);(d/'disk').write_bytes(bytes(1024*1024));(d/'errors').write_text('[inject-error]\nevent = "write_aio"\nerrno = "28"\nonce = "on"\n');qp,control_port,tp=vm.free_port(),vm.free_port(),vm.free_port()
  cmd=[str(r/'runtime/darwin/qemu-system-aarch64'),'-machine','virt','-global','virtio-mmio.force-legacy=false','-accel','qtest','-m','128M','-display','none','-serial','null','-nic','none','-S','-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qmp',f'tcp:127.0.0.1:{control_port},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(d/'qtest.log'),'-drive',f'file=blkdebug:{d}/errors:{d}/disk,format=raw,if=none,id=disk,werror=stop','-device','virtio-blk-device,drive=disk'];out=(d/'stdout').open('wb');err=(d/'stderr').open('wb');p=subprocess.Popen(cmd,stdout=out,stderr=err)
  try:
   for _ in range(100):
    try:q=vm.QMP(qp);break
    except OSError:
     if p.poll()is not None:raise
     time.sleep(.05)
   assert q;qt=QTest(tp);q.execute('cont');b=next(a for a in range(0xa000000,0xa004000,0x200)if qt.read(a+8)==2);assert qt.read(b+4)==2
   for off,val in [(0x70,0),(0x70,1),(0x70,3),(0x28,0),(0x20,0),(0x28,1),(0x20,1),(0x70,11),(0x30,0),(0x38,8)]:qt.write(b+off,val)
   desc,avail,used,header,data,status=0x40010000,0x40011000,0x40012000,0x40013000,0x40014000,0x40015000
   qt.put(header,struct.pack('<IIQ',1,0,0));qt.put(data,b'X'*512);qt.put(status,b'\xff');qt.put(desc,struct.pack('<QIHH',header,16,1,1)+struct.pack('<QIHH',data,512,1,2)+struct.pack('<QIHH',status,1,2,0));qt.put(avail,struct.pack('<HHH',0,1,0));qt.put(used,bytes(68))
   for off,a in [(0x80,desc),(0x90,avail),(0xa0,used)]:qt.write(b+off,a);qt.write(b+off+4,0)
   for off,val in [(0x44,1),(0x70,15),(0x50,0)]:qt.write(b+off,val)
   for _ in range(100):
    before=q.execute('query-status')
    if before['status']=='io-error':break
    time.sleep(.02)
   assert before=={'status':'io-error','running':False};blocks=q.execute('query-block');assert next(x for x in blocks if x['device']=='disk')['io-status']=='nospace'
   def read():return q.execute('query-status')
   def resume():
    time.sleep(.3);control=vm.QMP(control_port);control.execute('cont');control.close()
   thread=threading.Thread(target=resume);thread.start();observed=env['observe'](read,time.monotonic()+3,{'profile':'until-stop-v1'});thread.join()
   for _ in range(100):
    if qt.get(status,1)==b'\x00':break
    time.sleep(.01)
   disk=(d/'disk').read_bytes()[:512];report.update(before=before,block_io_status='nospace',observer=observed,same_qemu_alive=p.poll()is None,after=q.execute('query-status'),virtio_status=qt.get(status,1).hex(),used_index=struct.unpack('<H',qt.get(used+2,2))[0],retry_disk_matches=disk==b'X'*512)
   assert observed['result']=='resumed'and 'io-error'in observed['states']and p.poll()is None and report['virtio_status']=='00'and report['used_index']==1 and report['retry_disk_matches']
  finally:
   if q:
    q.execute('quit');q.close();q=None
   if qt:qt.close();qt=None
   if p.poll()is None:p.terminate()
   p.wait(timeout=5);out.close();err.close();report['qemu_stderr']=(d/'stderr').read_text();report['qemu_exit']=p.returncode
 report['exit']=0
except Exception as error:report.update(exit=2,error=repr(error))
finally:
 if p is not None and p.poll()is None:p.kill();p.wait()
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
