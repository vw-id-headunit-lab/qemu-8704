from pathlib import Path
import importlib.util,sys,json,copy,tempfile,hashlib,subprocess,shlex
from unittest.mock import patch
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'));spec=importlib.util.spec_from_file_location('pending_command_test',r/'tools/native_guest_command.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 previous=dict(begin='MEBB_0123456789abcdef',command='previous',capture='/tmp/not-read',guest='android');current=dict(stdout='previous complete\n',exit=0,raw_sha256='0'*64,raw_start=0,raw_end=1)
 class Bridge:
  session=Path('/tmp/native-pending-fixture')
  def __init__(self):self.sent=[];self.live=True
  def focus(self):return dict(running=True)if self.live else None
  def serial(self,guest,wire):self.sent.append((guest,wire));return dict(sent=True)
 for same,prefix in [(False,''),(True,''),(True,'getprop sys.boot_completed; ')]:
  reused=same and not prefix
  b=Bridge();clock=[0.];archives=[];calls=[0]
  def receipt(t):
   if t is not old:return dict(current,stdout='new complete\n')
   calls[0]+=1;return None if calls[0]<3 else dict(current)
  def sleep(t):clock[0]+=t
  old=copy.deepcopy(previous);old['command']='next'if same else'previous'
  with patch.object(m,'_pending',{'android':old}),patch.object(m,'_epoch',(str(b.session),42)),patch.object(m,'_receipt',receipt),patch.object(m,'_archive',lambda t,res:archives.append((t,res))),patch.object(m,'_journal',lambda t:None),patch.object(m,'_capture',return_value=Path('/tmp/missing-native-fixture-capture')),patch.object(m.time,'monotonic',lambda:clock[0]),patch.object(m.time,'sleep',sleep):
   result=m._exchange(b,'android','next',timeout=1,prefix=prefix,reuse_completed=True)
   assert len(archives)==2-reused and len(b.sent)==1-reused
   assert not m._pending
   if reused:assert result['reused_completed_nonce']==old['begin']and result['stdout']=='previous complete\n'
   else:assert result['stdout']=='new complete\n'and clock[0]>=.2
 b=Bridge();clock=[0.];old=copy.deepcopy(previous)
 with patch.object(m,'_pending',{'android':old}),patch.object(m,'_receipt',return_value=None),patch.object(m.time,'monotonic',lambda:clock[0]),patch.object(m.time,'sleep',lambda t:clock.__setitem__(0,clock[0]+t)),patch.object(m,'_archive',side_effect=AssertionError('Must retain unfinished ticket')):
  try:m._exchange(b,'android','next',timeout=.3,reuse_completed=True)
  except TimeoutError:pass
  else:raise AssertionError('Unfinished original operation did not remain pending')
  assert m._pending['android']is old and not b.sent
 assert 'command,prefix=prefix,reuse_completed=True'in(r/'tools/native_guest_command.py').read_text()
 with tempfile.TemporaryDirectory()as temp:
  root=Path(temp);b=Bridge();b.session=root
  source="printf %s '中文和单引号'\n"+"printf %s "+shlex.quote("a'b")
  digest=hashlib.sha256(source.encode()).hexdigest();identity=dict(uid=2000,kernel='5.4.131-android11-2-ab151',proof=dict(stdout='2000\n5.4.131-android11-2-ab151\n'));calls=[];blocked=[True];path=[]
  def exchange(bridge,guest,command,**kwargs):
   assert kwargs.get('reuse_completed')is True;calls.append(command)
   if command.startswith('mkdir '):path.append(command.split()[-1][1:]);return dict(current,stdout='')
   if command.startswith('sha256sum '):return dict(current,stdout=digest+'  '+path[0]+'\n')
   if command.startswith('/system/bin/sh '):
    if blocked[0]:blocked[0]=False;raise TimeoutError('Retained actual payload nonce')
    return dict(current,stdout='original app',exit=7,reused_completed_nonce='MEBB_0123456789abcdef')
   return dict(current,stdout='')
  with patch.object(m,'_exchange',exchange):
   try:m._staged(b,'android',source,root,42,identity)
   except TimeoutError:pass
   else:raise AssertionError('Payload timeout was not retained')
   job=m._source(root,42,'android');assert job['phase']=='execute'and job['command']==source and job['source_hash_receipt']['exit']==0
   result=m._staged(b,'android',source,root,42,identity,job);assert result['exit']==7 and result['source_cleanup_exit']==0 and result['reused_completed_nonce']=='MEBB_0123456789abcdef'and not m._source_path(root,'android').exists()
  assert sum(x.startswith('mkdir ')for x in calls)==1 and sum(x.startswith('sha256sum ')for x in calls)==1
  parts=m._source_parts('android',path[0],source);assert len([x for x in calls if x.startswith('printf ')])==len(parts)
  target=root/'uploaded';target.write_bytes(b'')
  for command in parts:
   command=command.replace(path[0],str(target));p=subprocess.run(['/bin/sh','-c',command],capture_output=True);assert p.returncode==0
  assert target.read_bytes()==source.encode()
 print('NATIVE_PENDING_COMMAND_PASS wait_same_original_nonce=1 next_step_starts_once=1 completed_same_command_reused=1 observer_timeout_retains_owner=1 no_guest_restart_or_resend=1 staged_phase_and_source_retained=1 no_reupload_after_payload_timeout=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
