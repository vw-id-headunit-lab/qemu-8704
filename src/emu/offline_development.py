




import gzip,hashlib,json,os,stat
from pathlib import Path
import original_8704_vm as vm
from vlm_platform import parse_dtb,rebuild_fdt

PROFILE='android-private-unencrypted-v1'
SOURCE=vm.ROOT/'var/inputs/support/offline-dev-android'
ANDROID_ORIGINAL=vm.ROOT/'var/inputs/analysis/android'
ZYGOTE_RC='init.zygote64_32.rc'
ZYGOTE_PATH='system/etc/init/hw/'+ZYGOTE_RC
ZYGOTE_IMPORT='import /system/etc/init/hw/init.${ro.zygote}.rc\n'
ZYGOTE_SHA='de41ac40f00a29107f74b61485bf7807a5454860bbe67fb7b4cf98006c92c3bc'
VENDOR_PROP_SHA='e0a99739a6d4a1936e250b339b76a6489b567638c79a2bc0d7fbe07bbb83e517'
VENDOR_INIT_RC='/odm/etc/init/offline-vendor.rc'

def selected():
    value=os.environ.get('OEM_8704_OFFLINE_DEVELOPMENT','')
    if value and value!=PROFILE:raise ValueError('Unknown offline development profile')
    return bool(value)

def software_keystore_selected():
    value=os.environ.get('OEM_8704_OFFLINE_KEYSTORE','')
    if value and (value!='stock-software-v1' or not selected()):
        raise ValueError('Stock software keystore requires explicit private offline development')
    return bool(value)

def software_audio_selected():
    value=os.environ.get('OEM_8704_OFFLINE_AUDIO','')
    if value and (value!='stock-default-v1' or not selected()):
        raise ValueError('Original default audio requires explicit private offline development')
    return bool(value)

def art_interpreter_selected():
    value=os.environ.get('OEM_8704_OFFLINE_ART','')
    if value and (value!='no-jit-v1' or not selected()):
        raise ValueError('ART development option requires explicit offline no-jit-v1')
    return bool(value)

def policy(*,native=False,writable=False):
    if not selected():return None
    if not native or not writable:raise ValueError('Offline profile requires native three-domain session and private Android CoW')
    return {'profile':PROFILE,'default_enabled':False,'factory_boot_verified':False,
      'factory_security_verified':False,'hardware_backed_keys':False,'data_encrypted':False,
      'fresh_android_data_only':True,'original_partitions_changed':False,
      'tee_success_synthesized':False,'software_keymaster_hal_installed':False,
      'stock_keystore_software_fallback':software_keystore_selected(),
      'keystore_backend':'SOFTWARE' if software_keystore_selected() else 'original-vendor-TEE',
      'audio_backend':'ORIGINAL_DEFAULT_SOFTWARE' if software_audio_selected() else 'original-vendor-exynosauto9',
      'linux_smack_setup_available':False,'linux_masked_units':['smack-setup.service'],
      'art_jit_and_profile_saver_disabled':art_interpreter_selected(),
      'warning':'OFFLINE DEVELOPMENT — private unencrypted data; modified boot inputs; NOT factory secure boot'}

def _source(part,path):
    report=json.loads((SOURCE/'result.json').read_bytes())
    row=next(r for r in report['files']if r['partition']==part and r['path']==path)
    p=SOURCE/part/path.lstrip('/');b=p.read_bytes()
    if hashlib.sha256(b).hexdigest()!=row['sha256']:raise ValueError('Selected original config changed')
    return b

def _unencrypted(original):
    rows=original.decode('utf-8').splitlines(True);hits=0
    for i,line in enumerate(rows):
        fields=line.split()
        if len(fields)<5 or fields[0].startswith('#')or fields[1]!='/data':continue
        if fields[0]!='/dev/block/by-name/userdata' or fields[2]!='ext4':raise ValueError('Unknown userdata mount')
        flags=fields[4].split(',')
        if 'fileencryption=aes-256-xts:aes-256-cts:v2'not in flags or 'keydirectory=/metadata/vold/metadata_encryption'not in flags:raise ValueError('Unexpected original encryption policy')
        fields[3]=','.join(f for f in fields[3].split(',')if f!='inlinecrypt')
        fields[4]=','.join(f for f in flags if not f.startswith(('fileencryption=','keydirectory=')))
        rows[i]='    '.join(fields)+'\n';hits+=1
    if hits!=1:raise ValueError('Require one exact original userdata entry')
    return ''.join(rows).encode()

