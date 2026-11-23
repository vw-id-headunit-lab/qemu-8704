
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path
from original_8704_vm import QMP,ROOT,free_port
from ufs_platform import QTest


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve()
 if not out.is_relative_to(ROOT/'var')or out.exists():raise ValueError('Require fresh task scratch output')
 out.mkdir();exe=ROOT/'runtime/qemu-system-aarch64.exe';qp,tp=free_port(),free_port()
 cmd=[str(exe),'-L',str(exe.parent/'pc-bios'),'-machine','virt-8704-apm,secure=on,virtualization=on,nvdimm=on,gic-version=2,x-exynos-vlm=on,x-exynos-sysmmu-discovery=1',
      '-cpu','cortex-a72','-accel','tcg,thread=single','-smp','1','-m','3072M,slots=3,maxmem=64G','-display','none','-serial','none','-monitor','none','-nic','none','-S',
      '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(out/'qtest.log')]
 for i,base in enumerate((0xa00000000,0xbf0000000,0xa80000000)):
  cmd+=['-object',f'memory-backend-ram,id=root-fixture-{i},size=128M','-device',f'nvdimm,memdev=root-fixture-{i},addr={base:#x}']
 env=dict(os.environ,PATH=str(ROOT/'.local/deps/bin')+os.pathsep+os.environ.get('PATH',''))
 result={'argv':cmd,'carrier':str(exe),'carrier_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'scope':'Real emulated register operations with synthetic real-RAM spans; no OEM code execution, DMA translation or full-boot claim'}
 q=qt=None
 with(out/'qemu.log').open('wb')as log:
  proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  try:
   until=time.monotonic()+12
   while time.monotonic()<until and proc.poll()is None:
    try:q=QMP(qp);qt=QTest(tp);break
    except OSError:time.sleep(.1)
   if qt is None:raise RuntimeError((out/'qemu.log').read_bytes().decode('utf-8','backslashreplace'))
   cases=[('low-existing',0xa0004000,True),('vm4-first-high',0xa00004000,True),('vm4-observed-high',0xbf0f8c000,True),
          ('vm4-unbacked-high',0xa10000000,False),('vm3-backed-foreign',0xa80004000,False),('vm2-foreign',0x880004000,False),
          ('misaligned-root',0xbf0f8d000,False),('outside-owner-end',0xc00000000,False)]
   rows=[];register=0x1aa8400c
   for name,address,want in cases:
    qt.write(register,0);qt.write(register,address>>12);got=qt.read(register);accepted=got==address>>12
    rows.append({'case':name,'physical_root':hex(address),'input_ppn':hex(address>>12),'readback_ppn':hex(got),'accepted':accepted,'goal_accept':want})
   result['cases']=rows;result['goal_met']=all(x['accepted']==x['goal_accept']for x in rows)
   result['all_negative_controls_rejected']=all(not x['accepted']for x in rows if not x['goal_accept'])
   result['qtest_transcript']=qt.transcript;qt.close();qt=None
   try:q.execute('quit')
   except(OSError,EOFError):pass
   proc.wait(timeout=8);result['qemu_exit_status']=proc.returncode
  finally:
   if qt:qt.close()
   if q:q.close()
   if proc.poll()is None:proc.terminate();proc.wait(timeout=8)
 (out/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in result.items()if k not in ['argv','qtest_transcript']}));return 0 if result['goal_met']else 3

if __name__=='__main__':raise SystemExit(main())
