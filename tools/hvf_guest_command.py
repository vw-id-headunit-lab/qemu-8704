from pathlib import Path
import json,os,shlex,secrets,re,subprocess
from hvf_console import execute,ROOT

def session(guest):
 if guest=='linux':
  record=json.loads((ROOT/'var/hvf/current.json').read_bytes());s=Path(record['session']);j=json.loads((s/'session.json').read_bytes());port=j['ports']['debug_port'];kernel=str(ROOT/'src/hvf_bench/runtime/alpine/vmlinuz-virt')
 elif guest=='android':
  j=json.loads((ROOT/'var/hvf/android-current.json').read_bytes());s=Path(j['session']);port=j['control_port'];kernel=str(ROOT/'src/hvf_android/runtime/kernel/Image')
 else:raise ValueError('Unknown guest')
 os.kill(j['qemu_pid'],0);q=subprocess.run(['ps','-p',str(j['qemu_pid']),'-o','command='],capture_output=True,text=True,check=True)
 if str(ROOT/'runtime/darwin/qemu-system-aarch64')not in q.stdout or kernel not in q.stdout or f'port={port},'not in q.stdout:raise ValueError('Guest process identity changed')
 return s,j,port

def run(guest,command):
 if not isinstance(command,str)or not command.strip()or len(command.encode('utf8'))>4096 or '\0'in command:raise ValueError('Expected nonempty command up to4096bytes')
 s,j,port=session(guest);tag='EXIT_'+secrets.token_hex(12)
 if guest=='linux':
  code="import os;os.setgroups([]);os.setgid(65534);os.setuid(65534);os.chdir('/tmp');os.execve('/bin/sh',['sh','-c',"+repr(command)+"],{'PATH':'/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8'})"
  payload='chroot /mnt/agl /usr/bin/python3 -c '+shlex.quote(code);uid=65534
 else:
  prefix=''if j.get('android_root')=='/'else'chroot /android '
  payload=prefix+'/bench/busybox su shell -s /system/bin/sh -c '+shlex.quote(command);uid=2000
 busybox='/bench/busybox'if guest=='android'and j.get('android_root')=='/'else'/bin/busybox'
 wire=busybox+' timeout -s TERM -k 1 10 '+busybox+' sh -c '+shlex.quote(payload)+'; printf "\\n'+tag+'=%s\\n" "$?"';text=execute(wire,timeout=13,port=port,busybox=busybox);m=re.search(r'\n'+tag+r'=(\d+)\r?\n',text)
 if not m:raise RuntimeError('Command completion marker missing; guest retained')
 stdout=text[:m.start()];result=dict(guest=guest,uid=uid,command=command,stdout=stdout,exit=int(m.group(1)),qemu_pid=j['qemu_pid'],session=str(s),kernel='generic HVF',firmware='8704',framework_boot_completed=None)
 if guest=='android':
  observed=execute('printf "BENCH_BOOT_COMPLETE=%s\\n" "$(getprop sys.boot_completed)"',timeout=3,port=port,busybox=busybox)
  boot=re.search(r'BENCH_BOOT_COMPLETE=(.*)\r?\n',observed)
  if boot:result['framework_boot_completed']=boot.group(1).strip()=='1'
 return result
if __name__=='__main__':
 import sys
 print(json.dumps(run(sys.argv[1],sys.argv[2]),ensure_ascii=False,indent=2))