def _entry(name,data=b'',mode=stat.S_IFREG|0o644,inode=50000,rdev=(0,0)):
    n=name.encode('ascii')+b'\0';fields=[inode,mode,0,0,1,0,len(data),0,0,*rdev,len(n),0]
    b=b'070701'+b''.join(f'{v:08x}'.encode()for v in fields)+n
    b+=b'\0'*(-len(b)%4);b+=data;b+=b'\0'*(-len(b)%4);return b

def _zygote_restart_config(base, original, properties):
    if (base.count(ZYGOTE_IMPORT)!=1 or hashlib.sha256(original).hexdigest()!=ZYGOTE_SHA or
        hashlib.sha256(properties).hexdigest()!=VENDOR_PROP_SHA or
        properties.splitlines().count(b'ro.zygote=zygote64_32')!=1):
        raise ValueError('Unexpected original zygote restart configuration')
    restart=b'    onrestart restart netd\n'
    if original.count(restart)!=1 or original.index(restart)>original.index(b'\nservice zygote_secondary '):
        raise ValueError('Unknown primary zygote restart edge')
    return base.replace(ZYGOTE_IMPORT,'import /offline-dev/'+ZYGOTE_RC+'\n'),original.replace(restart,b'',1)

def _native_cpu_config(vendor,hcb):
    import struct
    if hashlib.sha256(hcb).hexdigest()!='cf218e39cb2afc9fd06b009198f53ca41112a12b52a89e1b2a868cf8716a6606':
        raise ValueError('Original Android CPU allocation differs')
    at=hcb.index(b'VDTHYP\0\0')+8
    tree=parse_dtb(hcb[at:at+struct.unpack_from('>I',hcb,at+4)[0]])
    props=tree['/vlm/vm@4/vcpus']
    if props.get('affinity')!=b'3,7\0'or props.get('migration')!=b'off\0':
        raise ValueError('Original two-core Android assignment differs')
    anchor=b'on init\n'
    action=b'    write /sys/devices/system/cpu/cpu1/online 1\n'
    if vendor.count(anchor)!=1 or action in vendor:
        raise ValueError('Android CPU staging already present or vendor init site differs')
    return vendor.replace(anchor,anchor+action),dict(
        logical_cpu=1,original_physical_affinity=[3,7],operation='original init sysfs CPU hotplug',
        path='/sys/devices/system/cpu/cpu1/online',requested_value=1,
        init_script=VENDOR_INIT_RC,execution_context='u:r:vendor_init:s0',
        kernel_online_mask_written=False,watchdog_changed=False,original_HCB_changed=False)

