from pathlib import Path
import importlib.util,json,os,sys
from unittest.mock import patch
repo=Path(__file__).resolve().parents[1];root=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else repo
try:
 spec=importlib.util.spec_from_file_location('native_trace_profile',root/'tools/macos_center.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ROOT=repo
 for value in ['', 'stop-writer-v1']:
  with patch.dict(os.environ,{'OEM_8704_VM_LIFECYCLE_TRACE':value},clear=True):args,env=m.profile()
  assert env.get('OEM_8704_VM_LIFECYCLE_TRACE')==('stop-writer-v1'if value else None)
  assert '--native-parallel-tcg'in args and '--load-android'in args and '--load-linux'in args and '--load-qnx'in args
  assert env['OEM_8704_NATIVE_LIFETIME']=='until-stop-v1'
 with patch.dict(os.environ,{'OEM_8704_VM_LIFECYCLE_TRACE':'unknown'},clear=True):
  try:m.profile()
  except ValueError:pass
  else:raise AssertionError('Unknown lifecycle diagnostic profile accepted')
 text=(repo/'src/emu/vlm_offline_live_darwin.py').read_text();assert "if lifecycle_trace:"in text and "command[command.index('-d')+1]+=',exec,cpu'"in text
 print('NATIVE_LIFECYCLE_TRACE_PASS default_exec_cpu_trace_off=1 explicit_original_stop_trace_preserved=1 native_domains_unchanged=1 diagnostic_profile_rejected_if_unknown=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
