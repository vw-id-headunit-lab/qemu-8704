import atexit,ctypes as c,ctypes.wintypes as w,hashlib,json,os,re
from pathlib import Path

PROFILE='virtual-bench-v1'
CODING=bytes(25)
RECORD=('1536 '+' '.join(map(str,CODING))+'\n').encode('ascii')
STAGE='/sys/firmware/devicetree/base/chosen/oem-stage'
BENCH='/sys/firmware/devicetree/base/chosen/oem-bench'

def selected(environ=None):
 env=os.environ if environ is None else environ
 value=env.get('OEM_8704_OFFLINE_BENCH','')
 if value and (value!=PROFILE or env.get('OEM_8704_OFFLINE_DEVELOPMENT')!='android-private-unencrypted-v1'):
  raise ValueError('Virtual bench input requires its explicit local offline profile')
 return bool(value)

def diagval_record(environ=None):
 env=os.environ if environ is None else environ
 source=env.get('OEM_8704_OFFLINE_DIAGVAL','')
 if not source:return RECORD
 if not selected(env):raise ValueError('Captured diagnostic input requires the private bench profile')
 path=Path(source)
 if not path.is_absolute() or path.is_symlink():raise ValueError('Require an absolute captured diagnostic file')
 raw=path.read_bytes()
 expected=env.get('EMU_DIAGVAL_SHA256','')
 if not re.fullmatch('[0-9a-f]{64}',expected) or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Private diagnostic checksum mismatch')
 if not raw.endswith(b'\n') or raw.endswith(b'\n\n') or any(v not in b'0123456789 \t\n' for v in raw):raise ValueError('Captured diagnostic format mismatch')
 records={}
 for line in raw.splitlines():
  values=[int(v)for v in line.split()]
  if len(values)<2 or values[0]in records or not 0<=values[0]<=65535 or any(not 0<=v<256 for v in values[1:]):raise ValueError('Captured diagnostic record mismatch')
  records[values[0]]=bytes(values[1:])
 if len(records.get(1536,b''))!=25:raise ValueError('Require a private 25-byte coding record')
 return raw


def bench_script():
 line=diagval_record().decode('ascii').rstrip('\n')
 return ("set -eu\np=/data/lge/hmi/persist/diagval\n"
         "[ -f \"$p\" ] && [ ! -L \"$p\" ] || exit 73\n"
         "/bin/mountpoint -q /data || exit 75\n"
         "v='"+line+"'\n"
         "if [ -s \"$p\" ]; then [ \"$(/bin/cat \"$p\")\" = \"$v\" ] || exit 74; "
         "else printf '%s\\n' \"$v\" >\"$p\"; fi\n"+
         native_resolution_script()+
         "printf 'virtual-bench-profile="+PROFILE+"\\n' >/run/oem-bench-input\n"
         "/usr/bin/sha256sum \"$p\" >>/run/oem-bench-input\n")


def native_resolution_script():
 if os.environ.get('OEM_8704_OFFLINE_BENCH_PANEL')!='bench-1920x1080-v1':return ''
 width,height=panel_geometry()
 return ('q=/data/resolution_config;r=/run/oem-bench-resolution\n'
         '[ ! -L "$q" ] && { [ ! -e "$q" ] || [ -f "$q" ]; } || exit 76\n'
         '[ ! -e "$r" ] || exit 76\nmkdir "$r"\n'
         'if [ -e "$q" ];then cp -p "$q" "$r/pristine";else : >"$r/absent";fi\n'
         't=/data/.oem-bench-resolution-config;[ ! -e "$t" ] || exit 76\n'
         "printf '%s\\n' '"+str(width)+' '+str(height)+"' >\"$t\"\n"
         '/usr/bin/chsmack -a hmi::sub "$t"\nchmod 0644 "$t"\nmv "$t" "$q"\n')

POWER_PROFILE='factory-rsi-power-v1'
POWER_ASSETS={'powermanagement': 'de7e88be2025f256f4cf5c2dfd33136237bfcecf99fef6ba8587cb584df46066', 'vehicleStates': '9af395266efd09029957cc1c979c3979ed42aa00f87422de0e17c4c44daf0553', 'userPresence': 'e0b25c777d0c78ceb5331f8e15d390ceddc8aab19c83e0bfe10dc46d7f77ca90', 'powerEvents': 'd31a8251e772c63db54a75031721e82925b67d96824747f3caf07ef1f0f4d139', 'doorsStatus': '8d812f0effa490d1246efb46f8d5be876127347ea05a538430fa1786a8b78cdc', 'onlineRemoteUpdate': '4c17fbbd16b86ed4a49a5bc78fd27731038cdf062778ef90675e1f870171bf3a'}
POWER_STAGE='/sys/firmware/devicetree/base/chosen/oem-bench-power'