def adapt_ramdisk(original):
    from android_local_shell import _ramdisk_members
    members=_ramdisk_members(original)
    if any(n.startswith('offline-dev')or n in('offline-init','offline-odm.ext4','offline-loop0')for n in members):raise ValueError('Refuse nested development overlays')
    if not members.issuperset({'init','oem','odm'}):raise ValueError('Original init and development mountpoints required')
    base=_source('system_a','/system/etc/init/hw/init.rc').decode('utf-8')
    base,zygote=_zygote_restart_config(base,(ANDROID_ORIGINAL/'system_a'/ZYGOTE_PATH).read_bytes(),
                                      (ANDROID_ORIGINAL/'vendor_a/default.prop').read_bytes())
    apex_wait='    exec_start apexd-bootstrap\n'
    if base.count(apex_wait)!=1:raise ValueError('Unknown bootstrap APEX startup site')
    base=base.replace(apex_wait,'    wait_for_prop ro.cold_boot_done true\n'+apex_wait)
    if art_interpreter_selected():
                                                                            
                                                                             
        start='    start zygote\n'
        if base.count(start)!=3:raise ValueError('Unknown original zygote start actions')
        base=base.replace(start,'    setprop dalvik.vm.usejit false\n    setprop dalvik.vm.usejitprofiles false\n'+start)
    vendor=_source('vendor_a','/etc/init/init.exynosauto9.rc').decode('utf-8')
    needle='    exec_start wait_for_keymaster\n'
    if vendor.count(needle)!=1:raise ValueError('Require exact original keymaster wait site')
    vendor=vendor.replace(needle,'')
    if vendor.count('/vendor/etc/fstab.exynosauto9')!=2:raise ValueError('Unknown vendor mount_all sites')
    vendor=vendor.replace('/vendor/etc/fstab.exynosauto9','/odm/etc/fstab.offline-dev')
    fstab=_unencrypted(_source('vendor_a','/etc/fstab.exynosauto9'))
    inv=json.loads((vm.ROOT/'var/inputs/analysis/android-files.json').read_bytes())
    oem=[e for e in inv['system_a']['inventory']if e['path']=='/oem'or e['path'].startswith('/oem/')]
    if len(oem)!=1 or oem[0]['path']!='/oem'or not stat.S_ISDIR(oem[0]['type']):raise ValueError('Require original empty /oem mountpoint')
                                                                              
                                                                                 
    odm_children=[e for e in inv['system_a']['inventory']if e['path'].startswith('/odm/')]
    link_names={'app','bin','etc','firmware','framework','lib','lib64','overlay','priv-app','usr'}
    if {e['path']for e in odm_children}!={'/odm/'+n for n in link_names}or not all(stat.S_ISLNK(e['type'])for e in odm_children):raise ValueError('Unknown original ODM root')
    links={n:_source('system_a','/odm/'+n).decode('ascii')for n in sorted(link_names)}
    if any(t!='/vendor/odm/'+n for n,t in links.items()):raise ValueError('ODM link targets changed')
    odm_rows=[e for e in inv['vendor_a']['inventory']if e['path'].startswith('/odm/etc/')]
    odm_names={'NOTICE.xml.gz','build.prop','group','passwd'}
    if {e['path']for e in odm_rows}!={'/odm/etc/'+n for n in odm_names}or not all(stat.S_ISREG(e['type'])for e in odm_rows):raise ValueError('Unknown original ODM config set')
    audit=json.loads((SOURCE/'result.json').read_bytes())
    odm_files={}
    for n in sorted(odm_names):
        path='/odm/etc/'+n;row=next(e for e in audit['files']if e['partition']=='vendor_a'and e['path']==path)
        odm_files[n]=(_source('vendor_a',path),row['mode'])
    odm_files['fstab.offline-dev']=(fstab,0o644)
    if software_keystore_selected():
        odm_files['vintf/manifest.xml']=(b'<manifest version="2.0" type="device"><hal format="hidl" override="true"><name>android.hardware.keymaster</name><transport>hwbinder</transport></hal></manifest>\n',0o644)
    def original_label(part,path):
        row=next(e for e in audit['files']if e['partition']==part and e['path']==path)
        value=row.get('selinux_context')
        if not value:raise ValueError('Missing original ODM SELinux metadata: '+path)
        return value
    odm_labels={'/odm':original_label('system_a','/odm'),'/odm/etc':original_label('vendor_a','/odm/etc')}
    odm_labels.update({'/odm/'+n:original_label('system_a','/odm/'+n)for n in links if n!='etc'})
    odm_labels.update({'/odm/etc/'+n:original_label('vendor_a','/odm/etc/'+n)for n in odm_names})
    odm_labels['/odm/etc/fstab.offline-dev']=original_label('vendor_a','/etc/fstab.exynosauto9')
    init_context=original_label('vendor_a','/etc/init/init.exynosauto9.rc')
    odm_labels['/odm/etc/init']=init_context
    odm_labels[VENDOR_INIT_RC]=init_context
    if software_keystore_selected():
        context=original_label('vendor_a','/etc/vintf/manifest.xml')
        odm_labels['/odm/etc/vintf']=context
        odm_labels['/odm/etc/vintf/manifest.xml']=context
        from offline_software_keystore import build as software_keystore
        private_keystore,software_meta=software_keystore(_source('system_a','/system/bin/keystore'))
        original_rc=_source('system_a','/system/etc/init/keystore.rc')
        first=b'service keystore /system/bin/keystore /data/misc/keystore\n'
        if original_rc.count(first)!=1:raise ValueError('Require exact original keystore service')
        private_rc=original_rc.replace(first,b'service keystore /odm/etc/offline-keystore /data/misc/keystore\n    override\n')
        odm_files['offline-keystore']=(private_keystore,0o755)
        odm_files['init/offline-keystore.rc']=(private_rc,0o644)
        odm_labels['/odm/etc/offline-keystore']=original_label('system_a','/system/bin/keystore')
        context=original_label('vendor_a','/etc/init/init.exynosauto9.rc')
        odm_labels['/odm/etc/init']=context
        odm_labels['/odm/etc/init/offline-keystore.rc']=context
    imports=[]
    for e in inv['vendor_a']['inventory']:
        p=e['path']
        if p.startswith('/etc/init/')and p.count('/')==3 and p.endswith('.rc')and stat.S_ISREG(e['type']):
            imports.append(VENDOR_INIT_RC if p=='/etc/init/init.exynosauto9.rc'else'/vendor'+p)
    if imports.count(VENDOR_INIT_RC)!=1:raise ValueError('Original vendor rc inventory is incomplete')
    if software_keystore_selected():
        service='/vendor/etc/init/android.hardware.keymaster@4.0_tee-service.rc'
        if imports.count(service)!=1:raise ValueError('Require one original TEE keymaster service import')
        imports.remove(service)
                                                                                 
                                                                               
    base='import /offline-dev/services.rc\n'+base+'\n'
    base+='\n'.join('import '+p for p in ['/system/etc/init','/system_ext/etc/init','/product/etc/init','/odm/etc/init']+sorted(p for p in imports if p!=VENDOR_INIT_RC))+'\n'
    services='''on early-init
    write /dev/kmsg "OFFLINE DEVELOPMENT: unencrypted private data; no factory security claim"
    setprop sys.offline_development 1

on init
    start offline_dev_console

service offline_dev_console /system/bin/sh
    class core
    console
    disabled
    user shell
    group shell log readproc
    seclabel u:r:shell:s0
    setenv PS1 "console:/ $ "
'''.encode()
                                                                              
                                                                              
    base=base.replace('/offline-dev/','/oem/offline-dev/')
    services=services.replace(b'/offline-dev/',b'/oem/offline-dev/')
    if software_keystore_selected():
        token=b'    setprop sys.offline_development 1\n'
        if services.count(token)!=1:raise ValueError('Require one explicit development marker')
        services=services.replace(token,token+b'    setprop sys.offline_keystore SOFTWARE\n    write /dev/kmsg "OFFLINE KEYSTORE: SOFTWARE, original built-in implementation"\n')
    if software_audio_selected():
        token=b'    setprop sys.offline_development 1\n'
        if services.count(token)!=1:raise ValueError('Require one private development audio marker')
        services=services.replace(token,token+b'    setprop ro.hardware.audio.primary default\n    setprop sys.offline_audio ORIGINAL_DEFAULT_SOFTWARE\n    write /dev/kmsg \"OFFLINE AUDIO: original default software backend\"\n')
    from vlm_platform import original as platform_original
    vendor_bytes,cpu_online=_native_cpu_config(vendor.encode(),platform_original('hcb/hcb.img').read_bytes())
    from offline_preinit import build
    preinit,preinit_meta=build({'init.rc':base.encode(),'vendor-init.rc':vendor_bytes,'services.rc':services,'fstab.exynosauto9':fstab,ZYGOTE_RC:zygote},odm_files=odm_files,odm_links={n:t for n,t in links.items()if n!='etc'},odm_labels=odm_labels)
    preinit_meta['asm_sha256']=hashlib.sha256(preinit_meta.pop('asm').encode()).hexdigest()
    if software_keystore_selected():preinit_meta['software_keystore_routing']=software_meta
    from offline_odm_image import empty_ext4
    private_image=empty_ext4()
    entries=[('offline-init',preinit,stat.S_IFREG|0o750),('offline-odm.ext4',private_image,stat.S_IFREG|0o600)]
    for name in ('first_stage_ramdisk/fstab.exynosauto9','fstab.exynosauto9'):
        if name in members:entries.append((name,fstab,stat.S_IFREG|0o644))
    cpio=b''.join(_entry(n,b,m,50000+i)for i,(n,b,m)in enumerate(entries))+_entry('TRAILER!!!')
    result=original+gzip.compress(cpio,mtime=0)
    if not _ramdisk_members(result).issuperset(n for n,_,_ in entries):raise ValueError('Derived ramdisk did not roundtrip')
    return result,{'original_sha256':hashlib.sha256(original).hexdigest(),'derived_sha256':hashlib.sha256(result).hexdigest(),
      'overlay_members':[n for n,_,_ in entries],'fstab_sha256':hashlib.sha256(fstab).hexdigest(),
      'changed_init_site':'/vendor/etc/init/init.exynosauto9.rc:on late-fs:exec_start wait_for_keymaster',
      'zygote_restart':{'original_sha256':ZYGOTE_SHA,'private_sha256':hashlib.sha256(zygote).hexdigest(),
                        'removed_edge':'zygote -> restart netd','import':'/oem/offline-dev/'+ZYGOTE_RC},
      'security_scope':'Unencrypted empty development userdata, not replacement secure hardware or authenticated factory boot',
      'configuration_lifetime':'Private /oem tmpfs moved by original SwitchRoot; original empty mountpoint verified',
      'native_cpu_online':cpu_online,
      'preinit':preinit_meta,'preinit_interpreter':'No dynamic interpreter; direct Linux syscalls, then exec original /init',
      'fstab_selector':'androidboot.fstab_suffix=offline-dev; /odm/etc/fstab.offline-dev; no mount on immutable vendor files',
      'odm_original_files_preserved':[n for n in sorted(odm_names)],'odm_original_links_preserved':{n:t for n,t in links.items()if n!='etc'},
      'odm_etc_view':'Original /vendor/odm/etc link replaced in private RAM-backed ext4 by directory containing all four byte-identical originals plus explicit development fstab',
      'guest_selinux_enforcing_changed':False,
      'private_inode_original_selinux_labels':odm_labels,
      'label_staging':'Copy original xattrs to private RAM-backed ext4, clean unmount/remount to drop pre-policy SID caches; original unchanged policy initializes labels from persistent xattrs. No late relabel or policy override',
      'private_odm_filesystem':{'type':'ext4','bytes':len(private_image),'sha256':hashlib.sha256(private_image).hexdigest(),'loop':0,'handed_off_read_only':True,'original_media_changed':False},
      'original_image_modified':False,'secure_return_patched':False}

