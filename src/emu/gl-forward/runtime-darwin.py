
from pathlib import Path
import gzip,hashlib,importlib.util,json,os,socket,subprocess,threading,time

ROOT=Path(__file__).resolve().parents[3]
PROFILE=os.environ.get('OEM_8704_GL_FORWARD_BOOT','original-hmi-egl-v1')
PROFILES=('original-hmi-egl-v1',)

def selected():
 value=os.environ.get('OEM_8704_GL_FORWARD_BOOT','')
 if value and value not in PROFILES:raise ValueError('Unknown GL boot profile')
 return bool(value)

def assets():
 data=json.loads(Path(__file__).with_name('hmi-runtime-inputs-darwin.json'if PROFILE=='original-hmi-egl-v1'else'runtime-inputs.json').read_bytes())
 if data['profile']!=PROFILE:raise ValueError('GL runtime input profile differs')
 result={}
 for name,row in data['assets'].items():
  p=(ROOT/row['path']).resolve(strict=True)
  if not p.is_relative_to(ROOT)or p.is_symlink():raise ValueError('GL asset outside project')
  if p.stat().st_size!=row['bytes']or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('GL asset identity differs: '+name)
  result[name]=p
 return result

def carrier():return assets()['carrier']

def hmi_payload(inputs):
 prepare=b'''#!/bin/sh
set -eu
r=/run/oem-gl-forward
exec >"$r/prepare.stdout" 2>"$r/prepare.stderr"
chmod 0644 "$r/prepare.stdout" "$r/prepare.stderr"
trap 'rc=$?;printf "%s\\n" "$rc" >"$r/prepare.exit";chmod 0644 "$r/prepare.stdout" "$r/prepare.stderr" "$r/prepare.exit"' 0
n=0
until grep -q ' /vw ext4 rw[, ]' /proc/mounts;do n=$((n+1));[ "$n" -le 120 ] || exit 82;/bin/sleep 1;done
d=/vw/oem-gl-forward
[ ! -e "$d" ] || exit 81
mkdir "$d"
chmod 0755 "$d"
cp "$r/agent" "$d/agent"
chmod 0755 "$d/agent"
for f in gfx.so libjopengl_es3.so libFontSPINative.so;do
 original=/lge/hmi/libs/libjopengl_es3.so
 case "$f" in libFontSPINative.so)original=/lge/hmi/libs/libFontSPINative.so;;esac
 cp -a "$original" "$d/$f"
 [ ! -L "$d/$f" ] || exit 85
 cat "$r/$f" >"$d/$f"
 chmod 0644 "$d/$f"
done
mkdir -p "$d/jvm/bin"
chmod 0755 "$d/jvm" "$d/jvm/bin"
ln -s /lge/hmi/jvm/lib "$d/jvm/lib"
for f in java java-rollback;do
 cp -a /lge/hmi/jvm/bin/java "$d/jvm/bin/$f"
 [ ! -L "$d/jvm/bin/$f" ] || exit 85
 cat "$r/$f" >"$d/jvm/bin/$f"
done
for role in BASELINE MODIFIED ROLLBACK;do
 case "$role" in BASELINE)target=/lge/hmi/jvm/bin/java;;MODIFIED)target="$d/jvm/bin/java";;ROLLBACK)target="$d/jvm/bin/java-rollback";;esac
 if (unset LD_LIBRARY_PATH;exec /lib/ld-linux-aarch64.so.1 --list "$target") >"$r/java-$role.stdout" 2>"$r/java-$role.stderr";then rc=0;else rc=$?;fi
 printf '%s\\n' "$rc" >"$r/java-$role.exit"
 chmod 0644 "$r/java-$role.stdout" "$r/java-$role.stderr" "$r/java-$role.exit"
 [ "$rc" -eq 0 ] || exit 84
done
! grep -q libjsig "$r/java-BASELINE.stdout" || exit 84
grep -q libjsig "$r/java-MODIFIED.stdout" || exit 84
! grep -q libjsig "$r/java-ROLLBACK.stdout" || exit 84
cp -a /vw/hmiroot.sh "$d/hmi-entry"
cat "$r/hmi-entry" >"$d/hmi-entry"
cp -a /vw/hmiroot.sh "$d/hmiroot.sh"
cp -a /vw/hmi/runHMI.sh "$d/runHMI.sh"
cp -a /vw/hmiroot.sh "$d/hmi.stdout"
: >"$d/hmi.stdout"
chown 724:44 "$d/hmi.stdout"
chmod 0644 "$d/hmi.stdout"
[ "$(grep -Fc '/vw/hmi/runHMI.sh' "$d/hmiroot.sh")" = 1 ] || exit 83
[ "$(grep -Fc '| /lge/app_ro/bin/vtee' "$d/hmiroot.sh")" = 1 ] || exit 83
sed 's@/vw/hmi/runHMI.sh@/vw/oem-gl-forward/runHMI.sh@;s@| /lge/app_ro/bin/vtee@| /usr/bin/tee /vw/oem-gl-forward/hmi.stdout | /lge/app_ro/bin/vtee@' "$d/hmiroot.sh" >"$r/hmiroot-copy"
cat "$r/hmiroot-copy" >"$d/hmiroot.sh"
[ "$(grep -Fc -- '-Djava.library.path=$LD_LIBRARY_PATH' "$d/runHMI.sh")" = 1 ] || exit 83
[ "$(grep -Fc -- '-Dsun.boot.library.path=$LD_LIBRARY_PATH' "$d/runHMI.sh")" = 1 ] || exit 83
[ "$(grep -Fc 'JVM="$JAVA_HOME/bin/java"' "$d/runHMI.sh")" = 1 ] || exit 83
sed 's@-Djava.library.path=$LD_LIBRARY_PATH@-Djava.library.path=/vw/oem-gl-forward:$LD_LIBRARY_PATH@;s@-Dsun.boot.library.path=$LD_LIBRARY_PATH@-Dsun.boot.library.path=/vw/oem-gl-forward:$LD_LIBRARY_PATH@;s@JVM="$JAVA_HOME/bin/java"@JVM="/vw/oem-gl-forward/jvm/bin/java"@' "$d/runHMI.sh" >"$r/runHMI-copy"
cat "$r/runHMI-copy" >"$d/runHMI.sh"
chmod 0644 "$d/hmi-entry"
for f in gfx.so libjopengl_es3.so libFontSPINative.so hmi-entry hmiroot.sh runHMI.sh jvm/bin/java jvm/bin/java-rollback;do
 /usr/bin/chsmack -a hmi::sub -e hmi::sub "$d/$f" || exit 85
 /usr/bin/chsmack "$d/$f" >"$r/label-readback"
 grep -Fq 'access="hmi::sub" execute="hmi::sub"' "$r/label-readback" || exit 85
 cat "$r/label-readback" >>"$r/hmi-security.stdout"
done
chmod 0644 "$r/hmi-security.stdout"
mkdir "$r/ipc"
chmod 0755 "$r/ipc"
printf "ORIGINAL_HMI_PRIVATE_PREPARATION_COMPLETE\\n"
'''
 run=b'''#!/bin/sh
set -eu
r=/run/oem-gl-forward
exec >"$r/guest.stdout" 2>"$r/guest.stderr"
chmod 0644 "$r/guest.stdout" "$r/guest.stderr"
exec /vw/oem-gl-forward/agent
'''
 prepare_unit=b'''[Unit]
Description=Prepare private original HMI graphics files and labels
DefaultDependencies=no
Requires=post-init.service
After=post-init.service
Before=oem-gl-forward.service
[Service]
Type=oneshot
ExecStart=/bin/sh /run/oem-gl-forward/prepare
TimeoutStartSec=600
RemainAfterExit=yes
'''
 wrapper=b'''#!/bin/sh
export OEM_8704_GL_FORWARD=private-hmi-service-v1
export OEM_8704_GL_FORWARD_SOCKET=/run/oem-gl-forward/ipc/socket
exec /bin/sh -c '/vw/oem-gl-forward/hmiroot.sh'
'''
 unit=b'''[Unit]
Description=Private graphics hardware service for original HMI
DefaultDependencies=no
Requires=post-init.service oem-gl-prepare.service
After=post-init.service oem-gl-prepare.service
[Service]
Type=notify
NotifyAccess=all
ExecStart=/bin/sh /run/oem-gl-forward/run
ExecStartPost=/usr/bin/chsmack -a hmi::sub /run/oem-gl-forward/ipc/socket
TimeoutStartSec=150
'''
 dropin=b'''[Unit]
Requires=oem-gl-forward.service
After=oem-gl-forward.service
[Service]
ExecStart=
ExecStart=/bin/sh /vw/oem-gl-forward/hmi-entry
'''
 return dict(agent=inputs['agent'].read_bytes(),library=inputs['library'].read_bytes(),jni=inputs['jni'].read_bytes(),font=inputs['font'].read_bytes(),java=inputs['java'].read_bytes(),java_rollback=inputs['java_rollback'].read_bytes(),run=run,prepare=prepare,prepare_unit=prepare_unit,unit=unit,entry=wrapper,dropin=dropin)

