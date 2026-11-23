import argparse,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BENCH=ROOT/'src/hvf_bench'
sys.path.insert(0,str(BENCH/'src'))
from hmi_bench.config import Config
CONFIG=ROOT/'config/hvf-8704.json'
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['start','serve','status','stop']);p.add_argument('--worker',action='store_true');p.add_argument('--session',type=Path);p.add_argument('--language',choices=['zh_CN','en_US'],default='zh_CN');a=p.parse_args();c=Config.load(CONFIG);root=c['output'];current=root/'current.json'
 if a.worker:
  paths=dict(c.paths);paths['output']=a.session;os.environ['MEB_HVF_UNTIL_STOP']='1'
  from hmi_bench.launcher import run
  return run(Config(c.source,paths),'hmi',3600,open_viewer=False,debug_shell=True,language=a.language)
 previous=json.loads(current.read_bytes())if current.exists()else None
 record=Path(previous['session'])/'session.json'if previous else root/'session.json'
 state=json.loads(record.read_bytes())if record.exists()else {'state':'not_started'}
 if a.action=='status':
  keys=['state','language','qemu_pid','qemu_exit_status','hmi_exit_status','viewer_url','error','milestone_wall_seconds']
  info={k:state[k]for k in keys if k in state};info.update(route='8704-original-HMI-generic-kernel-HVF',firmware='8704',session=str(record.parent),missing=[k for k in c.required('hmi')if not c[k].exists()]);print(json.dumps(info,indent=2));return 0
 if a.action=='stop':
  ac=ROOT/'var/hvf/android-current.json'
  if ac.exists():
   aj=json.loads(ac.read_bytes())
   try:
    from hvf_guest_command import session as guest_session
    _,aj,_=guest_session('android')
    from hmi_bench.launcher import qmp
    qmp(aj['qmp_port'],'quit')
   except (ProcessLookupError,ConnectionError,ValueError,subprocess.CalledProcessError):pass
  dc=ROOT/'var/hvf/dashboard.json'
  if dc.exists():
   dj=json.loads(dc.read_bytes())
   try:
    dcproc=subprocess.run(['ps','-p',str(dj['pid']),'-o','command='],capture_output=True,text=True).stdout
    if str(ROOT/'tools/hvf_dashboard.py')in dcproc:os.kill(dj['pid'],15)
   except ProcessLookupError:pass
  if state.get('state')in('running','starting')and state.get('ports',{}).get('qmp_port'):
   from hmi_bench.launcher import qmp
   qmp(state['ports']['qmp_port'],'quit')
  return 0
 if a.action=='serve':
  from hvf_android import ensure as android
  from hvf_dashboard import ensure as dashboard
  android();print(dashboard());return 0
 if previous:
  try:os.kill(previous['worker_pid'],0)
  except ProcessLookupError:pass
  else:
   from hvf_android import ensure as android
   from hvf_dashboard import ensure as dashboard
   android();print(json.dumps({'route':'HVF','firmware':'8704','session':previous['session'],'dashboard_url':dashboard()},indent=2));return 0
 c.validate('hmi');out=root/time.strftime('hmi-%Y%m%dT%H%M%SZ',time.gmtime());out.mkdir(parents=True,exist_ok=False)
 with(out/'worker.stdout').open('xb')as stdout,(out/'worker.stderr').open('xb')as stderr:
  child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'start','--worker','--session',str(out),'--language',a.language],cwd=ROOT,stdout=stdout,stderr=stderr,start_new_session=True)
 current.write_text(json.dumps({'worker_pid':child.pid,'session':str(out)},indent=2)+'\n')
 for _ in range(150):
  f=out/'session.json'
  if f.exists():
   j=json.loads(f.read_bytes())
   if j.get('state')=='running':
    from hvf_android import ensure as android
    from hvf_dashboard import ensure as dashboard
    android();print(json.dumps({'route':'HVF','firmware':'8704','worker_pid':child.pid,'qemu_pid':j['qemu_pid'],'viewer_url':j.get('viewer_url'),'dashboard_url':dashboard(),'session':str(out)},indent=2));return 0
   if j.get('state')in('error','ended'):raise RuntimeError(json.dumps(j))
  if child.poll()is not None:raise RuntimeError((out/'worker.stderr').read_text())
  time.sleep(.1)
 raise TimeoutError('Session preparation observation expired; existing worker retained')
if __name__=='__main__':raise SystemExit(main())
