from pathlib import Path
import sys,os,json,time,subprocess
from hvf_console import ROOT
from hvf_guest_command import session

def ensure():
 current=ROOT/'var/hvf/android-current.json'
 if current.exists():
  try:
   s,j,p=session('android')
   if 'ANDROID_CONTROL_READY='in(s/'stdout.txt').read_text(errors='replace'):return j
  except(ProcessLookupError,FileNotFoundError,ValueError,subprocess.CalledProcessError):pass
  else:raise RuntimeError('Android worker live; initialization is not complete; retained')
 root=ROOT/'var/hvf';root.mkdir(exist_ok=True)
 with(root/'android-worker.stdout').open('ab')as out,(root/'android-worker.stderr').open('ab')as err:child=subprocess.Popen([sys.executable,'-B',str(ROOT/'src/hvf_android/probe.py')],stdout=out,stderr=err,cwd=ROOT,start_new_session=True)
 for _ in range(150):
  if current.exists():
   j=json.loads(current.read_bytes())
   if j['worker_pid']==child.pid:
    text=(Path(j['session'])/'stdout.txt').read_text(errors='replace')
    if 'ANDROID_ORIGINAL_SECOND_STAGE_EXEC'in text and 'ANDROID_CONTROL_READY='in text:return j
    if any(x in text for x in ['PARTITION_DISCOVERY_FAILED','Kernel panic']):raise RuntimeError('Android initialization error: '+j['session'])
  if child.poll()is not None:raise RuntimeError('Android worker exited; inspect var/hvf/android-worker.stderr')
  time.sleep(.1)
 raise TimeoutError('Android preparation observation expired; worker retained')
if __name__=='__main__':print(json.dumps(ensure(),indent=2))
