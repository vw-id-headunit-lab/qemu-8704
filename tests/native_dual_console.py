from pathlib import Path
import hashlib,importlib.util,json,shlex,sys
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'tools'));p=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'tools/native_guest_command.py';result={'source':str(p),'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'commands':[]}
module=None
try:
 spec=importlib.util.spec_from_file_location('native_backend_under_test',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 expected='NATIVE_SOURCE_中文_quoted_\'"';long_source="printf '%s\\n' "+shlex.quote(expected)+'\n:\t'+shlex.quote('exact-plain-source'*50)+'\n'
 for guest,uid in [('linux',65534),('android',2000)]:
  for command in ['id && uname -r',long_source]:
   reply=module.run(guest,command);result['commands'].append(reply)
   if command==long_source:assert reply['stdout']==expected+'\n' and reply['exit']==0,reply
   else:assert 'uid='+str(uid)in reply['stdout']and reply['exit']==0,reply
 from hvf_qnx import owner
 s,pid=owner()
 for reply in result['commands']:
  assert reply['session']==str(s)and reply['qemu_pid']==pid and reply['kernel'].startswith('5.4.131')and reply['transport'].startswith('original-native-'),reply
  capture=Path(reply['capture']);raw=capture.read_bytes()[reply['raw_start']:reply['raw_end']];assert hashlib.sha256(raw).hexdigest()==reply['raw_sha256']
 result['exit']=0
except Exception as e:result.update(exit=2,error=repr(e))
finally:
 if module is not None and hasattr(module,'close'):module.close()
print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(result['exit'])
