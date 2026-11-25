import importlib.util,json,sys,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'src/hvf_bench/src'))
p=Path(sys.argv[1])if len(sys.argv)>1 else root/'src/hvf_bench/src/hmi_bench/fixtures.py'
try:
 s=importlib.util.spec_from_file_location('hmi_bench.display_input_test',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
 result=[]
 for lang in ['en_US','zh_CN']:
  values=m.payloads(lang);rows=json.loads(values['displaymanagement/displays.json']);assert len(rows)==1;v=rows[0];assert (v['width'],v['height'],v['controlClient'])==(1920,1080,'HMI');assert set(v)=={'id','name','uri','width','height','controlClient'}
  result.append(dict(language=lang,display=v))
 out=dict(source=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),displays=result,scope='Actual virtual display geometry only; no navigation lifecycle or readiness override',exit=0)
except Exception as e:out=dict(source=str(p),error=repr(e),exit=2)
print(json.dumps(out,ensure_ascii=False,indent=2));raise SystemExit(out['exit'])
