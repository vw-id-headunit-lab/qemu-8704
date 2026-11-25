from pathlib import Path
import hashlib,json,os,re,secrets,shlex,sys,threading,time
from hvf_console import ROOT
from hvf_qnx import owner

sys.path.insert(0,str(ROOT/'src/emu'))
import native_uart_console
from original_8704_vm import NativeInteractiveConsole,native_console_record
import linux_early_uart

_lock=threading.RLock()
_runs={name:threading.RLock()for name in('linux','android')}
_bridge=None
_epoch=None
_pending={}
_identities={}

def session(guest):
 if guest not in _runs:raise ValueError('Unknown native guest')
 with _lock:native_uart_console.configure_usi04()
 s,pid=owner()
 from native_domain_state import require_running
 require_running(s,pid,3 if guest=='linux'else 4)
 policy=json.loads((s/('linux-initcall-dtb.json'if guest=='linux'else'android-local-shell.json')).read_bytes())
 if policy.get('local_'+guest+'_shell')is not True:raise ValueError('Original native console was not staged')
 binding=linux_early_uart.read_direct_binding(s)if guest=='linux'else json.loads((s/'linux-native-console.json').read_bytes())
 return s,dict(qemu_pid=pid,session=str(s),kernel='original Exynos',console_route=binding['source']),binding['port']

def close():
 global _bridge,_epoch,_pending,_identities
 with _runs['linux'],_runs['android'],_lock:
  if _bridge is not None:_bridge.close()
  _bridge=None;_epoch=None;_pending={};_identities={}

def _capture(bridge,guest):
 return bridge.session/('linux-usi04-uart.bin'if guest=='linux'else'exynos-uart.bin')

def _journal_path(s,guest):
 return Path(s)/('native-console-command-'+guest+'.json')

def _controller(guest):
 global _bridge,_epoch
 with _lock:
  s,j,_=session(guest);epoch=(str(s),j['qemu_pid'])
  if _bridge is not None and _epoch!=epoch:raise RuntimeError('Original native epoch changed; reconnect the console owner')
  if _bridge is None:
   _bridge=NativeInteractiveConsole(s,direct_console=False);_epoch=epoch;legacy=s/'native-console-command.json'
   if legacy.exists():
    ticket=json.loads(legacy.read_bytes());name=ticket['guest']
    if name not in _runs or(ticket['session'],ticket['qemu_pid'])!=epoch:raise ValueError('Legacy native command owner differs')
    destination=_journal_path(s,name)
    if destination.exists():raise ValueError('Two retained commands for one native guest')
    os.replace(legacy,destination)
   for name in _runs:
    journal=_journal_path(s,name)
    if not journal.exists():continue
    ticket=json.loads(journal.read_bytes());capture=_capture(_bridge,name)
    if ticket['guest']!=name or(ticket['session'],ticket['qemu_pid'])!=epoch or ticket['capture']!=str(capture)or not re.fullmatch('MEBB_[0-9a-f]{16}',ticket['begin'])or not re.fullmatch('MEBE_[0-9a-f]{16}',ticket['end'])or type(ticket['start'])is not int or ticket['start']<0:raise ValueError('Retained original command owner differs')
    ticket['capture']=capture;_pending[name]=ticket
  if guest=='linux':_bridge.open_direct_console()
  return _bridge,s,j

def _lines(raw):
 result=[]
 for line in raw.split(b'\n')[:-1]:
  parsed=native_console_record(line);result.append(parsed[1]if parsed else line.rstrip(b'\r').decode('utf8','replace'))
 return result

def _receipt(ticket):
 p=Path(ticket['capture'])
 if not p.exists():return None
 with p.open('rb')as f:f.seek(ticket['start']);raw=f.read((1<<20)+1)
 if len(raw)>1<<20:raise RuntimeError('Native output exceeds 1MiB; original command retained')
 lines=_lines(raw)
 try:start=lines.index(ticket['begin'])+1
 except ValueError:return None
 for at in range(start,len(lines)):
  match=re.fullmatch(re.escape(ticket['end'])+r':(\d+)',lines[at])
  if match:
   stdout='\n'.join(lines[start:at])+('\n'if at>start else'')
   if ticket.get('framing')==2:
    if not stdout.endswith('\n'):raise ValueError('Native result separator missing')
    stdout=stdout[:-1]
   return dict(stdout=stdout,exit=int(match.group(1)),raw_sha256=hashlib.sha256(raw).hexdigest(),raw_start=ticket['start'],raw_end=ticket['start']+len(raw))
 return None

def _journal(ticket):
 p=_journal_path(ticket['session'],ticket['guest']);tmp=p.with_suffix('.tmp');data=dict(ticket,capture=str(ticket['capture']))
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w')as f:json.dump(data,f,ensure_ascii=False)
 os.replace(tmp,p)