def adapt_dtb(raw):
    nodes=parse_dtb(raw);old=nodes['/chosen']['bootargs']
    if not old.endswith(b'\0')or b'\0'in old[:-1]:raise ValueError('Invalid original command line')
    text=old[:-1].decode('ascii')
    if text.split().count('loglevel=3')!=1:raise ValueError('Expected original bounded console log level')
    level=os.environ.get('OEM_8704_OFFLINE_BOOT_LOGLEVEL','3')
    if level not in ('3','7'):raise ValueError('Explicit offline console loglevel must be3 or7')
    text=text.replace('loglevel=3','loglevel='+level)
    if any(t.partition('=')[0]in('rdinit','androidboot.init_rc','androidboot.offline_development','androidboot.fstab_suffix')for t in text.split()):raise ValueError('Existing development boot policy')
    text+=' rdinit=/offline-init androidboot.init_rc=/oem/offline-dev/init.rc androidboot.offline_development=1 androidboot.fstab_suffix=offline-dev'
    nodes['/chosen']['bootargs']=text.encode()+b'\0';new=rebuild_fdt(raw,nodes)
    if parse_dtb(new)!=nodes:raise ValueError('Development DT roundtrip failed')
    return new,{'original_sha256':hashlib.sha256(raw).hexdigest(),'derived_sha256':hashlib.sha256(new).hexdigest(),
      'bootargs_before':old[:-1].decode('ascii'),'bootargs_after':text,'changed_properties':['/chosen/bootargs'],
      'kernel_changed':False,'factory_secure_boot':False,'console_log_level':int(level)}


