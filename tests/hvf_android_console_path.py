from pathlib import Path
import importlib.util,json,sys
root=Path(__file__).resolve().parents[1];base=Path(sys.argv[1]);record={'source':str(base.resolve()),'input':'Same actual Android guest command id && uname -m at UID2000 plus original sys.boot_completed read'}
for name in ['hvf_console','hvf_guest_command']:
 spec=importlib.util.spec_from_file_location(name,base/'tools'/(name+'.py'));module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);module.ROOT=root
try:
 out=module.run('android','id && uname -m');record['result']=out;assert out['exit']==0 and out['uid']==2000 and 'uid=2000(shell)'in out['stdout']and 'aarch64'in out['stdout']and out['framework_boot_completed']is True;record['exit']=0
except Exception as error:record.update(error=repr(error),exit=2)
print(json.dumps(record,ensure_ascii=False,indent=2));raise SystemExit(record['exit'])
