import hashlib,json,mmap,os,runpy,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/emu'))
from macos_process import executable

def observe(session):
 session=Path(session).resolve(strict=True)
 runtime=json.loads((session/'native-runtime-state.json').read_bytes())
 pid=runtime['qemu_pid'];carrier=ROOT/'runtime/darwin/qemu-system-aarch64'
 pin=next(x for x in json.loads((ROOT/'runtime/darwin/build.json').read_bytes())['binaries'] if Path(x['path']).name==carrier.name)
 if not runtime['running'] or executable(pid)!=carrier or hashlib.sha256(carrier.read_bytes()).hexdigest()!=pin['sha256']:raise ValueError('Different or ended native owner')
 code=runpy.run_path(str(ROOT/'src/runner/qnx-bootmarkers.py'));code['contract']()
 banks=[('mach-virt.ram.temporary-ram',0x40000000,0x90000000,0xa0000000),('exynos-vm2-factory-ram.temporary-ram',0x880000000,0x880000000,0x8ce000000)]
 hits={};started=time.monotonic()
 for name,base,low,high in banks:
  with (session/name).open('rb') as file,mmap.mmap(file.fileno(),0,access=mmap.ACCESS_READ)as memory:
   if high-base>len(memory):raise ValueError('Short native QNX shared bank')
   for physical in range(low,high,code['CHUNK']):
    end=min(high,physical+code['CHUNK']);raw=memory[physical-base:end-base]
    for hit in code['markers'](raw,physical):
     a=int(hit['physical'],16)-base;page=a&~4095;before=memory[page:page+4096]
     found=code['record'](before,a-page)
     if found is None or found[1]-code['INITIAL_ID']!=hit['sequence'] or before!=memory[page:page+4096]:continue
     hit['record_page_sha256']=hashlib.sha256(before).hexdigest();hits[hit['physical']]=hit
 after=json.loads((session/'native-runtime-state.json').read_bytes())
 if not after['running'] or after['qemu_pid']!=pid:raise ValueError('Owner ended during observation')
 result=dict(status='observed_original_marker_records'if hits else'not_observed',source_sha256=code['BOOTTIME_SHA'],qemu_pid=pid,captured_monotonic=time.monotonic(),read_seconds=time.monotonic()-started,records=sorted(hits.values(),key=lambda x:x['sequence']),scope='Live owned shared RAM read only; stable marker pages and original writer validated; launch markers do not prove exec success')
 (session/'qnx-live-bootmarkers.json').write_text(json.dumps(result,indent=2)+'\n');return result

if __name__=='__main__':
 session=Path(json.loads((ROOT/'var/current-center.json').read_bytes())['session']);j=observe(session);print(json.dumps({k:v for k,v in j.items()if k!='records'},indent=2));print('\n'.join(str(x['sequence'])+' '+x['marker']for x in j['records']))
