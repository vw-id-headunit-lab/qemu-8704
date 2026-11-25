from pathlib import Path
import configparser,hashlib,json,os,pwd,shlex,subprocess,sys,time,urllib.request
IDENTITY='7927221500f4b73847cab12831cfa6256b09e211c45c5b357b0b7fe63ee0e70d'
CAPSH_IDENTITY='af0f20672db966f894bbf8e48927debc32ba61ebbfdd7c239fb80ce1d4bfa325'
EXECUTABLE='/lge/app_ro/onlinecore/onl-cn-onlineservice'
CAPABILITIES=('CAP_DAC_OVERRIDE','CAP_DAC_READ_SEARCH','CAP_FOWNER')

def unit_command(text,uid,gid):
 parser=configparser.ConfigParser(interpolation=None);parser.read_string(text);unit=parser['Service']
 if unit.get('User')!='sint_sec_onl-cn-onlineservice'or (uid,gid)!=(2000,100):raise ValueError('Original online account differs')
 if shlex.split(unit.get('ExecStart',''))!=[EXECUTABLE,'--log-level=0']:raise ValueError('Original online command differs')
 if tuple(unit.get('AmbientCapabilities','').split())!=CAPABILITIES:raise ValueError('Original online capabilities differ')
 caps=','.join(x.lower()for x in CAPABILITIES)
 return ['/usr/sbin/capsh','--groups=','--gid='+str(gid),'--keep=1','--uid='+str(uid),'--caps='+caps+'=eip','--addamb='+caps,'--shell=/bin/sh','--','-c','exec '+EXECUTABLE+' --log-level=0']

def observe(pid):
 proc=Path('/proc')/str(pid)
 if os.readlink(str(proc/'exe'))!=EXECUTABLE:raise ValueError('Original online executable owner differs')
 status={line.split(':',1)[0]:line.split(':',1)[1].strip()for line in(proc/'status').read_text().splitlines()if ':'in line}
 if status['Uid'].split()!=['2000']*4 or status['Gid'].split()!=['100']*4:raise ValueError('Original online process account differs')
 if any(int(status[field],16)!=14 for field in ['CapInh','CapPrm','CapEff','CapAmb']):raise ValueError('Original online unit capability set differs')
 with urllib.request.urlopen('http://[::1]/?$limit=200',timeout=2)as response:registry=json.loads(response.read())
 rows=[x for x in registry['data']if x['name']=='onlineservicesetting']
 if len(rows)!=1 or rows[0]['port']!=30001:raise ValueError('Original online registry ownership is ambiguous')
 inodes=set()
 for fd in(proc/'fd').iterdir():
  try:
   value=os.readlink(str(fd))
   if value.startswith('socket:['):inodes.add(value[8:-1])
  except OSError:pass
 matched=False
 for kind in ['tcp','tcp6']:
  for line in(proc/'net'/kind).read_text().splitlines()[1:]:
   fields=line.split()
   if int(fields[1].rsplit(':',1)[1],16)==30001 and fields[3]=='0A'and fields[9]in inodes:matched=True
 if not matched:raise ValueError('Original online HTTP listener owner differs')
 resources={}
 for resource in ['onlineServicePrivacyModes','privacygroups']:
  with urllib.request.urlopen('http://[::1]:30001/onlineservicesetting/'+resource+'/',timeout=2)as response:data=json.loads(response.read())
  if data.get('status')!='ok'or not data.get('data'):raise ValueError('Original online resource is not ready: '+resource)
  resources[resource]=data['data']
 if not all(isinstance(x.get('onlineServicePrivacyModeStatus'),bool)for x in resources['onlineServicePrivacyModes']):raise ValueError('Original online mode schema differs')
 if not all(isinstance(x.get('voicecollection'),bool)and isinstance(x.get('voiceservice'),bool)for x in resources['privacygroups']):raise ValueError('Original online groups schema differs')
 return dict(pid=pid,uid=2000,gid=100,capabilities=list(CAPABILITIES),port=30001,service_id=rows[0]['id'],resources=resources)

def ready(pid,timeout=180):
 deadline=time.monotonic()+timeout;last=''
 while time.monotonic()<deadline:
  try:
   result=observe(pid);print('ORIGINAL_ONLINE_READY '+json.dumps(result),flush=True);return result
  except (OSError,ValueError,KeyError)as error:last=str(error)
  if not Path('/proc',str(pid)).exists():raise RuntimeError('Original online process exited: '+last)
  time.sleep(.2)
 raise RuntimeError('Original online data readiness: '+last)

def start():
 if hashlib.sha256(Path(EXECUTABLE).read_bytes()).hexdigest()!=IDENTITY:raise ValueError('Original online ELF differs')
 if hashlib.sha256(Path('/usr/sbin/capsh').read_bytes()).hexdigest()!=CAPSH_IDENTITY:raise ValueError('Original capability launcher differs')
 account=pwd.getpwnam('sint_sec_onl-cn-onlineservice');argv=unit_command(Path('/lib/systemd/system/onl-cn-onlineservice.service').read_text(),account.pw_uid,account.pw_gid)
 for proc in Path('/proc').glob('[0-9]*'):
  try:
   if os.readlink(str(proc/'exe'))==EXECUTABLE:print('ORIGINAL_ONLINE_REUSED_PID='+proc.name,flush=True);return ready(int(proc.name))
  except OSError:pass
 env=dict(os.environ,LD_LIBRARY_PATH='/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib')
 logger=subprocess.Popen([sys.executable,'/tmp/navigation-log.py','/tmp/online-service.log'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
 try:process=subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=logger.stdin,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
 finally:logger.stdin.close()
 Path('/tmp/online-service.pid').write_text(str(process.pid)+'\n');print('ORIGINAL_ONLINE_LAUNCH_PID='+str(process.pid),flush=True)
 time.sleep(.1)
 if process.poll()is not None:raise RuntimeError('Original online unit execution failed: '+str(process.returncode))
 return ready(process.pid)
if __name__=='__main__':start()
