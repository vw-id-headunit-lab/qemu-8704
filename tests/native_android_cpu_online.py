from pathlib import Path
import importlib.util,sys,json,tempfile,hashlib
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1];repo=Path(__file__).resolve().parents[1]
try:
 sys.path[:0]=[str(repo/'tools'),str(repo/'src/emu'),str(repo/'src/firmware')]
 def module(name,path):
  spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
 o=module('cpu_init_stage',r/'src/emu/offline_development.py');q=module('cpu_seed_stage',r/'tools/hvf_qnx.py')
 hcb=(repo/'var/inputs/support/vlm-exynos-bringup/original-boot/hcb/hcb.img').read_bytes();before=b'on early-init\n    setprop vendor.fixture 1\n\non init\n    write /dev/stune/schedtune.boost 0\n'
 after,proof=o._native_cpu_config(before,hcb);action=b'    write /sys/devices/system/cpu/cpu1/online 1\n';assert after.replace(action,b'',1)==before and after.count(action)==1
 assert proof['original_physical_affinity']==[3,7]and proof['kernel_online_mask_written']is False and proof['watchdog_changed']is False and proof['original_HCB_changed']is False
 for services,source in [(after,hcb),(before,hcb[:-1]+bytes([hcb[-1]^1]))]:
  try:o._native_cpu_config(services,source)
  except ValueError:pass
  else:raise AssertionError('Nested or changed source accepted')
 with tempfile.TemporaryDirectory()as temp:
  root=Path(temp).resolve();q.ROOT=root;s=root/'var/hvf/prior';s.mkdir(parents=True);previous=dict(session=str(s),argv=[str(q.RUNNER),'probe','--output',str(s)])
  assert q.completed_android_seed(previous)is None
  def write(name,data):(s/name).write_text(json.dumps(data))
  write('integrity.json',dict(original_unchanged=True,exit_code=0));write('ufs-lun0-base-integrity.json',dict(unchanged_stat=True));image=s/'ufs-vh2-lun0-overlay.qcow2';image.write_bytes(b'HOST_METADATA_FIXTURE_NOT_QCOW2');write('android-ufs-overlay.json',dict(owner='VM4 / UFS VH2',base_read_only=True,path=str(image)));digest=hashlib.sha256(image.read_bytes()).hexdigest()
  assert q.completed_android_seed(previous)==s and hashlib.sha256(image.read_bytes()).hexdigest()==digest
  failed=root/'var/hvf/failed';failed.mkdir();checkpoint=dict(session=str(failed),argv=[str(q.RUNNER),'--output',str(failed)],android_seed=str(s))
  assert q.completed_android_seed(checkpoint)==s
  (failed/'launch.json').write_text('{}')
  try:q.completed_android_seed(checkpoint)
  except ValueError:pass
  else:raise AssertionError('Interrupted launched guest accepted as preparation failure')
  write('integrity.json',dict(original_unchanged=True,exit_code=1))
  try:q.completed_android_seed(previous)
  except ValueError:pass
  else:raise AssertionError('Unclean previous image accepted')
 seed=module('native_seed_stage',r/'src/emu/offline_android_seed.py')
 with tempfile.TemporaryDirectory()as temp:
  s=Path(temp).resolve();raw=b'MEBB_0123456789abcdef\n2000\n5.4.131-android11-2-ab151\n\nMEBE_0123456789abcdef:0\n';capture=s/'exynos-uart.bin';capture.write_bytes(raw)
  record=dict(guest='android',command='id -u; uname -r',session=str(s),capture=str(capture),begin='MEBB_0123456789abcdef',end='MEBE_0123456789abcdef',result=dict(exit=0,stdout='2000\n5.4.131-android11-2-ab151\n',raw_start=0,raw_end=len(raw),raw_sha256=hashlib.sha256(raw).hexdigest()))
  (s/'native-console-commands.jsonl').write_text(json.dumps(record)+'\n');assert seed._native_command_proof(s)
  capture.write_bytes(raw.replace(b'2000',b'0000'));assert not seed._native_command_proof(s)
 source=(r/'src/emu/offline_development.py').read_text();assert 'vendor_bytes,cpu_online=_native_cpu_config'in source and "'native_cpu_online':cpu_online"in source
 source=(r/'tools/hvf_qnx.py').read_text();assert "env['OEM_8704_OFFLINE_ANDROID_SEED']=str(previous_seed)"in source
 print('NATIVE_ANDROID_CPU_ONLINE_PASS original_HCB_two_core_binding=1 original_init_sysfs_request=1 no_kernel_mask_or_watchdog_write=1 immutable_inputs_preserved=1 prior_clean_private_Android_data_reused=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
