import hashlib,importlib.util,io,json,sys,tempfile
from pathlib import Path
p=Path(sys.argv[1])if len(sys.argv)>1 else Path(__file__).resolve().parents[1]/'src/hvf_bench/guest/navigation-log.py'
try:
 s=importlib.util.spec_from_file_location('navigation_log',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
 with tempfile.TemporaryDirectory()as d:
  f=Path(d)/'engine.log';data=bytes(range(256))*41;m.collect(str(f),io.BytesIO(data),4096);a=f.read_bytes();b=f.with_suffix('.log.1').read_bytes();assert len(a)<=4096 and len(b)==4096 and b+a==data[-(len(b)+len(a)):]
 launch=p.with_name('navigation-run.sh').read_text(); launch=launch[launch.rfind('$B chroot'):]; assert launch.index('unset LD_PRELOAD') < launch.index('mkfifo') < launch.rindex('export LD_PRELOAD=$navigation_preload') < launch.index('exec ./MXNavi'); assert 'navigation-launch.log' in launch
 out=dict(stdout_bytes=len(data),retained_bytes=len(a)+len(b),current_bytes=len(a),previous_bytes=len(b),tail_preserved_exact=True,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),exit=0)
except Exception as e:out=dict(error=repr(e),exit=2)
print(json.dumps(out,indent=2));raise SystemExit(out['exit'])