IGNITION_PROFILE='vehicle-ignition-on-v1'
IGNITION_NODE='/sys/firmware/devicetree/base/chosen/oem-bench-vehicle-states'
IGNITION_SHA='4022f7a9d2dad2fc1b52c17aa394b5a9b60a66adfb7cbaaaf014a44d6617ac3f'


def ignition_selected(environ=None):
 env=os.environ if environ is None else environ
 value=env.get('OEM_8704_OFFLINE_BENCH_IGNITION','')
 if not value:return False
 if value!=IGNITION_PROFILE or not selected(env) or env.get('OEM_8704_OFFLINE_BENCH_POWER')!=POWER_PROFILE:raise ValueError('Virtual ignition requires the explicit local bench power profile')
 return True


def ignition_record():
 raw=Path(__file__).with_name('bench-vehicle-states.json').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=IGNITION_SHA:raise ValueError('Virtual ignition fixture hash mismatch')
 old=b'"name":"ignition","switchValue":"ON"';new=b'"name":"ignition","switchValue":"IDLE"'
 if raw.count(old)!=1 or hashlib.sha256(raw.replace(old,new)).hexdigest()!=POWER_ASSETS['vehicleStates']:raise ValueError('Virtual ignition must change only the factory ignition switchValue')
 return raw


GUEST_PROFILE='factory-anonymous-guest-v1'
GUEST_NODES={'users':'/sys/firmware/devicetree/base/chosen/oem-bench-guest-users','userContexts':'/sys/firmware/devicetree/base/chosen/oem-bench-guest-contexts'}
GUEST_ASSETS={'bench-anonymous-users.json': '5d04dbfcae8161b8c6a948de878219c34e1f7fb4b0d798207173f58faacf1c24', 'bench-anonymous-contexts.json': '4442b8ff52bd1637db4648ae908590dc94fda2fccd4e99bc30410e26fa274c1f'}


def guest_selected(environ=None):
 env=os.environ if environ is None else environ
 value=env.get('OEM_8704_OFFLINE_BENCH_GUEST','')
 if not value:return False
 if value!=GUEST_PROFILE or not selected(env) or env.get('OEM_8704_OFFLINE_BENCH_POWER')!=POWER_PROFILE or env.get('OEM_8704_OFFLINE_BENCH_IGNITION')!=IGNITION_PROFILE:raise ValueError('Anonymous guest fixture requires explicit local ignition and power bench profiles')
 return True


def guest_records():
 result={}
 for key,filename in [('users','bench-anonymous-users.json'),('userContexts','bench-anonymous-contexts.json')]:
  raw=Path(__file__).with_name(filename).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=GUEST_ASSETS[filename]:raise ValueError('Anonymous factory fixture hash mismatch')
  result[key]=raw
 users=json.loads(result['users']);contexts=json.loads(result['userContexts'])
 if len(users)!=1 or users[0]['authenticationLevel']!='level0' or users[0]['userRole']!='anonymousGuestUser' or len(contexts)!=1:raise ValueError('Only factory level0 anonymous guest is permitted')
 active=contexts[0]['activeUser']
 if active!={'id':users[0]['id'],'name':users[0]['name'],'uri':'/usermanagement/users/'+users[0]['id']}:raise ValueError('Anonymous factory user context reference mismatch')
 return result


