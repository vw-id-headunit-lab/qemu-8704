from pathlib import Path
import io,json,struct,sys,zipfile,hashlib
root=Path(sys.argv[1]);label=sys.argv[2];record={'label':label,'input':'Canonical adapter NativeTouch.pointer first null guard after actual listener removal; original touch-app boot dependency','scope':'Exact source/class binding; live JVM hot-unplug and CUA behavior recorded separately'}
def field_guard(raw):
 cp=[None];pos=10;count=struct.unpack_from('>H',raw,8)[0]
 while len(cp)<count:
  tag=raw[pos];pos+=1
  if tag==1:
   n=struct.unpack_from('>H',raw,pos)[0];pos+=2;value=raw[pos:pos+n].decode();pos+=n
  elif tag in (3,4):value=raw[pos:pos+4];pos+=4
  elif tag in (5,6):value=raw[pos:pos+8];pos+=8;cp.append((tag,value));cp.append(None);continue
  elif tag in (7,8,16):value=struct.unpack_from('>H',raw,pos)[0];pos+=2
  elif tag in (9,10,11,12,18):value=struct.unpack_from('>HH',raw,pos);pos+=4
  elif tag==15:value=struct.unpack_from('>BH',raw,pos);pos+=3
  else:raise ValueError('Unsupported class constant '+str(tag))
  cp.append((tag,value))
 def u2():
  nonlocal pos
  value=struct.unpack_from('>H',raw,pos)[0];pos+=2;return value
 pos+=6;interfaces=u2();pos+=2*interfaces
 for group in ['fields','methods']:
  for _ in range(u2()):
   flags,name,desc,nattrs=u2(),u2(),u2(),u2()
   for _ in range(nattrs):
    attr=u2();length=struct.unpack_from('>I',raw,pos)[0];pos+=4;start=pos;pos+=length
    if group=='methods'and cp[name][1]=='pointer'and cp[attr][1]=='Code':
     n=struct.unpack_from('>I',raw,start+4)[0];code=raw[start+8:start+8+n];assert code[:2]==b'\x2a\xb4'and code[4]==0xc7
     ref=struct.unpack_from('>H',code,2)[0];nt=cp[ref][1][1];return cp[cp[nt][1][0]][1]
 raise ValueError('No NativeTouch.pointer code')
try:
 jar=root/'src/hvf_bench/build/bench-platform.jar'
 with zipfile.ZipFile(jar)as z:raw=z.read('bench/platform/BenchPlatform$NativeTouch.class')
 guard=field_guard(raw);source=(root/'src/hvf_bench/platform/BenchPlatform.java').read_text();startup=(root/'src/hvf_bench/guest/native-rsi-setup.sh').read_text();record.update(guard=guard,class_sha256=hashlib.sha256(raw).hexdigest(),jar_sha256=hashlib.sha256(jar.read_bytes()).hexdigest(),native_touch_startup='ORIGINAL_TOUCH_APP_PID'in startup)
 assert guard=='listener','Compiled adapter does not reattach after listener loss'
 assert 'synchronized boolean pointer(String action, int x, int y) {\n            if (listener == null)'in source,'Source/class guard mismatch'
 assert record['native_touch_startup'] and 'e1bb4059e85b0ffc1108f66b0a9e23f5a1ebec590804602ebf00ab67c47256c9'in startup,'Original touch dependency absent'
 record['exit']=0
except Exception as e:record.update(exit=1,error=repr(e))
print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
