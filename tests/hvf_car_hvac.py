from pathlib import Path
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[1];binary=Path(sys.argv[1]).resolve();root=binary.parents[2];label=sys.argv[2];sys.path.insert(0,str(root/'src/hvf_bench/src'));record=dict(label=label,input='Same zh_CN boot HVAC payloads; exact real car52objects/UUIDs/references, right22C ACoff/frontoff; no synthetic field expansion')
try:
 from hmi_bench.fixtures import payloads
 result=payloads('zh_CN');values={name.split('/')[1][:-5]:json.loads(b)for name,b in result.items()if name.startswith('hvac/')};rows=values['rows'];record.update(row_names=[x['name']for x in rows],row_ids=[x['id']for x in rows],objects=sum(len(x)for x in values.values()))
 assert len(rows)==1 and rows[0]['id']=='c5490902-793b-41f6-bdbe-9a6bf5f11caf','Synthetic HVAC still selected'
 source=json.loads((ROOT/'src/hvf_bench/local/car-hvac-capture.json').read_text());assert values==source['objects'],'Captured HVAC graph was altered'
 def by_name(name):return next(v for rows in values.values()for v in rows if v.get('name')==name)
 right=by_name('frontRowRightSideClimatisationZoneTemperatureValueControlObject');ac=by_name('generalSettingsAcSwitchControlObject');activation=by_name('frontRowSystemActivationSwitchControlObject');record.update(right_temperature=right['currentValue'],ac=ac['switchValue'],front_activation=activation['switchValue']);assert right['currentValue']==22 and ac['switchValue']=='off'and activation['switchValue']=='off';assert record['objects']==52
 record['unresolved_intelligent_control_references']=source['unresolved_references'];assert len(source['unresolved_references'])==7
 record.update(exit=0,scope='Actual generated boot payload matches captured graph byte-value semantics; native HTTP and HMI acceptance separate')
except Exception as e:record.update(exit=1,error=repr(e))
(ROOT/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
