from pathlib import Path
import json,sys,subprocess
r=Path(__file__).resolve().parents[1];p=Path(sys.argv[1])if len(sys.argv)>1 else r/'src/hvf_bench/guest/navigation-run.sh'
try:
 s=p.read_text();i=s.index('  exec /bin/bash /lge/app_ro/someip-daemon/run/launch_someipd.sh');a=s.rfind('$B chroot',0,i);part=s[a:i+150];assert '/tmp/navigation-log.py /tmp/someip-daemon.log' in part and '>"$fifo" 2>&1' in part
 q=subprocess.run(['sh','-n',str(p)],capture_output=True,text=True);assert q.returncode==0
 out=dict(scope='Same boundedoriginalstdout collector extended to observed411807391-byte SOME/IP writer; not protocolbypass',source=str(p),argv=q.args,stdout=q.stdout,stderr=q.stderr,exit=0)
except Exception as e:out=dict(error=repr(e),source=str(p),exit=2)
print(json.dumps(out,indent=2));raise SystemExit(out['exit'])
