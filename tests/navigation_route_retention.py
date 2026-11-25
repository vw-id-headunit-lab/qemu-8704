from pathlib import Path
import json,os,subprocess,sys,tempfile
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 p=r/'src/hvf_bench/guest/domain-network.sh';source=p.read_text();assert 'NAVIGATION_UPLINK_RECOVERY_FAILED'in source and '/proc/net/route'in source
 assert source.index('if [ -f /navigation-uplink.conf ]')<source.index('$B sleep 2')
 subprocess.run(['/bin/sh','-n',p],check=True,capture_output=True)
 records=[]
 with tempfile.TemporaryDirectory()as temp:
  d=Path(temp);(d/'net/eth0.3').mkdir(parents=True);(d/'net/eth0.3/address').write_text('52:54:00:87:04:01\n');(d/'flow').touch();(d/'profile').touch();route=d/'route';actions=d/'actions';(d/'out').touch()
  bb=d/'busybox';bb.write_text('''#!/bin/sh
case "$1" in
 cat) shift;exec cat "$@";;
 ip) exit 0;;
 awk) shift;exec awk "$@";;
 sh) echo RECOVER >>"$ACTIONS";printf 'Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0.3 00000000 0205570A 0003 0 0 0 00000000\n' >"$ROUTE";exit "${RECOVER_EXIT:-0}";;
 sleep) rm "$ADDRESS";exit 0;;
 *) exit 91;;
esac
''');bb.chmod(0o755)
  s=source.replace('B=/bin/busybox','B='+str(bb)).replace('/proc/sys/net/ipv6/auto_flowlabels',str(d/'flow')).replace('/sys/class/net',str(d/'net')).replace('/navigation-uplink.conf',str(d/'profile')).replace('/proc/net/route',str(route));script=d/'domain';script.write_text(s)
  env=dict(os.environ,ACTIONS=str(actions),ROUTE=str(route),ADDRESS=str(d/'net/eth0.3/address'))
  for label,profile,default,expected in [('missing',True,False,True),('existing',True,True,False),('offline',False,False,False)]:
   actions.unlink(missing_ok=True);(d/'net/eth0.3/address').write_text('52:54:00:87:04:01\n')
   if profile:(d/'profile').touch()
   else:(d/'profile').unlink(missing_ok=True)
   route.write_text('Iface Destination Gateway Flags RefCnt Use Metric Mask\n'+('eth0.3 00000000 0205570A 0003 0 0 0 00000000\n'if default else'eth0.3 0004570A 00000000 0001 0 0 0 00FFFFFF\n'))
   q=subprocess.run(['/bin/sh',script],env=env,capture_output=True,text=True);assert q.returncode==0,(label,q.stdout,q.stderr);assert actions.exists()==expected;records.append(dict(case=label,recovery_observed=actions.exists(),exit=q.returncode))
 print(json.dumps(dict(status='NAVIGATION_ROUTE_RETENTION_PASS',records=records,policy='recover only declared uplink with missing default; preserve existing route and offline configurations')));raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
