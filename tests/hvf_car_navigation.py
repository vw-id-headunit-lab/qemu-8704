from pathlib import Path
import ast,hashlib,importlib.util,json,sys
sys.dont_write_bytecode=True
r=Path.cwd();root=Path(sys.argv[1]).resolve();label=sys.argv[2];record=dict(label=label,input='Same configured 8704 HMI make_argv with Nanjing car map and native OOM memory headroom')
try:
 p=root/'src/hvf_bench/src/hmi_bench/config.py';spec=importlib.util.spec_from_file_location('car_cfg_'+label,p);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);c=m.Config.load(root/'config/hvf-8704.json')
 sys.path.insert(0,str(root/'src/hvf_bench/src'));from hmi_bench.launcher import make_argv;ns={'make_argv':make_argv};argv=ns['make_argv'](c,'hmi',Path('/same-input-initramfs'));baseline=ns['make_argv'](c,'baseline',Path('/same-input-initramfs'));mem=int(argv[argv.index('-m')+1]);mp=c.paths.get('navigation_map');record.update(hmi_memory_mib=mem,baseline_memory_mib=int(baseline[baseline.index('-m')+1]),map=str(mp)if mp else None,argv=argv)
 assert mem==3072,'HMI still has native-OOM 2GiB allocation';assert baseline[baseline.index('-m')+1]=='1024';assert mp and mp.is_file(),'No car map carrier selected'
 drive='file='+str(mp)+',format=raw,if=none,id=bench-navi-car-map,readonly=on';assert drive in argv;assert argv.count('virtio-blk-device,drive=bench-navi-car-map,serial=bench-navi-map')==1;assert not any('"node-name": "bench-navi-map"'in x for x in argv)
 from dissect.extfs import ExtFS
 with mp.open('rb')as f:
  fs=ExtFS(f);data=fs.get('/NDS/ROOT.NDS').open().read();road=fs.get('/NDS/CORE/3201_1901_1911/ROUTING.NDS');record.update(root_sha256=hashlib.sha256(data).hexdigest(),root_bytes=len(data),routing_bytes=road.size);assert len(data)==12288 and road.size==52858880
 record.update(exit=0,map_read_only=True,scope='Actual launcher and map carrier content; live original navigation roads acceptance is separate')
except Exception as ex:record.update(exit=1,error=repr(ex))
(r/'.local/mac-port/transaction'/('car-navigation-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