def _archive(ticket,result):
 s=Path(ticket['session']);data=dict(ticket,capture=str(ticket['capture']),result=result)
 with(s/'native-console-commands.jsonl').open('a')as f:f.write(json.dumps(data,ensure_ascii=False)+'\n')
 _journal_path(s,ticket['guest']).unlink(missing_ok=True)

def _exchange(bridge,guest,command,timeout=60,prefix='',reuse_completed=False):
 previous=_pending.get(guest)
 if previous is not None:
  deadline=time.monotonic()+timeout;result=_receipt(previous)
  while result is None:
   if time.monotonic()>=deadline:raise TimeoutError('Previous original '+guest+' command is still completing; owner and nonce retained without resend')
   if bridge.focus()is None:raise RuntimeError('Native heartbeat unavailable; original command retained')
   time.sleep(.1);result=_receipt(previous)
  _archive(previous,result);del _pending[guest]
  if reuse_completed and previous['command']==command and previous.get('prefix','')==prefix:
   return dict(result,capture=str(previous['capture']),reused_completed_nonce=previous['begin'])
 nonce=secrets.token_hex(8);begin='MEBB_'+nonce;end='MEBE_'+nonce;shell='/bin/sh'if guest=='linux'else'/system/bin/sh'
 wire='echo '+begin+'; '+prefix+shell+' -c '+shlex.quote(command)+' 2>&1; printf '+shlex.quote('\\n'+end+':%s\\n')+' "$?"'
 if len(wire.encode())+1>240:raise ValueError('Framed native command requires source staging')
 capture=_capture(bridge,guest);ticket=dict(guest=guest,capture=capture,start=capture.stat().st_size if capture.exists()else 0,begin=begin,end=end,command=command,session=str(bridge.session),qemu_pid=_epoch[1],framing=2,prefix=prefix)
 _pending[guest]=ticket;_journal(ticket);sent=bridge.serial(guest,wire);deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  result=_receipt(ticket)
  if result is not None:
   _archive(ticket,result);del _pending[guest];result.update(sent=sent,capture=str(capture));return result
  if bridge.focus()is None:raise RuntimeError('Native heartbeat unavailable; original command retained')
  time.sleep(.1)
 raise TimeoutError('Original '+guest+' command is still completing; owner and nonce retained')

def _source_path(s,guest):
 return Path(s)/('native-console-source-'+guest+'.json')

def _save_source(job):
 p=_source_path(job['session'],job['guest']);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(job));tmp.chmod(0o600);os.replace(tmp,p)

def _source(s,pid,guest):
 p=_source_path(s,guest)
 if not p.exists():return None
 job=json.loads(p.read_bytes());directory=job['directory'];root='/tmp/'if guest=='linux'else'/data/local/tmp/'
 if (job['session'],job['qemu_pid'],job['guest'])!=(str(s),pid,guest)or not re.fullmatch(re.escape(root)+r'mN[0-9a-f]{24}',directory)or job['sha256']!=hashlib.sha256(job['command'].encode()).hexdigest():raise ValueError('Retained native source owner or content differs')
 identity=job['identity'];uid=65534 if guest=='linux'else 2000
 if identity['uid']!=uid or not identity['kernel'].startswith('5.4.131')or identity['proof']['stdout']!=str(uid)+'\n'+identity['kernel']+'\n':raise ValueError('Retained native source identity differs')
 return job

def _source_parts(guest,path,command):
 shell='/bin/sh'if guest=='linux'else'/system/bin/sh';parts=[];part=''
 def wire(text):
  append='printf %s '+shlex.quote(text)+' >>'+path
  framed='echo MEBB_0000000000000000; '+shell+' -c '+shlex.quote(append)+' 2>&1; printf '+shlex.quote('\\nMEBE_0000000000000000:%s\\n')+' "$?"'
  return append,len(framed.encode())+1
 for char in command:
  if char in '\n\t\r':
   if part:parts.append(wire(part)[0]);part=''
   escaped={'\n':'\\n','\t':'\\t','\r':'\\r'}[char];parts.append('printf '+shlex.quote(escaped)+' >>'+path);continue
  if wire(part+char)[1]>240:
   if not part:raise ValueError('Native source character exceeds framed UART limit')
   parts.append(wire(part)[0]);part=''
  part+=char
 if part:parts.append(wire(part)[0])
 return parts