def power_bench_script():
 root='/run/oem-bench-power'
 lines=['set -eu','test -f /run/oem-bench-input',
        "printf '%s  %s\\n' '0f6e9cda8b2e2b774eb728da0f850ac044a0d055a4b1b265449fb0f51f343dec' /lge/app_ro/bin/rsi-mockup|/usr/bin/sha256sum -c -",
        'mkdir -p '+root+'/vehicledynamics',
        "printf 'vehicledynamics:system\\n'>"+root+'/services']
 for name,digest in POWER_ASSETS.items():
  original='/lge/app_ro/sai/mockup/vehicledynamics/'+name+'.json'
  lines.extend(["printf '%s  %s\\n' '"+digest+"' "+original+'|/usr/bin/sha256sum -c -',
                'cp '+original+' '+root+'/vehicledynamics/'+name+'.json'])
 if ignition_selected():
  ignition_record()
  lines.extend(['/bin/cat '+IGNITION_NODE+' >'+root+'/vehicledynamics/vehicleStates.json',
                "printf '%s  %s\\n' '"+IGNITION_SHA+"' "+root+'/vehicledynamics/vehicleStates.json|/usr/bin/sha256sum -c -',
                "printf 'virtual-bench-ignition=ON\\n'>"+root+'/ignition-profile'])
 if guest_selected():
  guest_records()
  lines.append('mkdir -p '+root+'/usermanagement')
  for key,filename in [('users','bench-anonymous-users.json'),('userContexts','bench-anonymous-contexts.json')]:
   target=root+'/usermanagement/'+key+'.json'
   lines.extend(['/bin/cat '+GUEST_NODES[key]+' >'+target,
                 "printf '%s  %s\\n' '"+GUEST_ASSETS[filename]+"' "+target+'|/usr/bin/sha256sum -c -'])
  lines.extend(["printf 'vehicledynamics:system\\nusermanagement:auth\\n'>"+root+'/services',
                "printf 'virtual-bench-guest=anonymousGuestUser-level0\\n'>"+root+'/guest-profile'])
 lines.extend(['test ! -e '+root+'/vehicledynamics/componentProtections.json',
               "printf 'virtual-bench-power="+POWER_PROFILE+"\\n'>"+root+'/profile'])
 return '\n'.join(lines)+'\n'


def power_stage(nodes):
 ignition_selected()
 guest_selected()
 value=os.environ.get('OEM_8704_OFFLINE_BENCH_POWER','')
 if not value:return ''
 if value!=POWER_PROFILE or not selected():raise ValueError('Original RSI power provider requires explicit local bench profile')
 chosen=nodes['/chosen']
 if 'oem-bench-power' in chosen:raise ValueError('Nested bench power stage')
 chosen['oem-bench-power']=power_bench_script().encode('ascii')
 if ignition_selected():
  if 'oem-bench-vehicle-states'in chosen:raise ValueError('Nested virtual ignition fixture')
  chosen['oem-bench-vehicle-states']=ignition_record()
 if guest_selected():
  for key,raw in guest_records().items():
   name=GUEST_NODES[key].rsplit('/',1)[1]
   if name in chosen:raise ValueError('Nested anonymous guest fixture')
   chosen[name]=raw
 unit='[Unit]\nRequires=persistence-app.service\nAfter=persistence-app.service\n[Service]\nExecStart=\nExecStartPre=/bin/sh '+POWER_STAGE+'\nExecStart=/lge/app_ro/bin/rsi-mockup -rsi-mockup.dir=/run/oem-bench-power -rsi-log_console=true\n'
 import shlex
 return ('p=/run/systemd/system/rsi-mockup.service.d;mkdir -p "$p";printf %s '+shlex.quote(unit)+'>"$p/10-bench-power.conf";'
         'd=/run/systemd/system/multi-user.target.wants;mkdir -p "$d";'
         '[ -e "$d/rsi-mockup.service" ]||ln -s /lib/systemd/system/rsi-mockup.service "$d/rsi-mockup.service";')


PANEL_PROFILE='original-1560x700-v1'
PANEL_PROFILES={PANEL_PROFILE:(1560,700),'bench-1920x1080-v1':(1920,1080)}


def panel_geometry():
 value=os.environ.get('OEM_8704_OFFLINE_BENCH_PANEL','')
 if value not in PANEL_PROFILES:raise ValueError('Explicit private panel geometry required')
 return PANEL_PROFILES[value]



RSI_QUEUE_PROFILE='original-found-dispatch-v1'
RSI_QUEUE_NODE='/sys/firmware/devicetree/base/chosen/oem-bench-rsi-monitor'
RSI_QUEUE_JAR_SHA='ccb388ee6e929ba5b40d6497a364cc81111dfe8967e134e51584a9104b468a5c'


def rsi_queue_selected():
 value=os.environ.get('OEM_8704_OFFLINE_BENCH_RSI_QUEUE','')
 if not value:return False
 if value!=RSI_QUEUE_PROFILE or not selected() or os.environ.get('OEM_8704_OFFLINE_BENCH_PANEL')not in PANEL_PROFILES:raise ValueError('RSI queue correction requires the explicit private bench panel')
 return True


RSI_POWER_PROFILE='original-vehicle-state-io-recovery-v1'
RSI_POWER_NODE='/sys/firmware/devicetree/base/chosen/oem-bench-rsi-power-recovery'
RSI_POWER_SHA='139e1f22ab3e1ed51e5a1121c256fbad571c4a22bfeaef9f23105341c5184e3a'


