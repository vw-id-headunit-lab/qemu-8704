from pathlib import Path
import hashlib,importlib.util,json,sys
repo=Path(__file__).resolve().parents[1];root=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else repo
try:
 sys.path[:0]=[str(repo/'tools'),str(repo/'src/emu'),str(repo/'src/firmware')]
 def module(name,path):
  spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
 p=module('offline_preinit',root/'src/emu/offline_preinit.py');sys.modules['offline_preinit']=p
 o=module('vendor_context_stage',root/'src/emu/offline_development.py')
 import vlm_offline_live_darwin as v
 kernel,generic,vendor=v.vm.android_parts(v.factory_file('android/boot.img').read_bytes(),v.factory_file('android/vendor_boot.img').read_bytes())
 captured={};original_build=p.build
 def build(files,**kw):
  captured.update(files=files,**kw);return original_build(files,**kw)
 p.build=build
 derived,meta=o.adapt_ramdisk(vendor+generic);paths=meta['preinit']['configuration_paths'];target='/odm/etc/init/offline-vendor.rc'
 assert meta['preinit']['vendor_init_script']==target and meta['preinit']['vendor_init_context']=='u:r:vendor_init:s0'and meta['preinit']['selinux_policy_changed']is False
 assert paths.count(target)==1 and '/oem/offline-dev/vendor-init.rc'not in paths
 assert b'import /odm/etc/init\n'in captured['files']['init.rc'] and b'/vendor/etc/init/init.exynosauto9.rc'not in captured['files']['init.rc'] and b'/oem/offline-dev/vendor-init.rc'not in captured['files']['init.rc']
 action=b'    write /sys/devices/system/cpu/cpu1/online 1\n';assert action not in captured['files']['services.rc'] and captured['files']['vendor-init.rc'].count(action)==1
 expected=o._source('vendor_a','/etc/init/init.exynosauto9.rc').replace(b'    exec_start wait_for_keymaster\n',b'').replace(b'/vendor/etc/fstab.exynosauto9',b'/odm/etc/fstab.offline-dev');assert captured['files']['vendor-init.rc'].replace(action,b'',1)==expected
 audit=json.loads((o.SOURCE/'result.json').read_bytes());label=next(x['selinux_context']for x in audit['files']if x['partition']=='vendor_a'and x['path']=='/etc/init/init.exynosauto9.rc')
 assert captured['odm_labels'][target]==label and captured['odm_labels']['/odm/etc/init']==label
 assert meta['native_cpu_online']['execution_context']=='u:r:vendor_init:s0' and meta['native_cpu_online']['init_script']==target
 assert meta['original_image_modified']is False and meta['guest_selinux_enforcing_changed']is False
 print(json.dumps(dict(scope='Actual original ramdisk derivation; source validation, not boot acceptance',original_kernel_sha256=hashlib.sha256(kernel).hexdigest(),derived_bytes=len(derived),derived_sha256=hashlib.sha256(derived).hexdigest(),vendor_init_path=target,original_vendor_rc_bytes_preserved_except_recorded_offline_mount_and_cpu_actions=True,original_labels_preserved=True,policy_changed=False)))
 print('NATIVE_VENDOR_INIT_CONTEXT_PASS vendor_directory_parser_context=1 original_vendor_actions_preserved=1 cpu_action_not_in_system_init=1 no_policy_or_immutable_image_write=1');raise SystemExit(0)
except Exception as error:print(json.dumps(dict(exit=2,error=repr(error))));raise SystemExit(2)