def _staged(bridge,guest,command,s,pid,identity,job=None,prefix=''):
 if job is None:
  directory=('/tmp/'if guest=='linux'else'/data/local/tmp/')+'mN'+secrets.token_hex(12)
  job=dict(session=str(s),qemu_pid=pid,guest=guest,command=command,directory=directory,sha256=hashlib.sha256(command.encode()).hexdigest(),identity=identity,prefix=prefix,phase='prepare',index=0);_save_source(job)
 directory=job['directory'];path=directory+'/s';parts=_source_parts(guest,path,command)
 if job['phase']=='prepare':
  result=_exchange(bridge,guest,'mkdir -m 700 '+directory+' && : >'+path,reuse_completed=True)
  if result['exit']!=0:raise RuntimeError('Native source directory creation failed: '+result['stdout'])
  job['phase']='upload';_save_source(job)
 if job['phase']=='upload':
  while job['index']<len(parts):
   result=_exchange(bridge,guest,parts[job['index']],reuse_completed=True)
   if result['exit']!=0:raise RuntimeError('Native source append failed: '+result['stdout'])
   job['index']+=1;_save_source(job)
  job['phase']='verify';_save_source(job)
 if job['phase']=='verify':
  result=_exchange(bridge,guest,'sha256sum '+path,reuse_completed=True)
  if result['exit']!=0 or result['stdout'].split()[0:1]!=[job['sha256']]:raise RuntimeError('Native staged source hash differs; no payload execution')
  job['source_hash_receipt']=result;job['phase']='execute';_save_source(job)
 if job['phase']=='execute':
  result=_exchange(bridge,guest,('/bin/sh'if guest=='linux'else'/system/bin/sh')+' '+path,prefix=prefix,reuse_completed=True)
  job['result']=result;job['phase']='cleanup';_save_source(job)
 if job['phase']!='cleanup':raise ValueError('Unknown retained native source phase')
 cleaned=_exchange(bridge,guest,'rm -f '+path+'; rmdir '+directory,reuse_completed=True)
 if cleaned['exit']!=0:raise RuntimeError('Consumed native source cleanup failed: '+cleaned['stdout'])
 result=dict(job['result'],source_cleanup_exit=cleaned['exit']);_source_path(s,guest).unlink();return result

def run(guest,command,*,observe_framework=False):
 if guest not in _runs:raise ValueError('Unknown native guest')
 if not isinstance(command,str)or not command.strip()or len(command.encode())>4096 or any(not(c.isprintable()or c in '\n\t\r')for c in command):raise ValueError('Expected printable source up to4096bytes')
 with _runs[guest]:
  bridge,s,j=_controller(guest);uid=65534 if guest=='linux'else 2000;prefix='getprop sys.boot_completed; 'if guest=='android'and observe_framework else''
  job=_source(s,j['qemu_pid'],guest)
  if job is not None:
   if job['command']!=command or job.get('prefix','')!=prefix:raise RuntimeError('Original staged '+guest+' source retained; resume the same command before another request')
   _identities.setdefault(guest,job['identity'])
  if guest not in _identities:
   capture=_capture(bridge,guest)
   raw=b''
   if capture.exists():
    with capture.open('rb') as stream:
     stream.seek(max(0,capture.stat().st_size-32768));raw=stream.read(32768)
   prompt=rb'8704-vm3\$' if guest=='linux' else rb'(?:console:|:)/[^\r\n]* \$'
   if not re.search(prompt,raw):raise RuntimeError('Original '+guest+' shell prompt not yet observed; no command sent')
   checked=_exchange(bridge,guest,'id -u; uname -r',reuse_completed=True);values=checked['stdout'].splitlines()
   if checked['exit']!=0 or len(values)!=2 or values[0]!=str(uid)or not values[1].startswith('5.4.131'):raise RuntimeError('Original native UID or kernel differs: '+checked['stdout'])
   _identities[guest]=dict(uid=int(values[0]),kernel=values[1],observed_unix=time.time(),proof=checked)
  identity=_identities[guest]
  if job is not None:
   result=_staged(bridge,guest,command,s,j['qemu_pid'],identity,job,prefix=prefix)
  else:
   try:
    if any(c in command for c in '\n\t\r'):raise ValueError('Framed native command requires source staging')
    result=_exchange(bridge,guest,command,prefix=prefix,reuse_completed=True)
   except ValueError as error:
    if str(error)!='Framed native command requires source staging':raise
    result=_staged(bridge,guest,command,s,j['qemu_pid'],identity,prefix=prefix)
  stdout=result['stdout'];framework=None
  if prefix:
   first,separator,stdout=stdout.partition('\n')
   if not separator:raise RuntimeError('Original framework property response incomplete')
   framework=first.strip()=='1'
  response=dict(guest=guest,uid=identity['uid'],kernel=identity['kernel'],identity_observed_unix=identity['observed_unix'],identity_proof=identity['proof'],command=command,stdout=stdout,exit=result['exit'],qemu_pid=j['qemu_pid'],session=str(s),firmware='8704',framework_boot_completed=framework,transport='original-native-'+('usi04'if guest=='linux'else'vlm-vm4'),capture=result['capture'],raw_start=result['raw_start'],raw_end=result['raw_end'],raw_sha256=result['raw_sha256'])
  if 'source_cleanup_exit'in result:response['source_cleanup_exit']=result['source_cleanup_exit']
  return response

if __name__=='__main__':
 try:print(json.dumps(run(sys.argv[1],sys.argv[2]),ensure_ascii=False,indent=2))
 finally:close()
