from pathlib import Path
import importlib.util,json,os,stat,subprocess,sys,tempfile,types
from unittest.mock import patch
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 p=r/'src/hvf_bench/guest/permission-service.py';spec=importlib.util.spec_from_file_location('original_permission',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 stage=(r/'src/hvf_bench/guest/platform-setup.sh').read_text();assert 'PermissionManagement:system'not in stage
 assert stage.index('ready services --pid')<stage.index('/usr/bin/python3 /tmp/permission-service.py')
 assert '"permission-service.py": "permission-service.py"'in(r/'src/hvf_bench/src/hmi_bench/archive.py').read_text()
 subprocess.run(['/bin/sh','-n',r/'src/hvf_bench/guest/platform-setup.sh'],check=True,capture_output=True)
 real_stat=Path.stat
 with tempfile.TemporaryDirectory()as temp:
  root=Path(temp);(root/'dev').mkdir();(root/'data/lge').mkdir(parents=True)
  for name in m.DEVICES:(root/'dev'/name).touch();(root/'dev'/name).chmod(0o660)
  owners={};bad=[False];writes=[]
  def metadata(p,*args,**kwargs):
   s=real_stat(p,*args,**kwargs);uid,gid=owners.get(str(p),(s.st_uid,s.st_gid));mode=s.st_mode;rdev=s.st_rdev
   if p.parent==root/'dev':mode=stat.S_IFCHR|stat.S_IMODE(mode);rdev=os.makedev(1,99 if bad[0]and p.name=='zero'else m.DEVICES[p.name])
   return types.SimpleNamespace(st_mode=mode,st_uid=uid,st_gid=gid,st_rdev=rdev)
  def chown(p,uid,gid):owners[str(p)]=(uid,gid);writes.append((str(p),uid,gid))
  with patch.object(Path,'stat',metadata),patch.object(m.pwd,'getpwnam',return_value=types.SimpleNamespace(pw_uid=2024,pw_gid=100)),patch.object(m.os,'chown',chown):
   bad[0]=True
   try:m.prepare(root)
   except ValueError:pass
   else:raise AssertionError('wrong device accepted')
   assert not writes and all(stat.S_IMODE(real_stat(root/'dev'/n).st_mode)==0o660 for n in m.DEVICES)
   bad[0]=False;m.prepare(root);m.prepare(root)
   assert len(writes)==2 and all((root/x).exists()for x in ['data/lge/permission','data/lge/permission/db'])
   assert not list((root/'data/lge/permission/db').iterdir())
   assert all(stat.S_IMODE(real_stat(root/'dev'/n).st_mode)==0o666 for n in m.DEVICES)
   assert set(owners.values())=={(2024,100)}
 print('ORIGINAL_PERMISSION_STAGE_PASS uid2024=1 genuine_ELF_bound=1 device_identity_guard=1 store_created_empty=1 readiness_checks_owner_and_data=1 mock_duplicate_removed=1 consent_and_activation_unwritten=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