def stage(raw,output):
 from vlm_platform import parse_dtb,rebuild_fdt
 spec=importlib.util.spec_from_file_location('_oem_gl_reservation',Path(__file__).with_name('reserve-guest.py'));reserve=importlib.util.module_from_spec(spec);spec.loader.exec_module(reserve)
 data,meta=reserve.reserve(raw);nodes=parse_dtb(data);chosen=nodes['/chosen'];inputs=assets()
 if any(k.startswith('oem-gl-')for k in chosen):raise ValueError('Nested GL staging')
 script=chosen.get('oem-stage',b'')
 if script.count(b'exec /sbin/init')!=1 or not script.rstrip().endswith(b'exec /sbin/init'):raise ValueError('Require one original init handoff')
 run=b'''#!/bin/sh
r=/run/oem-gl-forward
exec >"$r/guest.stdout" 2>"$r/guest.stderr"
trap 'rc=$?;printf "%s\n" "$rc" >"$r/guest.exit";chmod 0644 "$r/guest.stdout" "$r/guest.stderr" "$r/guest.exit"' 0
n=0
until grep -q ' /vw ext4 rw[, ]' /proc/mounts;do n=$((n+1));[ "$n" -le 120 ] || exit 82;/bin/sleep 1;done
d=/vw/oem-gl-forward
[ ! -e "$d" ] || exit 81
mkdir "$d" || exit 81
for f in agent liboem_gl_forward.so fixture;do cp "/run/oem-gl-forward/$f" "$d/$f" || exit 81;done
chmod 0755 "$d/agent" "$d/fixture" || exit 81
"$d/agent" 0xb7fc00000 0xb7fd00000 "$d/liboem_gl_forward.so" -- "$d/fixture"
rc=$?
exit "$rc"
'''
 unit=b'''[Unit]
Description=Private original Linux EGL transport
DefaultDependencies=no
[Service]
Type=oneshot
ExecStart=/bin/sh /run/oem-gl-forward/run
TimeoutStartSec=150
'''
 hmi=PROFILE=='original-hmi-egl-v1'
 payload=hmi_payload(inputs)if hmi else{'agent':inputs['agent'].read_bytes(),'library':inputs['library'].read_bytes(),'fixture':inputs['fixture'].read_bytes(),'run':run,'unit':unit}
 prefix='d=/run/oem-gl-forward;mkdir -p "$d"||exit 80;chmod 0755 "$d"||exit 80;'
 files={'agent':'agent','library':'gfx.so','jni':'libjopengl_es3.so','font':'libFontSPINative.so','java':'java','java_rollback':'java-rollback','run':'run','prepare':'prepare','prepare_unit':'prepare-unit','unit':'unit','entry':'hmi-entry','dropin':'hmi-dropin'}if hmi else{'agent':'agent','library':'liboem_gl_forward.so','fixture':'fixture','run':'run','unit':'unit'}
 hashes={};encodings={};stored_hashes={}
 for name,b in payload.items():
  packed=hmi and b.startswith(b'\x7fELF');stored=gzip.compress(b,mtime=0)if packed else b
  chosen['oem-gl-'+name]=stored;h=hashlib.sha256(b).hexdigest();hashes[name]=h;encodings[name]='gzip'if packed else'raw';stored_hashes[name]=hashlib.sha256(stored).hexdigest()
  prefix+=('/bin/busybox gzip -dc'if packed else'/bin/cat')+' /sys/firmware/devicetree/base/chosen/oem-gl-'+name+' >"$d/'+files[name]+'"||exit 80;'
  prefix+='printf "%s  %s\\n" '+h+' "$d/'+files[name]+'"|/usr/bin/sha256sum -c -||exit 80;'
 prefix+='chmod 0755 "$d/agent"'+(''if hmi else' "$d/fixture"')+'||exit 80;mkdir -p /run/systemd/system/basic.target.wants||exit 80;'
 prefix+='cp "$d/unit" /run/systemd/system/oem-gl-forward.service||exit 80;ln -s ../oem-gl-forward.service /run/systemd/system/basic.target.wants/oem-gl-forward.service||exit 80;'
 if hmi:prefix+='cp "$d/prepare-unit" /run/systemd/system/oem-gl-prepare.service||exit 80;mkdir -p /run/systemd/system/vw-hmi.service.d||exit 80;cp "$d/hmi-dropin" /run/systemd/system/vw-hmi.service.d/90-private-graphics.conf||exit 80;'
 chosen['oem-stage']=script.replace(b'exec /sbin/init',prefix.encode()+b'exec /sbin/init',1)
 out=rebuild_fdt(data,nodes)
 if len(out)>0x100000 or parse_dtb(out)!=nodes:raise ValueError('GL private DTB exceeds one MiB or differs')
 meta.update(panel_geometry=list(__import__('offline_bench_hmi').panel_geometry())if hmi else None,profile=PROFILE,parent_sha256=hashlib.sha256(raw).hexdigest(),sha256=hashlib.sha256(out).hexdigest(),bytes=len(out),guest_files=files,payload_sha256=hashes,payload_storage_encoding=encodings,payload_storage_sha256=stored_hashes,guest_executable_directory='/vw/oem-gl-forward',original_mount_options_unchanged=True,original_HMI_configuration_changed=False,private_HMI_dropin=hmi)
 if 'oem-touch-manifest'in chosen:meta['native_touch']=json.loads(chosen['oem-touch-manifest'])
 (Path(output)/'gl-forward-boot.json').write_text(json.dumps(meta,indent=2)+'\n',encoding='utf8')
 return out

