from pathlib import Path
import hashlib,json,os,pwd,stat,subprocess,time,urllib.request,urllib.error
IDENTITY='a0153c85cf764e86a5ec8761ccc84b262233354a2c8075fa161603baa94dde9e'
DEVICES={'null':3,'zero':5,'random':8,'urandom':9}
def prepare(root):
 root=Path(root)
 for name,minor in DEVICES.items():
  p=root/'dev'/name;s=p.stat()
  if not stat.S_ISCHR(s.st_mode)or os.major(s.st_rdev)!=1 or os.minor(s.st_rdev)!=minor:raise ValueError('Unexpected runtime character device: '+str(p))
 for name in DEVICES:os.chmod(str(root/'dev'/name),0o666)
 account=pwd.getpwnam('sint_sec_permission')
 if (account.pw_uid,account.pw_gid)!=(2024,100):raise ValueError('Original permission account mismatch')
 for suffix in ['data/lge/permission','data/lge/permission/db']:
  p=root/suffix
  if p.is_symlink():raise ValueError('Original permission store symlink conflict')
  if not p.exists():p.mkdir();os.chown(str(p),account.pw_uid,account.pw_gid);p.chmod(0o750)
  s=p.stat()
  if not stat.S_ISDIR(s.st_mode)or (s.st_uid,s.st_gid)!=(account.pw_uid,account.pw_gid):raise ValueError('Original permission store ownership conflict')
 return account

def observe(pid):
 proc=Path('/proc')/str(pid)
 if os.readlink(str(proc/'exe'))!='/lge/app_ro/permission/permission':raise ValueError('Original permission process changed')
 status=(proc/'status').read_text()
 if 'Uid:\t2024\t2024\t2024\t2024'not in status:raise ValueError('Original permission process UID mismatch')
 with urllib.request.urlopen('http://[::1]/',timeout=2)as response:registry=json.loads(response.read())
 rows=[r for r in registry['data']if r['name']=='PermissionManagement']
 if len(rows)!=1:raise ValueError('PermissionManagement registry must have one original provider')
 port=rows[0]['port'];inodes=set()
 for fd in (proc/'fd').iterdir():
  try:
   value=os.readlink(str(fd))
   if value.startswith('socket:['):inodes.add(value[8:-1])
  except OSError:pass
 matched=False
 for name in ['tcp','tcp6']:
  for line in (proc/'net'/name).read_text().splitlines()[1:]:
   f=line.split()
   if int(f[1].rsplit(':',1)[1],16)==port and f[3]=='0A'and f[9]in inodes:matched=True
 if not matched:raise ValueError('Original permission HTTP listener ownership mismatch')
 with urllib.request.urlopen('http://[::1]:%d/PermissionManagement/permissions/'%port,timeout=2)as response:data=json.loads(response.read())
 if data.get('status')!='ok'or not {'hmi','navi'}.issubset({x['name']for x in data['data']}):raise ValueError('Original permission data plane not ready')
 return dict(pid=pid,uid=2024,port=port,service_id=rows[0]['id'],applications=sorted(x['name']for x in data['data']))

def ready(pid):
 deadline=time.monotonic()+15;last=''
 while time.monotonic()<deadline:
  try:
   result=observe(pid);print('ORIGINAL_PERMISSION_READY '+json.dumps(result),flush=True);return result
  except (OSError,ValueError,KeyError)as error:last=str(error)
  time.sleep(.1)
 raise RuntimeError('Original permission readiness: '+last)

def start():
 exe=Path('/lge/app_ro/permission/permission')
 if hashlib.sha256(exe.read_bytes()).hexdigest()!=IDENTITY:raise ValueError('Original permission ELF identity mismatch')
 unit=Path('/lib/systemd/system/permission.service').read_text()
 if 'User=sint_sec_permission'not in unit or 'ExecStart=/lge/app_ro/permission/permission --log-level=0'not in unit:raise ValueError('Original permission unit mismatch')
 for proc in Path('/proc').glob('[0-9]*'):
  try:
   if os.readlink(str(proc/'exe'))==str(exe):print('ORIGINAL_PERMISSION_REUSED_PID='+proc.name,flush=True);return ready(int(proc.name))
  except OSError:pass
 account=prepare('/')
 def user():os.setgroups([]);os.setgid(account.pw_gid);os.setuid(account.pw_uid)
 env=dict(os.environ,LD_LIBRARY_PATH='/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib',XDG_RUNTIME_DIR='/early/user/0')
 with Path('/tmp/navigation-permission.log').open('ab')as log:
  process=subprocess.Popen([str(exe),'--log-level=0'],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,preexec_fn=user,start_new_session=True,close_fds=True)
 Path('/tmp/navigation-permission.pid').write_text(str(process.pid)+'\n');print('ORIGINAL_PERMISSION_LAUNCH_PID='+str(process.pid),flush=True)
 time.sleep(.2)
 if process.poll()is not None:raise RuntimeError('Original permission exited: '+str(process.returncode))
 return ready(process.pid)
if __name__=='__main__':start()