def adapt_linux_dtb(raw):

    from dissect.extfs import ExtFS
    from linux_weston_platform import ROOT_MEMBER
    from vlm_platform import factory_file
    root=factory_file(ROOT_MEMBER)
    expected={'/lib/systemd/system/smack-setup.service':'', '/etc/smack_setup.sh':''}
                                                                            
                                                                       
    with root.open('rb')as stream:
        fs=ExtFS(stream)
        for name in expected:
            original=fs.get(name).open().read(65536)
            copy=(vm.ROOT/'var/inputs/analysis/linux-root'/name.lstrip('/')).read_bytes()
            if original!=copy:raise ValueError('Selected RPMB consumer differs from original media: '+name)
            expected[name]=hashlib.sha256(original).hexdigest()
            if name.endswith('.service')and b'ExecStart=/etc/smack_setup.sh\n'not in original:raise ValueError('Unknown RPMB consumer unit')
            if name.endswith('.sh')and b'rpmb=$(cat /sys/devices/platform/common_gpio_default/rpmb)'not in original:raise ValueError('Unknown RPMB read site')
    nodes=parse_dtb(raw);old=nodes['/chosen']['bootargs']
    if not old.endswith(b'\0')or b'\0'in old[:-1]or b'systemd.mask=smack-setup.service'in old:raise ValueError('Invalid or nested Linux offline policy')
    nodes['/chosen']['bootargs']=old[:-1]+b' systemd.mask=smack-setup.service\0'
    new=rebuild_fdt(raw,nodes)
    if parse_dtb(new)!=nodes:raise ValueError('Linux offline DT roundtrip failed')
    return new,{'original_sha256':hashlib.sha256(raw).hexdigest(),'derived_sha256':hashlib.sha256(new).hexdigest(),
      'masked_unit':'smack-setup.service','verified_original_files':expected,'smack_setup_available':False,
      'security_state':'Unavailable; not a successful RPMB read or completed SMACK configuration',
      'kernel_hung_task_detection_changed':False,'original_image_modified':False}
