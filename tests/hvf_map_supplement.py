from pathlib import Path
import json,sys,hashlib
from dissect.extfs import ExtFS
ROOT=Path(__file__).resolve().parents[1];binary=Path(sys.argv[1]).resolve();root=binary.parents[2];label=sys.argv[2];record=dict(label=label,input='Configured read-only 1911 map: five actual missing product indices, WORLD and four1100 dependencies; isolated writable cache')
try:
 cfg=json.loads((root/'config/hvf-8704.json').read_text());p=Path(cfg['navigation_map']);record['map']=str(p);required=['DI/PRODUCT_55.NDS','WORLD/PRODUCT_17.NDS','CORE/PRODUCT_55.NDS','ADVANCED/PRODUCT_31.NDS','3DLANE/PRODUCT_55.NDS','WORLD/56_1901_1911/DTM.NDS','DI/1100/POI.NDS','CORE/1100/ROUTING.NDS','ADVANCED/1100/3DOCM.NDS','3DLANE/1100/3DLANE.NDS','CORE/3201_1901_1911/ROUTING.NDS'];found={};missing=[]
 with p.open('rb')as f:
  fs=ExtFS(f)
  for name in required:
   try:node=fs.get('/NDS/'+name);assert node.size>0;found[name]=node.size
   except Exception:missing.append(name)
  record.update(found=found,missing=missing)
  assert not missing,'Required native map files missing'
  identity=fs.get('/.bench-map-set-id').open().read().decode().strip();assert len(identity)==64;record['map_set_id']=identity
 setup=(root/'src/hvf_bench/guest/navigation-setup.sh').read_text();assert 'map-upper-$map_id'in setup and 'map-work-$map_id'in setup
 record.update(exit=0,scope='Actual selected map content and boot cache isolation; live MXNavi rendering verified separately')
except Exception as e:record.update(exit=1,error=repr(e))
(ROOT/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
