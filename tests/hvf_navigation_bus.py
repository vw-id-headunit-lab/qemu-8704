from pathlib import Path
import json,os,re,subprocess,sys,tempfile
source=Path(sys.argv[1]);label=sys.argv[2];text=source.read_text();record={'label':label,'input':'Warm launch with an existing stale bus file and delayed fresh dbus-launch; execute both actual shell launch bodies with fixture applications','scope':'Shell synchronization test only; native daemon/GNSS/pixels validated separately'}
try:
 blocks=re.findall(r'\$B chroot "\$ROOT" /bin/sh -c \'(.*?)\' </dev/null',text,re.S);alm=next(x for x in blocks if 'exec ./alm' in x);engine=next(x for x in blocks if 'exec ./MXNavi' in x)
 with tempfile.TemporaryDirectory(prefix='navigation-bus-')as tmp:
  root=Path(tmp);(root/'tmp').mkdir();(root/'bin').mkdir();(root/'alm').mkdir();(root/'engine').mkdir();bus=root/'tmp/navigation-dbus.env';bus.write_text('DBUS_SESSION_BUS_ADDRESS=FIXTURE_STALE_BUS\nDBUS_SESSION_BUS_PID=111\n')
  programs={root/'bin/dbus-launch':'#!/bin/sh\nsleep .2\nprintf "DBUS_SESSION_BUS_ADDRESS=FIXTURE_FRESH_BUS;\\nDBUS_SESSION_BUS_PID=222;\\n"\n',root/'alm/alm':'#!/bin/sh\nprintf "ALM_BUS=%s\\n" "$DBUS_SESSION_BUS_ADDRESS"\n',root/'engine/MXNavi':'#!/bin/sh\nprintf "ENGINE_BUS=%s\\n" "$DBUS_SESSION_BUS_ADDRESS"\n'}
  for p,s in programs.items():p.write_text(s);p.chmod(0o755)
  env=dict(os.environ,PATH=str(root/'bin')+':'+os.environ['PATH']);env.pop('DBUS_SESSION_BUS_ADDRESS',None);env.pop('DBUS_SESSION_BUS_PID',None)
  def mapped(s):return s.replace('/lge/navi/naviengine/package/MXNavi',str(root/'engine')).replace('/lge/app_ro/navi/navi-al',str(root/'alm')).replace('/tmp/navigation-dbus.env',str(bus))
  clean=next((x for x in text.splitlines()if x.startswith('$B rm -f "$ROOT/tmp/navigation-dbus.env"')),None)
  if clean:
   q=subprocess.run(['/bin/sh','-c',clean.replace('$B ','').replace('$ROOT',str(root))],env=env,capture_output=True,text=True);assert q.returncode==0
  a=subprocess.Popen(['/bin/sh','-c',mapped(alm)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  q=subprocess.run(['/bin/sh','-c',mapped(engine)],env=env,capture_output=True,text=True,timeout=8);out,err=a.communicate(timeout=8)
  record.update(alm={'stdout':out,'stderr':err,'exit':a.returncode},engine={'stdout':q.stdout,'stderr':q.stderr,'exit':q.returncode},fresh_file=bus.read_text())
  assert a.returncode==0 and q.returncode==0 and 'ALM_BUS=FIXTURE_FRESH_BUS' in out and 'ENGINE_BUS=FIXTURE_FRESH_BUS' in q.stdout,'Engine inherited stale bus'
 record['exit']=0
except Exception as e:record.update(exit=1,error=repr(e))
print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
