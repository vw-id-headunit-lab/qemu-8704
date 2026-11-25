from pathlib import Path
import sys,json,os,importlib.util,hashlib,tempfile,struct
r=Path.cwd();sys.path[:0]=[str(r/'src/emu'),str(r/'tools')];import vlm_platform
from macos_center import profile
_,env=profile();env.pop('OEM_8704_QNX_HUD_STARTUP',None);env['MEB_QNX_GL_ABI_PROBE']='original-loader-v1';os.environ.update(env)
target=Path(sys.argv[1]).resolve();label=sys.argv[2];record={'target':str(target),'label':label,'input':'Exact original8704 IFS loader + private original-loader-v1 profile, original IID/ARHUD; stage and reparse all entries/checksums','sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
try:
 spec=importlib.util.spec_from_file_location('qnx_loader_test_target',target);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 source=vlm_platform.factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn');before=hashlib.sha256(source.read_bytes()).hexdigest();command=[f'loader,file={source},addr=0x91080000,force-raw=on']
 with tempfile.TemporaryDirectory(prefix='qnx-gl-loader-test-',dir=r/'var/sessions')as d:
  result=m.stage(command,d,'iid-arhud');record.update(result=result,command=command,source_hash_unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==before)
  assert record['source_hash_unchanged'];assert result and result.get('profile')=='original-loader-v1','new loader probe not staged'
  import qnx_gl_forward as gl
  raw=Path(result['private_ifs']).read_bytes();entries,_,_=gl.directory(raw);names={x['name']for x in entries};assert gl.SCRIPT in names and gl.LIBRARY in names;assert not gl.checksum(raw[gl.STARTUP:gl.IMAGEFS]);assert not gl.checksum(raw[gl.IMAGEFS:]);record['exit']=0
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-gl-loader-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
