from pathlib import Path
import hashlib,importlib.util,json,os,shlex,subprocess,sys,tempfile

ROOT=Path(__file__).resolve().parents[1]
source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else ROOT
sys.path[:0]=[str(source/'src/emu'),str(source/'src/hvf_bench/src')]
result=dict(scope='Execute declared language preparation from the real generated shell; exact canonical data shared with existing center, original RSI executable and unit preserved',source=str(source))
try:
 spec=importlib.util.spec_from_file_location('language_bench_test',source/'src/emu/offline_bench_hmi_darwin.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 if not hasattr(module,'language_records'):raise ValueError('Native declared language is not staged')
 for name in list(os.environ):
  if name.startswith('OEM_8704_'):os.environ.pop(name)
 os.environ.update(OEM_8704_OFFLINE_DEVELOPMENT='android-private-unencrypted-v1',OEM_8704_OFFLINE_BENCH='virtual-bench-v1',OEM_8704_OFFLINE_BENCH_POWER='factory-rsi-power-v1',OEM_8704_OFFLINE_BENCH_LANGUAGE='zh_CN')
 records=module.language_records();assert set(records)=={'settings','requestedSettings','changeTransactions','strategies','components'}
 expected={'changeTransactions':'37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570','requestedSettings':'37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570','components':'12c495f3daa2fb5884505fbf4998630120df57f7c48c09924e59352bffaa6623','settings':'58beb198a9b97290f81698e0913e51026eed15be6c377931ed8622f33d1e174c','strategies':'0b69882765ad11a9f6d164f3b4ae256434f8f26d27f7b5dda0a9778cc1e69004'}
 assert {k:hashlib.sha256(v).hexdigest()for k,v in records.items()}==expected
 from dissect.extfs import ExtFS
 with(ROOT/'var/inputs/analysis/cpu/ivi/icas3cn-gp-image-euto-v9.ext4').open('rb')as image:
  spec_bytes=ExtFS(image).get('/lge/app_ro/sai/spec/viwi.service.language/viwi.service.language-1.4.0.json').open().read()
 assert hashlib.sha256(spec_bytes).hexdigest()=='cf39d4bcd88d1071756faa1e8f1c337aa28cbfadf2945031711ba05e3872cf25'
 schema=json.loads(spec_bytes)
 for name,data in records.items():
  if name not in schema['resources']:
   assert name=='changeTransactions'and json.loads(data)==[]
   continue
  properties=schema['resources'][name]['model']['properties']
  for item in json.loads(data):
   assert set(item)<=set(properties),(name,set(item)-set(properties))
   for field,value in item.items():
    rule=properties[field]
    if 'enum'in rule:assert value in rule['enum'],(name,field,value)
 nodes={'/chosen':{}};unit=module.power_stage(nodes);chosen=nodes['/chosen'];script=chosen['oem-bench-power'].decode()
 assert 'ExecStart=/lge/app_ro/bin/rsi-mockup -rsi-mockup.dir=/run/oem-bench-power -rsi-log_console=true' in unit
 assert not any(s in unit for s in('server.port=','Type=','User=','FailureAction=','WatchdogSec='))
 for key,raw in records.items():assert chosen['oem-bench-language-'+key]==raw
 start=script.index('mkdir -p /run/oem-bench-power/language\n');end=script.index('test ! -e /run/oem-bench-power/vehicledynamics/componentProtections.json',start);block=script[start:end]
 with tempfile.TemporaryDirectory(prefix='native-language-data-')as d:
  d=Path(d);data=d/'power';data.mkdir();(data/'services').write_text('vehicledynamics:system\n');chosen_dir=d/'chosen';chosen_dir.mkdir()
  for key,raw in records.items():(chosen_dir/('oem-bench-language-'+key)).write_bytes(raw)
  replay=block.replace('/run/oem-bench-power',str(data)).replace('/sys/firmware/devicetree/base/chosen',str(chosen_dir)).replace('/usr/bin/sha256sum','/usr/bin/shasum -a 256');p=subprocess.run(['sh','-eu','-c',replay],capture_output=True,text=True);assert p.returncode==0,(p.stdout,p.stderr)
  assert (data/'services').read_text()=='vehicledynamics:system\nlanguage:system\n'
  for key,raw in records.items():assert (data/'language'/(key+'.json')).read_bytes()==raw
  component=json.loads(records['components'])[0];assert component['type']=='graphicalUserInterface'and component['state']=='idle'and component['currentSetting']['language']=='zh-cn'
  assert json.loads(records['requestedSettings'])==[]and json.loads(records['changeTransactions'])==[]
  result.update(exit=0,stdout=p.stdout,stderr=p.stderr,preparation_exit=p.returncode,resource_hashes=expected,service='language:system',current_language='zh-cn',original_language_1_4_schema_sha256=hashlib.sha256(spec_bytes).hexdigest(),schema_resources_checked=sorted(schema['resources']),legacy_empty_resources=['changeTransactions'],mutated_readiness_or_authentication=False)
 os.environ['OEM_8704_OFFLINE_BENCH_LANGUAGE']='unsupported'
 try:module.language_selected()
 except ValueError:result['unsupported_language_rejected']=True
 else:raise AssertionError('Unknown language profile admitted')
except Exception as error:result.update(exit=2,stdout='',stderr=repr(error))
print(json.dumps(result,indent=2));raise SystemExit(result['exit'])