class Host:
 def __init__(self,output):
  self.owner_pid=None;self.largest_viewport=None
  self.output=Path(output).resolve(strict=True);self.closed=threading.Event();self.connection=None;self.process=None;self.threads=[];self.errors=[];self.requests=0;self.replies=0;self.operations={};self.last_save=0;self.lock=threading.RLock();self.started=time.monotonic();self.expected_disconnect=False
  if not self.output.is_relative_to(ROOT/'var'):raise ValueError('GL runtime output outside task space')
 def save(self,force=False):
  with self.lock:
   now=time.monotonic()
   if not force and now-self.last_save<1:return
   self.last_save=now
   row=dict(guest_pid=self.owner_pid,guest_requested_largest_viewport=self.largest_viewport,profile=PROFILE,host_pid=self.process.pid if self.process else None,host_exit=self.process.poll()if self.process else None,connected=self.connection is not None,closed=self.closed.is_set(),request_bytes=self.requests,reply_bytes=self.replies,operations=self.operations,errors=self.errors,elapsed=now-self.started,frame_source='original-HMI-pending-provenance'if PROFILE=='original-hmi-egl-v1'else'explicit-original-linux-EGL-fixture',original_HMI_frame=False)
   p=self.output/'gl-forward-runtime.json';tmp=p.with_suffix('.new');tmp.write_text(json.dumps(row,indent=2)+'\n',encoding='utf8')
   for attempt in range(4):
    try:os.replace(tmp,p);break
    except PermissionError as error:
     if getattr(error,'winerror',None) not in (5,32):raise
     if attempt==3:return
     time.sleep(.001*(attempt+1))
 def error(self,error):
  self.errors.append(dict(error=str(error),during_shutdown=self.closed.is_set()or self.expected_disconnect));self.save(True)
 def __enter__(self):
  inputs=assets();env=os.environ.copy();env['DYLD_LIBRARY_PATH']=str(ROOT/'.local/angle-sdk/lib');env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+str(ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','');env['MHI2_GL_FRAME']=str(self.output/'gl-forward-frame.ppm')
  if PROFILE=='original-hmi-egl-v1':env['MHI2_GL_PANEL']='x'.join(map(str,__import__('offline_bench_hmi').panel_geometry()))
  self.listener=socket.socket();self.listener.bind(('127.0.0.1',0));self.listener.listen(1);self.listener.settimeout(.2);port=self.listener.getsockname()[1]
  self.log=(self.output/'gl-forward-host.stderr').open('xb')
  try:self.process=subprocess.Popen([str(inputs['host'])],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  except BaseException:self.listener.close();self.log.close();raise
  self.arguments=['-chardev',f'socket,id=glbridge,host=127.0.0.1,port={port}']
  thread=threading.Thread(target=self.accept,daemon=True);self.threads.append(thread);thread.start();self.save(True);return self
 def accept(self):
  try:
   while not self.closed.is_set():
    try:self.connection,_=self.listener.accept();break
    except socket.timeout:
     if self.process.poll()is not None:raise RuntimeError('GL host exited before QEMU connected')
   if self.closed.is_set():return
   self.save(True)
   for f in [self.uplink,self.downlink]:
    t=threading.Thread(target=f,daemon=True);self.threads.append(t);t.start()
  except (OSError,RuntimeError)as e:self.error(e)
 def uplink(self):
  import struct
  pending=bytearray()
  try:
   while not self.closed.is_set():
    b=self.connection.recv(65536)
    if not b:break
    self.requests+=len(b);pending.extend(b)
    while len(pending)>=8:
     op,n=struct.unpack_from('<II',pending)
     if n>16*1024*1024:raise ValueError('GL record exceeds codec budget')
     if len(pending)<8+n:break
     if PROFILE=='original-hmi-egl-v1'and op==119 and n==8:
      pid=struct.unpack_from('<I',pending,8)[0]
      if self.owner_pid is not None and self.owner_pid!=pid:raise ValueError('Authenticated HMI stream owner changed')
      self.owner_pid=pid
     if op==2 and n==16:
      viewport=list(struct.unpack_from('<4i',pending,8))
      if viewport[2]>0 and viewport[3]>0 and(self.largest_viewport is None or viewport[2]*viewport[3]>self.largest_viewport[2]*self.largest_viewport[3]):self.largest_viewport=viewport
     key=str(op)
     if op==200 and n>=72:key+=':'+str(struct.unpack_from('<I',pending,8)[0])
     self.operations[key]=self.operations.get(key,0)+1;del pending[:8+n]
    self.process.stdin.write(b);self.process.stdin.flush();self.save()
  except (OSError,ValueError)as e:self.error(e)
  finally:
   try:self.process.stdin.close()
   except (OSError,ValueError):pass
   self.save(True)
 def downlink(self):
  try:
   while b:=self.process.stdout.read1(65536):self.replies+=len(b);self.connection.sendall(b);self.save()
  except (OSError,ValueError)as e:self.error(e)
  finally:self.save(True)
 def __exit__(self,*unused):
  self.closed.set();self.listener.close()
  if self.connection:
   try:self.connection.shutdown(socket.SHUT_RDWR)
   except OSError:pass
  for t in list(self.threads):t.join(timeout=3)
  try:self.process.stdin.close()
  except (OSError,ValueError):pass
  try:self.process.wait(timeout=5)
  except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=5)
  if self.connection:self.connection.close()
  self.log.close();self.save(True)