def rsi_power_selected():
 value=os.environ.get('OEM_8704_OFFLINE_BENCH_RSI_POWER','')
 if not value:return False
 if value!=RSI_POWER_PROFILE or not rsi_queue_selected() or not ignition_selected() or not guest_selected():raise ValueError('Original RSI power recovery requires explicit local ignition, guest and queue profiles')
 return True


def rsi_power_stage(nodes):
 if not rsi_power_selected():return ''
 import io,zipfile
 raw=Path(__file__).with_name('rsi-power-recovery.jar').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=RSI_POWER_SHA:raise ValueError('Private RSI power recovery jar differs')
 name='de/vw/mib/asl/internal/powermanagement/rsi/RSIVehicleDynamicsAccessImpl.class'
 with zipfile.ZipFile(io.BytesIO(raw))as jar:
  if jar.namelist()!=[name]or hashlib.sha256(jar.read(name)).hexdigest()!='371ea59e1619ed83d2c02ef2077851de929ad02bf1e88e2f6fee8301825a236e':raise ValueError('Private RSI power recovery class differs')
 if 'oem-bench-rsi-power-recovery'in nodes['/chosen']:raise ValueError('Nested private RSI power recovery')
 nodes['/chosen']['oem-bench-rsi-power-recovery']=raw
 return ('p=/run/oem-bench-rsi-power-recovery.jar;[ ! -e "$p" ]||exit 78;'
         '/bin/cat '+RSI_POWER_NODE+' >"$p"||exit 78;'
         "printf '%s  %s\\n' "+RSI_POWER_SHA+' "$p"|/usr/bin/sha256sum -c -||exit 78;')


RSI_ROUTE_PROFILE='original-local-vehicle-provider-v1'
RSI_ROUTE_NODE='/sys/firmware/devicetree/base/chosen/oem-bench-rsi-local-route'
RSI_ROUTE_SHA='d244c91d15f2d2b10fba69062fd0964acd464fdf24044e5cf199a642f94964b6'


def rsi_route_selected():
 value=os.environ.get('OEM_8704_OFFLINE_BENCH_RSI_ROUTE','')
 if not value:return False
 if value!=RSI_ROUTE_PROFILE or not rsi_power_selected() or os.environ.get('OEM_8704_OFFLINE_BENCH_INPUT_READY')!='factory-input-ready-v1':raise ValueError('Local original vehicle provider selection requires the explicit private power, guest, queue and input profiles')
 return True


def rsi_route_stage(nodes):
 if not rsi_route_selected():return ''
 import io,zipfile
 raw=Path(__file__).with_name('rsi-local-fixture-route.jar').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=RSI_ROUTE_SHA:raise ValueError('Private local vehicle route jar differs')
 name='de/vw/mib/rsi/framework/viwiaccess/ServiceRegistryMonitor.class'
 with zipfile.ZipFile(io.BytesIO(raw))as jar:
  if jar.namelist()!=[name]or hashlib.sha256(jar.read(name)).hexdigest()!='fcdcecc5739ec0c52f7a79aae84129685d60b25288a80bdc2f2e433aaa4f43bf':raise ValueError('Private local vehicle route class differs')
 if 'oem-bench-rsi-local-route'in nodes['/chosen']:raise ValueError('Nested private local vehicle route')
 nodes['/chosen']['oem-bench-rsi-local-route']=raw
 return ('p=/run/oem-bench-rsi-local-route.jar;[ ! -e "$p" ]||exit 78;'
         '/bin/cat '+RSI_ROUTE_NODE+' >"$p"||exit 78;'
         "printf '%s  %s\\n' "+RSI_ROUTE_SHA+' "$p"|/usr/bin/sha256sum -c -||exit 78;')


