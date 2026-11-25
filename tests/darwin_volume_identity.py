from pathlib import Path
import importlib.util,json,sys
r=Path(__file__).resolve().parents[1];source=Path(sys.argv[1])if len(sys.argv)>1 else r/'src/emu/ufs_virtual_disk_darwin.py';sys.path.insert(0,str(r/'src/emu'))
try:
 spec=importlib.util.spec_from_file_location('volume_identity',source);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 prior=dict(base='/DECLARED/immutable.raw',stat=[17,900,4096,1000000],gpt_regions_verified=5,archive_payloads_verified=[dict(slot=14,bytes=2048,sha256='a'*64)],profile='original-archive-v1')
 current=json.loads(json.dumps(prior));current['stat'][0]=18;m.sys.platform='darwin';assert m.verified_base_identity(prior,current)
 for field in [1,2,3]:
  q=json.loads(json.dumps(current));q['stat'][field]+=1;assert not m.verified_base_identity(prior,q)
 for key,value in [('gpt_regions_verified',0),('archive_payloads_verified',[]),('profile','other'),('base','/OTHER/immutable.raw'),('stat',None)]:
  q=json.loads(json.dumps(current));q[key]=value;assert not m.verified_base_identity(prior,q)
 m.sys.platform='linux';assert not m.verified_base_identity(prior,current);assert m.verified_base_identity(prior,prior)
 result=dict(scope='Exact verified archive identity accepts Darwin device-number re-enumeration only; inode/size/mtime/path/GPT/payload/profile changes rejected',exit=0)
except Exception as e:result=dict(error=repr(e),exit=2)
print(json.dumps(result,indent=2));raise SystemExit(result['exit'])
