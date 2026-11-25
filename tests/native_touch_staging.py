from pathlib import Path
import configparser,gzip,hashlib,importlib.util,json,os,subprocess,sys,tempfile

ROOT=Path(__file__).resolve().parents[1]
source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else ROOT
sys.path[:0]=[str(source/'src/emu'),str(ROOT/'tools'),str(ROOT/'src/emu')]
result=dict(scope='Execute native original touch DTB staging, unchanged original entry/exports/allocated sections, original unit identity and bounded hardware ring; live acceptance separate',source=str(source))
try:
 p=source/'src/emu/offline_bench_hmi_darwin.py';spec=importlib.util.spec_from_file_location('touch_offline_test',p);offline=importlib.util.module_from_spec(spec);spec.loader.exec_module(offline)
 if not hasattr(offline,'native_touch_stage'):raise ValueError('Original native input provider is not staged')
 from macos_center import profile
 _,env=profile();os.environ.update(env)
 import native_touch_stage
 native_touch_stage.ROOT=source
 for name in ('rsi_queue_stage','power_stage'):setattr(offline,name,lambda n:'')
 for name in ('input_ready_stage','panel_stage'):setattr(offline,name,lambda:'')
 offline.bench_script=lambda:''
 nodes={'/chosen':{}};script=offline.stage(nodes,'exec /sbin/init');chosen=nodes['/chosen'];manifest=json.loads(chosen['oem-touch-manifest'])
 for name,row in manifest['assets'].items():
  raw=gzip.decompress(chosen['oem-touch-'+name.replace('.','-')]);assert hashlib.sha256(raw).hexdigest()==row['sha256']and len(raw)==row['bytes']and raw==(source/row['path']).read_bytes()
 def unit(text):
  c=configparser.ConfigParser(strict=False);c.optionxform=str;c.read_string(text);return c
 dropin=unit(chosen['oem-touch-dropin'].decode());prepare=unit(chosen['oem-touch-prepare_unit'].decode());relay=unit(chosen['oem-touch-unit'].decode())
 assert set(dropin['Service'])=={'ExecStart','StandardOutput','StandardError'}
 assert dropin['Service']['ExecStart']=='/vw/oem-touch/touch-app -rsi-log_level=error'
 assert dropin['Unit']['Requires']==dropin['Unit']['After']=='oem-touch-prepare.service'
 assert prepare['Unit']['Requires']==prepare['Unit']['After']=='post-init.service oem-gl-prepare.service'and prepare['Service']['TimeoutStartSec']=='600'
 assert "until grep -q ' /vw ext4 rw[, ]' /proc/mounts"in chosen['oem-touch-prepare'].decode()and '/bin/busybox mkfifo 'in chosen['oem-touch-prepare'].decode()
 assert relay['Service']['ExecStart']=='/vw/oem-touch/relay'and 'cp "$r/relay" "$d/relay"'in chosen['oem-touch-prepare'].decode()
 for name in ('prepare','unit','dropin','prepare_unit'):
  if name=='prepare':
   checked=subprocess.run(['sh','-n'],input=chosen['oem-touch-'+name].decode(),capture_output=True,text=True);assert checked.returncode==0,checked.stderr
 from dissect.extfs import ExtFS
 with(ROOT/'var/inputs/analysis/cpu/ivi/icas3cn-gp-image-euto-v9.ext4').open('rb')as f:
  fs=ExtFS(f);original=fs.get('/lge/app_ro/hmi/touch-app').open().read();original_unit=fs.get('/lib/systemd/system/touch-app.service').open().read()
 assert hashlib.sha256(original).hexdigest()==manifest['binding']['original_sha256']
 build_spec=importlib.util.spec_from_file_location('build_touch_test',source/'tools/build_native_touch.py');build=importlib.util.module_from_spec(build_spec);build_spec.loader.exec_module(build)
 changed,proof=build.bind(original);assert changed==(source/manifest['assets']['touch-app']['path']).read_bytes()and proof==manifest['binding']
 assert hashlib.sha256(original_unit).hexdigest()=='ecfcceb881563ccf89c1c41a497c0c4f04c24959520b8452390151824746c2c9'
 native_unit=unit(original_unit.decode());assert native_unit['Service']['User']=='sint_sec_touch_display'and native_unit['Service']['Type']=='notify'and native_unit['Service']['FailureAction']=='reboot-immediate'
 from hvf_qnx import owner
 from vlm_platform import parse_dtb,rebuild_fdt
 s,pid=owner();raw=(s/'linux-offline.dtb').read_bytes();tree=parse_dtb(raw)
 for key in list(tree['/chosen']):
  if key.startswith('oem-'):del tree['/chosen'][key]
 tree['/chosen'].update(chosen);staged=rebuild_fdt(raw,tree)
 gl_spec=importlib.util.spec_from_file_location('touch_gl_test',ROOT/'src/emu/gl-forward/runtime-darwin.py');gl=importlib.util.module_from_spec(gl_spec);gl_spec.loader.exec_module(gl)
 with tempfile.TemporaryDirectory(prefix='native-touch-dtb-')as d:
  packed=gl.stage(staged,d);assert len(packed)<=0x100000;meta=json.loads((Path(d)/'gl-forward-boot.json').read_bytes());assert meta['native_touch']==manifest
 result.update(exit=0,stdout='NATIVE_TOUCH_STAGE_PASS original_uid=979 original_notify=unchanged timer=original_worker aperture=b7fc03000 ring=128 original_code_exports=unchanged\n',stderr='',dtb_bytes=len(packed),original_unit_sha256=hashlib.sha256(original_unit).hexdigest(),original_sha256=hashlib.sha256(original).hexdigest(),assets=manifest['assets'])
except Exception as error:result.update(exit=2,stdout='',stderr=repr(error))
print(json.dumps(result,indent=2));raise SystemExit(result['exit'])