def rsi_queue_stage(nodes):
 if not rsi_queue_selected():return ''
 import io,zipfile
 raw=Path(__file__).with_name('rsi-monitor-queue.jar').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=RSI_QUEUE_JAR_SHA:raise ValueError('Private RSI queue jar hash mismatch')
 name='de/vw/mib/rsi/framework/viwiaccess/RsiServiceMonitorImpl$MonitorInfo.class'
 with zipfile.ZipFile(io.BytesIO(raw))as jar:
  if jar.namelist()!=[name] or hashlib.sha256(jar.read(name)).hexdigest()!='0a3c099df06045a061b00fad19f758b51235ee63bbcf05d629580507fbf51987':raise ValueError('Private RSI queue class mismatch')
 if 'oem-bench-rsi-monitor'in nodes['/chosen']:raise ValueError('Nested private RSI queue artifact')
 nodes['/chosen']['oem-bench-rsi-monitor']=raw
 import shlex
 flags='-Xpreload:off -Xshare:off -XX:-CompressedSharedSpaces -XX:-EnableSharedLookupCache -XX:-PrecompilePreloadedClasses -XX:TieredStopAtLevel=4'
 unit='[Service]\nEnvironment="_JAVA_OPTIONS='+flags+'"\n'
 return rsi_route_stage(nodes)+rsi_power_stage(nodes)+('p=/run/oem-bench-rsi-monitor.jar;[ ! -e "$p" ]||exit 78;'
         '/bin/cat '+RSI_QUEUE_NODE+' >"$p"||exit 78;'
         "printf '%s  %s\\n' "+RSI_QUEUE_JAR_SHA+' "$p"|/usr/bin/sha256sum -c -||exit 78;'
         'd=/run/systemd/system/vw-hmi.service.d;mkdir -p "$d";'
         '[ ! -e "$d/22-bench-rsi-cache.conf" ]||exit 79;'
         'printf %s '+shlex.quote(unit)+'>"$d/22-bench-rsi-cache.conf";')


def input_ready_stage():
 value=os.environ.get('OEM_8704_OFFLINE_BENCH_INPUT_READY','')
 if not value:return ''
 if value!='factory-input-ready-v1' or not selected():raise ValueError('Input readiness requires the explicit private bench profile')
 import shlex
 units='post-init.service touch-app.service serviceregistry.service'
 unit='[Unit]\nRequires='+units+'\nAfter='+units+'\n'
 return ('p=/run/systemd/system/vw-hmi.service.d;mkdir -p "$p";'
         '[ ! -e "$p/21-bench-input-ready.conf" ]||exit 77;'
         'printf %s '+shlex.quote(unit)+'>"$p/21-bench-input-ready.conf";')


def panel_stage():
 value=os.environ.get('OEM_8704_OFFLINE_BENCH_PANEL','')
 if not value:return ''
 if value not in PANEL_PROFILES or not selected():raise ValueError('Panel geometry requires the explicit private bench profile')
 import shlex
 width,height=panel_geometry();fields={'width':width,'height':height,'activeAreaWidth':width,'activeAreaHeight':height}
 options=' '.join('-Dde.vw.mib.lsc.initial.fallback.'+name+'='+str(n)for name,n in fields.items())
 if rsi_queue_selected():options+=' -Xbootclasspath/p:'+('/run/oem-bench-rsi-local-route.jar:' if rsi_route_selected()else '')+('/run/oem-bench-rsi-power-recovery.jar:' if rsi_power_selected()else '')+'/run/oem-bench-rsi-monitor.jar'
 unit='[Service]\nEnvironment="VMOPTIONS='+options+'"\n'
 return ('p=/run/systemd/system/vw-hmi.service.d;mkdir -p "$p";'
         '[ ! -e "$p/20-bench-panel.conf" ]||exit 76;'
         'printf %s '+shlex.quote(unit)+'>"$p/20-bench-panel.conf";')


def stage(nodes,script):
 if not script.endswith('exec /sbin/init') or script.count('exec /sbin/init')!=1:
  raise ValueError('Require one original init handoff')
 chosen=nodes['/chosen']
 if any(k in chosen for k in ('oem-stage','oem-bench')):raise ValueError('Nested virtual bench stage')
 unit='[Unit]\nRequires=data.mount\nAfter=data.mount\n[Service]\nExecStartPre=/bin/sh '+BENCH+'\n'
 prefix=rsi_queue_stage(nodes)+input_ready_stage()+panel_stage()+power_stage(nodes)+"d=/run/systemd/system/persistence-app.service.d;mkdir -p $d&&printf '"+unit.replace('\n',r'\n')+"'>$d/10-bench.conf; "
 chosen['oem-stage']=(prefix+script+'\n').encode('ascii')
 chosen['oem-bench']=bench_script().encode('ascii')
 return '/bin/mount -t sysfs sysfs /sys 2>/dev/null; exec /bin/sh '+STAGE

