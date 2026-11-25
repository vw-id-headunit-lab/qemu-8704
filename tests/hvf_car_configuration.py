from pathlib import Path
import hashlib,importlib.util,json,sys
r=Path.cwd();root=Path(sys.argv[1]).resolve();label=sys.argv[2];record=dict(label=label,input='Same supplied 8704 HMI vehicle profile, all exported config/Data bytes and Ric ID3 resource key')
try:
 profile=json.loads((root/'src/hvf_bench/local/vehicle-profile.json').read_text());record.update(registered=bool(profile.get('hmi_configuration_manifest')),encoded_vehicle_type=profile['encoded_vehicle_type']);assert record['registered'],'Car HMI configuration export is not registered'
 path=root/'src/hvf_bench/src/hmi_bench/car_configuration.py';s=importlib.util.spec_from_file_location('car_configuration_'+label,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);checks=m.checksums(profile);rows=checks.decode().splitlines();record['files']=len(rows);assert len(rows)==151;assert profile['encoded_vehicle_type']=='020301006300000002000';assert sum('/Climate/climate.clm'in x for x in rows)==12
 archive=(root/'src/hvf_bench/src/hmi_bench/archive.py').read_text();boot=(root/'src/hvf_bench/guest/hmi-init.sh').read_text();assert 'bench-car-configuration.sha256'in archive and 'bench-car-configuration.sha256'in boot;assert 'sha256sum -c'in boot
 record.update(exit=0,checklist_sha256=hashlib.sha256(checks).hexdigest(),climate_layouts=12,source_files_verified=True,scope='Registered original exported file bytes and automatic startup identity check; current guest identical-file proof recorded separately')
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('car-hmi-config-contract-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
