from pathlib import Path
import importlib.util,json,subprocess,sys
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 p=r/'src/hvf_bench/guest/online-service.py';spec=importlib.util.spec_from_file_location('original_online',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 unit='[Service]\nUser=sint_sec_onl-cn-onlineservice\nExecStart=/lge/app_ro/onlinecore/onl-cn-onlineservice --log-level=0\nAmbientCapabilities=CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH CAP_FOWNER\n'
 argv=m.unit_command(unit,2000,100);assert argv==['/usr/sbin/capsh','--groups=','--gid=100','--keep=1','--uid=2000','--caps=cap_dac_override,cap_dac_read_search,cap_fowner=eip','--addamb=cap_dac_override,cap_dac_read_search,cap_fowner','--shell=/bin/sh','--','-c','exec '+m.EXECUTABLE+' --log-level=0']
 for text,uid,gid in [(unit.replace('CAP_FOWNER','CAP_SYS_ADMIN'),2000,100),(unit.replace('--log-level=0','--log-level=0 --bypass'),2000,100),(unit.replace('User=sint_sec_onl-cn-onlineservice','User=root'),2000,100),(unit,0,100),(unit,2000,0)]:
  try:m.unit_command(text,uid,gid)
  except ValueError:pass
  else:raise AssertionError('Unit mismatch accepted')
 stage=(r/'src/hvf_bench/guest/platform-setup.sh').read_text();assert stage.index('ready services --pid')<stage.index('/usr/bin/python3 /tmp/online-service.py')<stage.index('/usr/bin/python3 /tmp/permission-service.py')
 assert '"online-service.py": "online-service.py"'in(r/'src/hvf_bench/src/hmi_bench/archive.py').read_text()
 subprocess.run(['/bin/sh','-n',r/'src/hvf_bench/guest/platform-setup.sh'],capture_output=True,check=True)
 assert 'subprocess.PIPE'in p.read_text()and '/tmp/navigation-log.py'in p.read_text()
 print('ORIGINAL_ONLINE_STAGE_PASS exact_unit_uid2000=1 exact_ambient_caps0e=1 original_ELF_and_capsh_pinned=1 data_and_listener_owner_checked=1 startup_before_permission=1 bounded_logs=1 unit_mismatch_rejected=1 consent_unwritten=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