def adapt_staged_dtb(raw):
 from vlm_platform import parse_dtb,rebuild_fdt
 import re
 nodes=parse_dtb(raw);value=nodes['/chosen']['bootargs'];assert value.endswith(b'\0') and b'\0' not in value[:-1]
 text=value[:-1].decode('ascii');match=re.fullmatch(r'(.* -- -c ")(.*)("(?: [^"]*)?)',text)
 if not match:raise ValueError('Expected one staged Linux init command')
 command=stage(nodes,match[2]);nodes['/chosen']['bootargs']=(match[1]+command+match[3]).encode()+b'\0'
 result=rebuild_fdt(raw,nodes);assert parse_dtb(result)==nodes and len(nodes['/chosen']['bootargs'])<=2048
 return result

def metadata():
 return {'profile':PROFILE,'label':'用户授权的本机虚拟台架配置','coding_bytes':len(CODING),'coding_hex':CODING.hex(),
         'record_key':1536,'record_sha256':hashlib.sha256(RECORD).hexdigest(),'guest_path':'/data/lge/hmi/persist/diagval',
         'known_prefix_source':'existing ID.3 known-bit fixture','remaining_bytes_policy':'explicit virtual zero defaults',
         'application':'original persistence-app ExecStartPre writes existing empty private file before original parser starts',
         'unit':'persistence-app.service','device_tree_properties':['/chosen/oem-stage','/chosen/oem-bench'],
         'touch_transport':'original-kipc-write-v1','touch_panel':[1560,700],'render_panel':list(panel_geometry())if os.environ.get('OEM_8704_OFFLINE_BENCH_PANEL')else None,
         'diagnostic_source':'captured-local' if os.environ.get('OEM_8704_OFFLINE_DIAGVAL') else 'virtual-bench',
         'diagnostic_sha256':hashlib.sha256(diagval_record()).hexdigest(),'diagnostic_records':len(diagval_record().splitlines())}

class KeepAwake:
 def __init__(self):self.handle=None;self.active=[];self.release_results=[]
 def __enter__(self):
  if os.name!='nt':raise OSError('Windows native power request required')
  class Detail(c.Structure):_fields_=[('module',w.HMODULE),('id',w.ULONG),('count',w.ULONG),('strings',c.POINTER(w.LPWSTR))]
  class Reason(c.Union):_fields_=[('simple',w.LPWSTR),('detail',Detail)]
  class Context(c.Structure):_fields_=[('version',w.ULONG),('flags',w.DWORD),('reason',Reason)]
  self.kernel=c.WinDLL('kernel32',use_last_error=True);self.kernel.PowerCreateRequest.argtypes=[c.POINTER(Context)];self.kernel.PowerCreateRequest.restype=w.HANDLE
  self.kernel.PowerSetRequest.argtypes=[w.HANDLE,c.c_int];self.kernel.PowerSetRequest.restype=w.BOOL
  self.kernel.PowerClearRequest.argtypes=[w.HANDLE,c.c_int];self.kernel.PowerClearRequest.restype=w.BOOL;self.kernel.CloseHandle.argtypes=[w.HANDLE]
  reason=Context();reason.version=0;reason.flags=1;reason.reason.simple='MEB-EMU 原厂三系统正在运行'
  self.handle=self.kernel.PowerCreateRequest(c.byref(reason))
  if not self.handle or self.handle==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
  try:
   for kind in (1,3):
    if not self.kernel.PowerSetRequest(self.handle,kind):raise c.WinError(c.get_last_error())
    self.active.append(kind)
  except BaseException:self.close();raise
  return self
 def close(self):
  if self.handle:
   for kind in reversed(self.active):self.release_results.append({'type':kind,'cleared':bool(self.kernel.PowerClearRequest(self.handle,kind))})
   self.active.clear();self.kernel.CloseHandle(self.handle);self.handle=None
 def __exit__(self,*args):self.close()

def install_keep_awake(output,environ=None):
 env=os.environ if environ is None else environ;value=env.get('OEM_8704_OFFLINE_KEEP_AWAKE','')
 if not value:return None
 if value!='system-execution-v1' or env.get('OEM_8704_OFFLINE_DEVELOPMENT')!='android-private-unencrypted-v1':
  raise ValueError('Process power request requires explicit native offline profile')
 request=KeepAwake().__enter__();destination=Path(output)/'native-host-power-request.json'
 def record(phase):
  if destination.parent.is_dir():destination.write_text(json.dumps({'profile':value,'phase':phase,'pid':os.getpid(),'active_request_types':request.active,'release_results':request.release_results,'global_power_configuration_changed':False},indent=2),encoding='utf8')
 def finish():request.close();record('released')
 request.record_active=lambda:record('active')
 atexit.register(finish);record('active');return request
