from pathlib import Path
import base64,hashlib,json,re,struct,time
from hvf_qnx import owner
from native_domain_state import require_running
from native_guest_command import _lines
COMMAND='screencap -p /data/local/tmp/meb-nav-android.png && sha256sum /data/local/tmp/meb-nav-android.png && base64 /data/local/tmp/meb-nav-android.png'
SOURCE='original_android_surfaceflinger_screencap'
LOCATION_INTENT='android.intent.action.MANAGE_PERMISSION_APPS'
LOCATION_GROUP='android.permission-group.LOCATION'
LOCATION_READINESS_COMMAND="dumpsys activity -a processes | grep -q 'mSystemReady=true'"
LOCATION_CAPTURE_COMMAND=LOCATION_READINESS_COMMAND+" && cmd -w window size && cmd -w activity start-activity -W -a "+LOCATION_INTENT+" --es android.intent.extra.PERMISSION_NAME "+LOCATION_GROUP+" && sleep 1 && "+COMMAND

def validate(result,session,pid):
 s=Path(session)
 if result.get('guest')!='android'or result.get('uid')!=2000 or result.get('session')!=str(s)or result.get('qemu_pid')!=pid:raise ValueError('Android frame guest or epoch differs')
 if result.get('command')not in(COMMAND,LOCATION_CAPTURE_COMMAND) or result.get('exit')!=0 or result.get('transport')!='original-native-vlm-vm4'or not result.get('kernel','').startswith('5.4.131-android11'):raise ValueError('Original Android capture command differs')
 capture=s/'exynos-uart.bin'
 if result.get('capture')!=str(capture):raise ValueError('Android UART capture belongs to another domain')
 start,end=result['raw_start'],result['raw_end']
 if type(start)is not int or type(end)is not int or not 0<=start<end or end-start>1<<20:raise ValueError('Android capture receipt span differs')
 with capture.open('rb')as f:f.seek(start);raw=f.read(end-start)
 if hashlib.sha256(raw).hexdigest()!=result['raw_sha256']:raise ValueError('Android UART receipt changed')
 lines=_lines(raw);begins=[i for i,line in enumerate(lines)if re.fullmatch('MEBB_[0-9a-f]{16}',line)]
 if len(begins)!=1:raise ValueError('Android capture begin marker differs')
 first=begins[0];marker='MEBE_'+lines[first][5:]+':0'
 if marker not in lines[first+1:]:raise ValueError('Android capture completion absent')
 last=lines.index(marker,first+1);stdout='\n'.join(lines[first+1:last])
 if stdout!=result['stdout']:raise ValueError('Android capture stdout differs from original receipt')
 if result['command']==LOCATION_CAPTURE_COMMAND and ('Status: ok'not in stdout or 'com.android.permissioncontroller/'not in stdout):raise ValueError('Original location activity launch was not confirmed')
 rows=stdout.splitlines();matches=[(i,re.fullmatch(r'([0-9a-f]{64})  /data/local/tmp/meb-nav-android.png',line))for i,line in enumerate(rows)];matches=[(i,m)for i,m in matches if m]
 if len(matches)!=1:raise ValueError('Original screencap hash missing')
 i,match=matches[0];encoded=''.join(line for line in rows[i+1:]if re.fullmatch('[A-Za-z0-9+/]+={0,2}',line));png=base64.b64decode(encoded,validate=True)
 if hashlib.sha256(png).hexdigest()!=match.group(1)or png[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('Original Android PNG differs')
 width,height=struct.unpack('>II',png[16:24])
 if not 1<=width<=4096 or not 1<=height<=2160:raise ValueError('Android raster dimensions invalid')
 return png,dict(session=str(s),qemu_pid=pid,vmid=4,uid=2000,kernel=result['kernel'],frame_source=SOURCE,sha256=match.group(1),width=width,height=height,raw_capture=str(capture),raw_start=start,raw_end=end,raw_sha256=result['raw_sha256'])

def publish(result):
 s,pid=owner();require_running(s,pid,4);png,proof=validate(result,s,pid);proof['captured_unix']=time.time()
 for name,data in [('native-android-frame.png',png),('native-android-capture.json',json.dumps(result).encode()),('native-android-frame.json',json.dumps(proof).encode())]:
  p=s/name;tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(p)
 return proof

def sample():
 s,pid=owner();require_running(s,pid,4);proof=json.loads((s/'native-android-frame.json').read_bytes());result=json.loads((s/'native-android-capture.json').read_bytes());png,current=validate(result,s,pid)
 for key,value in current.items():
  if proof.get(key)!=value:raise ValueError('Android frame proof differs: '+key)
 if (s/'native-android-frame.png').read_bytes()!=png:raise ValueError('Published Android pixels changed')
 age=time.time()-proof['captured_unix']
 if age<0:raise ValueError('Android capture time differs')
 return png,dict(proof,frame_age_seconds=age)
