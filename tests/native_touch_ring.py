from pathlib import Path
import importlib.util,json,os,struct,sys,tempfile

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'src/emu')]
from native_touch import emit,OFFSET
import native_touch

report=dict(scope='Host input ring using a sparse fixture only; original native delivery checked live separately')
owner=native_touch.owner;require=native_touch.require_running
try:
 with tempfile.TemporaryDirectory(prefix='native-touch-ring-fixture-')as d:
  s=Path(d);manifest=json.loads((ROOT/'runtime/guest/native-touch/manifest.json').read_bytes());(s/'gl-forward-boot.json').write_text(json.dumps(dict(native_touch=manifest,profile='original-hmi-egl-v1')))
  p=s/'exynos-vm3-factory-ram-b0.temporary-ram'
  with p.open('wb')as f:f.truncate(0x80000000)
  fd=os.open(p,os.O_RDWR)
  try:
   os.pwrite(fd,struct.pack('<16I',0x3154434d,1,0,0,0,0,11,0,1,2,3,4,0,0,0,0),OFFSET)
   native_touch.owner=lambda:(s,123);native_touch.require_running=lambda *args:None
   epoch=str(s)+':123';events=[['down',100,200],['move',300,400],['up',300,400]];r=emit(events,epoch)
   assert r['sequence']==3 and struct.unpack('<I',os.pread(fd,4,OFFSET+8))[0]==3
   assert struct.unpack('<12I',os.pread(fd,48,OFFSET+64))==(1,100,200,1,2,300,400,2,3,300,400,3)
   for bad,token in [([['down',0,0]],'WRONG_EPOCH'),([['down',1920,0]],epoch),([['move',0,0]],epoch)]:
    try:emit(bad,token)
    except ValueError:pass
    else:raise AssertionError('Bad gesture or stale epoch admitted')
   def stopped(*args):raise RuntimeError('FIXTURE_NOT_RUN')
   native_touch.require_running=stopped
   try:emit([['down',0,0]],epoch)
   except RuntimeError as error:assert str(error)=='FIXTURE_NOT_RUN'
   else:raise AssertionError('Stopped domain admitted')
   native_touch.require_running=lambda *args:None;os.pwrite(fd,struct.pack('<I',0),OFFSET)
   try:emit([['down',0,0]],epoch)
   except RuntimeError:pass
   else:raise AssertionError('Unready relay admitted')
  finally:os.close(fd)
 report.update(exit=0,stdout='NATIVE_TOUCH_RING_PASS sequences=1,2,3 original_shape=1920x1080 stale_epoch=rejected bad_gesture=rejected stopped_domain=rejected unready=rejected\n',stderr='')
except Exception as error:report.update(exit=2,stdout='',stderr=repr(error))
finally:native_touch.owner=owner;native_touch.require_running=require
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
