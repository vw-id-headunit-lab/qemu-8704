
import json
from pathlib import Path

TTY = '/dev/ttySAC0'
DIRECT_TTY = '/dev/ttySAC3'
DIRECT_SOURCE = 'dedicated-linux-usi03'
DIRECT_CHR = 'oem-linux-usi03'
DIRECT_CAPTURE = 'linux-usi03-console-rx.bin'
DIRECT_META = 'linux-direct-console.json'
DIRECT_INDEX = 3
DIRECT_BASE = 0x10360000
DIRECT_SPI = 351
SHELL = '/bin/bash.bash'
SETSID = '/usr/bin/setsid.util-linux'
CAPSH = '/usr/sbin/capsh'
UID = 65534
CLEAN_SEED_FILES = {
    '/etc/fstab':'ac06a5fdd55a136bb5dfc8bca1f17eeed2b7eacf001823ed743473977f695c52',
    '/lib/systemd/system/post-init.service':'4f21ae6dbf1883c15ac777728e6a3a4fd61957eafaef8c9971d4bce7bccc739c',
    '/lge/app_ro/bin/vw_shutdown.sh':'7e95bf1c64fb7a903e9fa6f60c4b325e300a03e93aa3ac0522a5eaa3da1c1d31',
}
SEED_POST_INIT_PATH = '/lge/app_ro/post_init/post_init_main.sh'
SEED_POST_INIT_SHA256 = '89adf770ae874b7d336e5790ed1c1e9ed1ee51dd2ec91db101d836b467d1681e'
SEED_SSOA_FILES = {
    '/lge/app_ro/post_init/data/ssoa.sh':'0e29623cb8d4040bc88e8792d5983c1623fc9471405bcc73eae11ae5b1990280',
    '/lib/systemd/system/alias.service':'ab283dc1eb2cdefe8b6f0c67c0003b0aaae0452a78888cb4f2b342a7e0ef9e69',
    '/lib/systemd/system/manifest.service':'bc2bb433072d4d2b4d5c5b77dfce93ca3f53c01846df0b35ff48cc73b83e3c02',
    '/lib/systemd/system/sitm.service':'af0495b125d9b3c3684fdc163443e967a6728625f9215d8876a14bb494cb62be',
}
SEED_DISPLAY_FILES = {
    '/lib/systemd/system/weston.service':'69631b0dfb6296da563d07d995b9ceab14539018a346d57e897ec7401b502f15',
    '/lib/systemd/system/displaymgr.service':'cf977db15d5276f2e2ee8237e8f0904d7c24f950cc2b9f17bb35820b70254dfe',
}
COLDPLUG_PRIORITY = (26,18)
                                                                      
PRIOR_LOCAL_FS = {20:('/nav',259,3), 3:('/swup',8,3), 19:('/lge/bt',259,2),
                  21:('/radio/osdb',259,4), 22:('/lge/speech/db',259,5),
                  29:('/lge/log/backup',259,11)}
COLDPLUG_HEADER = '[Unit]\nAfter=systemd-udevd.service\n[Service]\n'
COLDPLUG_COMMAND = 'ExecStartPre=-/bin/udevadm trigger --type=devices --action=add --subsystem-match=block'
COLDPLUG_COMPACT = 'ExecStartPre=-/bin/udevadm trigger -t devices -c add -s block'
TRACE_BACKUP_SLOT = 29
TRACE_BACKUP_SETTLE_SEC = 8
TRACE_BACKUP_SERVICE = 'b29.service'
TRACE_BACKUP_DROPIN = 'systemd-udev-trigger.service.d/9.conf'
TRACE_BACKUP_FILES = {
    '/bin/udevadm':'4e8853c5afc907841bab04d7b60fddd3be83579068f7980b1062402c5c78061a',
    '/lib/systemd/system/systemd-udev-trigger.service':
        '9090e2dd7cec66d86b601718ddf26af1f80b6eb25947f14451394e8ab71790e3',
    '/etc/fstab':CLEAN_SEED_FILES['/etc/fstab'],
}
HMI_UNIT = '/lib/systemd/system/vw-hmi.service'
HMI_UNIT_SHA256 = 'fbde0f0a5a9366b1c5f7ae1765d2adb0f9e4e7b5a642abcccf58062adedb8caf'
HMI_START_GRACE_SECONDS = 240                                                 
HMI_RECORDING_PATH = '/data/8704-hmi-boot.jfr'
HMI_RECORDING_SECONDS = 90
HMI_VM_LOG_PATH = '/data/8704-hmi-vm.xml'
HMI_RECORDING_FILES = {
    '/lge/hmi/jvm/lib/aarch64/server/libjvm.so':'6fba892e4cb2dde51592acc4d9764ce42cbec88057e9c18284b9abc3273c52fc',
    '/lge/hmi/jvm/lib/jfr/default.jfc':'c722481cff20b9e0a0dd9a44092673af354c71b554f0fc471a494f100f3c54bb',
}
CLEAN_SEED_MARKERS = ('[sys_shutdown] All partitions went to RO','[sys_shutdown] return of tune2fs: (0)',
                      '[sys_shutdown] eMMC cleanup')
SEED_HMI_GUARD = 'ConditionPathExists=!/run/systemd/system/8704-clean-seed.service'
SEED_COMPLETION_GATE = 'ExecStartPre=/bin/grep -q ^end[[:blank:]]post_init_main /data/post_init_done.flag'


def coldplug_slots(prior_slots=()):
    return COLDPLUG_PRIORITY+tuple(slot for slot in PRIOR_LOCAL_FS if slot in prior_slots)


def coldplug_dropin(prior_slots=(), *, compact=False):
    slots=coldplug_slots(prior_slots)
                                                                            
                                                                                
    groups=[(TRACE_BACKUP_SLOT,)] if TRACE_BACKUP_SLOT in slots else []
    groups.append(tuple(slot for slot in slots if slot!=TRACE_BACKUP_SLOT))
    command=COLDPLUG_COMPACT if compact else COLDPLUG_COMMAND
    return COLDPLUG_HEADER+''.join(command+''.join(f' -y sda{slot}' for slot in group)+'\n'
                                  for group in groups)


def verified_clean_seed(parent):

    from original_8704_vm import sha
    parent=Path(parent).resolve(strict=True)
    path=parent/'linux-clean-seed-proof.json'
    proof=json.loads(path.read_bytes())
    archive='VW.03.680333-01.25.01_02-CA.tgz'
    if (proof.get('status')!='original_clean_seed_verified' or proof.get('session')!=parent.name
            or proof.get('original_unchanged') is not True or proof.get('read_only_inspection') is not True
            or proof.get('sda29')!={'check_interval_seconds':86400,'fs_state':1,'journal_recovery':False}
            or proof.get('post_init_variant')!='0x65'
            or proof.get('navigation')!=dict(archive=archive,regular_files=1728,directories=188,
                                               file_payload_bytes=643493992,all_file_sha256_match=True)
            or any(proof.get(slot,{}).get('fs_state')!=1 or
                   proof.get(slot,{}).get('journal_recovery') is not False for slot in ('sda15','sda26'))
            or tuple(m['text'] for m in proof.get('cleanup_markers',()))!=CLEAN_SEED_MARKERS
            or sha(parent/'ufs-vh1-data-session.qcow2')!=proof.get('overlay_sha256')):
        raise ValueError('Original cleanup and complete navigation receipt do not match the seed')
    return proof


def _verified_clean_mount_source(parent):

    try:
        return tuple(verified_clean_seed(parent).get('mounts',()))
    except (OSError,ValueError,KeyError,TypeError):
        return ()


def prior_local_fs_slots(session):

    from linux_pid1_context import LINUX_SHA
    if session is None:
        return ()
    parent=Path(session)
    try:
        if not all((parent/name).is_file() for name in ('result.json','ufs-vh1-data-session.qcow2')):
            return ()
        integrity=json.loads((parent/'integrity.json').read_bytes())
        snap=json.loads((parent/'linux-original-userspace.json').read_bytes())
        if (integrity['original_unchanged'] is not True or type(integrity['exit_code']) is not int or
                integrity['exit_code']!=0 or snap['kernel_sha256']!=LINUX_SHA or
                snap['status']!='verified_process_snapshot' or snap['mounts_verified'] is not True):
            return ()
        verified_ro=_verified_clean_mount_source(parent)
        slots=[]
        for slot,(target,major,minor) in PRIOR_LOCAL_FS.items():
            rows=[row for row in snap['mounts'] if row.get('mountpoint')==target]
            if (len(rows)==1 and rows[0].get('source')==f'/dev/sda{slot}' and rows[0].get('fstype')=='ext4' and
                    rows[0].get('dev_major')==major and rows[0].get('dev_minor')==minor and
                    (rows[0].get('read_only') is False or
                     (rows[0].get('read_only') is True and rows[0] in verified_ro))):
                slots.append(slot)
        return tuple(slots)
    except (OSError,ValueError,KeyError,TypeError):
        return ()


def prior_nav_mount(session):
    return 20 in prior_local_fs_slots(session)
ARGV = (SHELL, '--noprofile', '--norc', '--noediting', '-i')
COMMAND = (SETSID, '-w', CAPSH, '--no-new-privs', '--noamb', '--inh=',
           '--drop=all', '--gid=65534', '--groups=', '--uid=65534', '--caps=',
           '--shell='+SHELL, '--') + ARGV[1:]


def direct_serial(session, port, profile):

    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError('Linux USI03 needs an explicit loopback port')
    rows=[row for row in profile['ports'] if row['index']==DIRECT_INDEX]
    if len(rows)!=1 or any(rows[0][k]!=v for k,v in
            (('base',DIRECT_BASE),('spi',DIRECT_SPI),('fifo',64))):
        raise ValueError('Original VM3 USI03 grant differs from the QEMU binding')
    path=str(session/'linux-usi03-uart.bin').replace(',',',,')
    args=['-chardev',f'socket,id={DIRECT_CHR},host=127.0.0.1,port={port},server=on,wait=off,logfile={path}',
          '-serial','chardev:'+DIRECT_CHR]
    return args,dict(source=DIRECT_SOURCE, host='127.0.0.1', port=port, vmid=3,
                     serial_index=DIRECT_INDEX, terminal=DIRECT_TTY, physical=DIRECT_BASE,
                     spi=DIRECT_SPI, hcb_sha256=profile['hcb_sha256'])


def read_direct_binding(session):

    try:
        raw=(session/DIRECT_META).read_bytes()
    except FileNotFoundError:
        return None
    meta=json.loads(raw)
    profile=json.loads((session/'linux-usi-uart-profile.json').read_bytes())
    launch=json.loads((session/'launch.json').read_bytes())
    boot=json.loads((session/'linux-initcall-dtb.json').read_bytes())
    args,expected=direct_serial(session,meta['port'],profile)
    command=launch['command']
    paired=list(zip(command,command[1:]))
    serial=[value for option,value in paired if option=='-serial']
    machines=[value for option,value in paired if option=='-machine']
    if (meta!=expected or paired.count(tuple(args[:2]))!=1 or
            len(serial)<=DIRECT_INDEX or serial[DIRECT_INDEX]!=args[3] or serial.count(args[3])!=1 or
            len(machines)!=1 or 'x-exynos-linux-usi-uart=1' not in machines[0].split(',') or
            boot.get('local_linux_shell') is not True or boot.get('local_linux_shell_tty')!=DIRECT_TTY):
        raise ValueError('Unverified original VM3 USI03 serial binding')
    return meta


def stage_script(tty=TTY, *, compact=False):
                                                                          
                                                                            
                                                                                    
                                                                      
                                                                          
    command=' '.join(COMMAND)
    if tty not in (TTY,DIRECT_TTY):
        raise ValueError('Unsupported original VM3 UART terminal')
    if compact:
        if tty!=DIRECT_TTY:
            raise ValueError('Compact staging requires the exclusive native UART')
                                                                                 
        return ("umask 077;(b=/bin/busybox.nosuid;t="+tty+";until [ -c $t ];do $b sleep 1;done;until "
                "PATH=/usr/bin:/bin HOME=/nonexistent TERM=dumb PS1='8704-vm3$\\n' "+command+
                " <$t>$t 2>&1;do $b sleep 1;done)&exec /sbin/init")
    run=("( until [ -c "+tty+" ]; do /bin/busybox.nosuid sleep 1; done; "
         "e=0; until PATH=/usr/bin:/bin HOME=/nonexistent TERM=dumb PS1='8704-vm3$\\n' "
         +command+" <"+tty+" >"+tty+" 2>&1; do r=$?; "
         "if [ $r != $e ]; then echo '<3>Local VM3 UART retry:' $r >/dev/kmsg; e=$r; fi; "
         "/bin/busybox.nosuid sleep 1; done ) & ")
    if tty==DIRECT_TTY:
                                                               
        return "umask 077; echo '<5>Local VM3 USI03 UART staged' >/dev/kmsg; "+run+"exec /sbin/init"
    return ("umask 077; if /bin/mkdir -p /run/systemd/system && "
            "/bin/ln -s /dev/null /run/systemd/system/serial-getty@ttySAC0.service; then "
            "echo '<5>Local VM3 capsh UART staged' >/dev/kmsg; "+run+
            "else echo '<3>Local VM3 UART staging failed' >/dev/kmsg; fi; exec /sbin/init")


def trace_backup_settle_units(prior_slots, *, early_nav=False, early_nav_core=False,
                              single_batch=False, early_swup=False, early_radio=False):

    slots = coldplug_slots(prior_slots)
    if TRACE_BACKUP_SLOT not in slots:
        raise ValueError('Trace backup settle requires a verified original sda29 seed')
    if early_nav and 20 not in slots:
        raise ValueError('Early /nav coldplug requires a verified original sda20 mount')
    first = (TRACE_BACKUP_SLOT,) + ((20,) if early_nav else ())
    if early_nav_core:
        first += COLDPLUG_PRIORITY
    if early_swup:
        if 3 not in slots:
            raise ValueError('Early /swup coldplug requires a verified original sda3 mount')
        first += (3,)
    if early_radio:
        if 21 not in slots or single_batch:
            raise ValueError('Early /radio/osdb requires verified sda21 and separate batches')
        first += (21,)
    if single_batch:
        if not (early_nav and early_nav_core):
            raise ValueError('Single early coldplug batch requires the /nav and navigation core options')
                                                                          
                                                                           
        first += tuple(slot for slot in slots if slot not in first)
    selectors = ''.join(f' -y sda{slot}' for slot in first)
    unit = ('[Unit]\nDefaultDependencies=no\nAfter=systemd-udevd.service\n'
            '[Service]\nType=oneshot\n'
            f'TimeoutSec={TRACE_BACKUP_SETTLE_SEC}\n'
            f'ExecStart=/bin/udevadm trigger -w -c add -s block{selectors}\n')
                                                                        
                                                                          
    other = ''.join(f' -y sda{slot}' for slot in slots if slot not in first)
    dropin = f'[Unit]\nWants={TRACE_BACKUP_SERVICE}\nAfter={TRACE_BACKUP_SERVICE}\n'
    if other:
        dropin += f'[Service]\nExecStartPre=-/bin/udevadm trigger -c add -s block{other}\n'
    return unit,dropin


def validate_trace_backup_settle(root_image):

    from hashlib import file_digest
    from dissect.extfs import ExtFS
    with Path(root_image).open('rb') as image:
        fs = ExtFS(image)
        for name,digest in TRACE_BACKUP_FILES.items():
            with fs.get(name).open() as member:
                if file_digest(member,'sha256').hexdigest()!=digest:
                    raise ValueError('Trace backup settle input changed: '+name)
        for root in ('/etc/systemd/system','/lib/systemd/system','/usr/lib/systemd/system'):
            for entry in (TRACE_BACKUP_SERVICE, TRACE_BACKUP_DROPIN.split('/')[0]):
                path = root+'/'+entry
                try:
                    fs.get(path)
                except FileNotFoundError:
                    continue
                raise ValueError('Trace backup unit or drop-in already exists: '+path)


def stage_coldplug_order(prior_slots=(), *, compact=False, trace_backup_settle=False,
                         early_nav=False, early_nav_core=False, single_batch=False, early_swup=False,
                         early_radio=False):

    if (early_nav or early_nav_core or single_batch or early_swup or early_radio) and not trace_backup_settle:
        raise ValueError('Early navigation coldplug requires the bounded settle unit')
    if trace_backup_settle:
        if not compact:
            raise ValueError('Trace backup settle requires the compact native boot staging')
        units = trace_backup_settle_units(prior_slots,early_nav=early_nav,early_nav_core=early_nav_core,
                                          single_batch=single_batch,early_swup=early_swup,early_radio=early_radio)
        unit,dropin = (s.replace('\n',r'\n') for s in units)
                                                                            
        return ("umask 022;d=/run/systemd/system/systemd-udev-trigger.service.d;"
                "mkdir -p $d&&printf '"+unit+"'>$d/../"+TRACE_BACKUP_SERVICE
                +"&&printf '"+dropin+"'>$d/9.conf;")
                                                                         
                                                                             
    body=coldplug_dropin(prior_slots,compact=compact).replace('\n',r'\n')
    if compact:
                                                                        
                                                                   
        return ("umask 022;d=/run/systemd/system/systemd-udev-trigger.service.d;"
                "if /bin/mkdir -p $d&&printf '"+body+"'>$d/90-native-coldplug.conf;then "
                "echo '<5>OEM coldplug staged';else echo '<3>OEM coldplug failed';fi >/dev/kmsg; ")
    return ("umask 022; d=/run/systemd/system/systemd-udev-trigger.service.d; "
            "if /bin/mkdir -p $d && printf '"+body+"' "
            ">$d/90-native-coldplug.conf; then echo '<5>OEM coldplug ordering staged' >/dev/kmsg; "
            "else echo '<3>OEM coldplug ordering failed' >/dev/kmsg; fi; ")


def validate_clean_seed_policy(root_image, *, accelerated=False, no_display=False):

    import hashlib
    from dissect.extfs import ExtFS
    with Path(root_image).open('rb') as image:
        fs=ExtFS(image)
        files=CLEAN_SEED_FILES|({SEED_POST_INIT_PATH:SEED_POST_INIT_SHA256, **SEED_SSOA_FILES} if accelerated else {})
        if no_display:
            if not accelerated:
                raise ValueError('Display isolation requires accelerated seed preparation')
            files |= SEED_DISPLAY_FILES
        for path,digest in files.items():
            with fs.get(path).open() as f:
                if hashlib.sha256(f.read()).hexdigest()!=digest:
                    raise ValueError('Original VM3 clean-shutdown contract changed: '+path)


def clean_seed_unit(*, accelerated=False):

    return ('[Unit]\nAfter=post-init.service\n'
            +('Requires=post-init.service\n' if accelerated else '')+
            'RequiresMountsFor=/data /vw /nav /navi_data /lge/navi/naviengine /lge/bt /radio/osdb /lge/speech/db /swup /lge/log/backup\n'
            'ConditionPathIsReadWrite=/data\nConditionPathExists=!/tmp/post_init_fail.tmp\n'
            'ConditionPathExists=/data/lge/ime_rw/ChineseMainlandUser.dic\n'
            '[Service]\nType=oneshot\nTimeoutStartSec=60\n'
            +(SEED_COMPLETION_GATE if accelerated else
              'ExecStartPre=/bin/grep -qF icas3cn-gp /data/post_init_done.flag')+'\n'+
            'ExecStart=/lge/app_ro/bin/vw_shutdown.sh cleanup\n')


def stage_clean_seed():
                                                                            
                                                                          
    unit=clean_seed_unit().replace('\n',r'\n')
    return ("p=/run/systemd/system; d=$p/post-init.service.wants; n=8704-clean-seed.service; "
            "if mkdir -p $d && printf '"+unit+"' >$p/$n && ln -s ../$n $d/$n; then "
            "echo '<5>OEM clean seed hook staged' >/dev/kmsg; "
            "else echo '<3>OEM clean seed staging failed' >/dev/kmsg; fi; ")


def stage_preparation_order(*, accelerated=False, no_display=False):

                                                                          
                                                                              
    hmi = SEED_HMI_GUARD if accelerated else 'After=8704-clean-seed.service post-init.service'
                                                                         
                                                                                  
    before = ' alias.service manifest.service sitm.service' if accelerated else ''
    if no_display:
        if not accelerated:
            raise ValueError('Display isolation requires accelerated seed preparation')
                                                                              
                                                                                      
        return ("d=$p/post-init.service.d;mkdir -p $d;printf "
                r"'[Unit]\nDefaultDependencies=no\nRequires=sysinit.target local-fs.target\n"
                r"After=systemd-tmpfiles-setup.service trace.service\nBefore=shutdown.target"+before+
                r"\nConflicts=shutdown.target\n[Service]\nNice=-20\n'>$d/9.conf;"
                "for i in vw-hmi weston displaymgr;do d=$p/$i.service.d;"
                "mkdir -p $d&&printf '[Unit]\\n"+SEED_HMI_GUARD+r"\n'>$d/90.conf;done;")
    return ("for n in post-init vw-hmi;do mkdir -p $p/$n.service.d;done;"
            r"printf '[Unit]\nDefaultDependencies=no\nRequires=sysinit.target local-fs.target\n"
            r"After=systemd-tmpfiles-setup.service local-fs.target trace.service\nBefore=shutdown.target"+before+r"\n"
            r"Conflicts=shutdown.target\n"+(r"[Service]\nNice=-20\n" if accelerated else '')+
            "'>$p/post-init.service.d/9.conf;"
            r"printf '[Unit]\n"+hmi+r"\n'>$p/vw-hmi.service.d/90.conf;")


def seed_targeted_coldplug_unit(prior_slots):

    unit, dropin=trace_backup_settle_units(
        prior_slots,early_nav=True,early_nav_core=True,single_batch=True)
                                                                           
                                                                          
                                                                            
    slots=(26,18,20,3,21,19,22,29)
    if set(slots)!=set(coldplug_slots(prior_slots)):
        raise ValueError('Targeted coldplug requires the exact verified partitions')
    cmd=("/bin/sh -c 'for n in "+' '.join(map(str,slots))+
         ";do echo add >/sys/block/sda/sda$$n/uevent||exit;done'")
    unit=unit[:unit.index('ExecStart=')]+'ExecStart='+cmd+'\n'
    return unit,dropin


def stage_stock_seed(prior_slots, *, accelerated=False, no_display=False, direct_uevents=False):

    body=clean_seed_unit(accelerated=accelerated).replace('\n',r'\n')
    ons=''
    if direct_uevents and not accelerated:
        raise ValueError('Targeted coldplug requires accelerated seed preparation')
    if accelerated:
        if set(prior_slots)!=set(PRIOR_LOCAL_FS):
            raise ValueError('Accelerated preparation needs all original coldplug mounts verified')
        unit,dropin=(seed_targeted_coldplug_unit(prior_slots) if direct_uevents else
                     trace_backup_settle_units(prior_slots,early_nav=True,early_nav_core=True,single_batch=True))
                                                                        
                                                                             
        encoded=unit.replace("'",r'\047').replace('\n',r'\n')
        ons="printf '"+encoded+"'>$p/"+TRACE_BACKUP_SERVICE+"&&"
    else:
        dropin=coldplug_dropin(prior_slots,compact=True)
    coldplug=dropin.replace('\n',r'\n')
    return ("p=/run/systemd/system;n=8704-clean-seed.service;d=$p/post-init.service.wants;"
            "u=$p/systemd-udev-trigger.service.d;umask 022;mkdir -p $d $u&&"
            "printf '"+body+"'>$p/$n&&ln -s ../$n $d/$n;"
            +ons+"printf '"+coldplug+"'>$u/9.conf;"
            +stage_preparation_order(accelerated=accelerated,no_display=no_display)+"exec /sbin/init")


def validate_post_init_priority(session, *, clean_seed=False):

    p=Path(session).resolve(strict=True)
    if set(prior_local_fs_slots(p))!=set(PRIOR_LOCAL_FS):
        raise ValueError('Need a verified original source with all required local mounts')
    snap=json.loads((p/'linux-original-userspace.json').read_bytes())
    proof=verified_clean_seed(p) if clean_seed else None
    required={'/data':('/dev/sda15',clean_seed),'/navi_data':('/dev/sda26',clean_seed),
              '/lge/navi/naviengine':('/dev/sda18',True),'/vw':('/dev/sda23',True)}
    for path,(source,ro) in required.items():
        rows=[r for r in snap['mounts'] if r.get('mountpoint')==path]
        if (len(rows)!=1 or rows[0].get('source')!=source or rows[0].get('read_only') is not ro
                or rows[0].get('fstype')!='ext4' or
                proof and path in ('/data','/navi_data') and rows[0] not in proof['mounts']):
            raise ValueError('Original post-init prerequisite not verified: '+path)
    if snap.get('mcu_identity')!={'platform':4,'market':4,'variant':0x65} or not any(
            r.get('comm')=='trace-daemon' for r in snap.get('tasks',())):
        raise ValueError('Need original China VM3 and trace-daemon runtime evidence')


def stage_post_init_priority():

    unit=('[Unit]\\nDefaultDependencies=no\\nRequires=sysinit.target local-fs.target\\n'
          'After=sysinit.target local-fs.target trace.service\\n'
          'Before=shutdown.target\\nConflicts=shutdown.target\\n')
    return ("p=/run/systemd/system/post-init.service.d; mkdir -p $p&&printf '"+unit+
            "'>$p/90-native-priority.conf; ")


def validate_hmi_start_grace(root_image):

    import hashlib
    from dissect.extfs import ExtFS
    with Path(root_image).open('rb') as image:
        fs=ExtFS(image)
        with fs.get(HMI_UNIT).open() as stream:
            original=stream.read(4097)
    if len(original)>4096 or hashlib.sha256(original).hexdigest()!=HMI_UNIT_SHA256:
        raise ValueError('Original VW HMI notify service does not match')


def validate_hmi_recording(root_image, *, vm_log=False):

    import hashlib
    from dissect.extfs import ExtFS
    with Path(root_image).open('rb') as image:
        fs=ExtFS(image)
        for name,expected in HMI_RECORDING_FILES.items():
            if vm_log and name.endswith('.jfc'):
                continue
            with fs.get(name).open() as member:
                digest=hashlib.file_digest(member,'sha256').hexdigest()
            if digest!=expected:
                raise ValueError('Original HMI JFR input changed: '+name)


def validate_hmi_post_init_order(root_image):

    from hashlib import file_digest
    from dissect.extfs import ExtFS
    with Path(root_image).open('rb') as image:
        fs=ExtFS(image)
        for name,target in (('/var/cache/hmi','/data/lge/hmi/cache'),
                            ('/etc/systemd/system/basic.target.wants/vw-hmi.service',HMI_UNIT),
                            ('/etc/systemd/system/basic.target.wants/post-init.service',
                             '/lib/systemd/system/post-init.service')):
            node=fs.get(name)
            if node.filetype!=0xa000 or node.link!=target:
                raise ValueError('Original HMI ordering input differs: '+name)
        with fs.get('/lge/app_ro/post_init/data/hmi.sh').open() as member:
            if file_digest(member,'sha256').hexdigest()!='e4f91342935a22c09f04f906c5afa929b0434aab2a64b321c916c39ff769591b':
                raise ValueError('Original HMI cache cleanup differs')


def stage_hmi_start_grace(*, interpreter=False, flight_recording=False, vm_log=False, lsc_timeout_ms=None,
                          after_post_init=False):
    if sum(map(bool,(interpreter,flight_recording,vm_log)))>1:
        raise ValueError('Choose one HMI observation mode; recordings preserve the original compiler mode')
    if lsc_timeout_ms is not None and (type(lsc_timeout_ms) is not int or not 5000<=lsc_timeout_ms<=90000
                                      or interpreter or flight_recording):
        raise ValueError('Native LSC timing requires stock JVM mode and 5000..90000 ms')
    unit=f'[Service]\\nTimeoutStartSec={HMI_START_GRACE_SECONDS}\\n'
    if after_post_init:
        if interpreter or flight_recording or vm_log or lsc_timeout_ms is None:
            raise ValueError('HMI ordering comparison needs the stock JVM without diagnostic XML')
                                                                           
                                                                             
        unit=r'[Unit]\nAfter=post-init.service\n'+unit
    if interpreter:
                                                                        
                                                                              
        unit+=r'Environment=_JAVA_OPTIONS=-Xint\n'
    if flight_recording:
        unit+=(f'Environment=_JAVA_OPTIONS=-XX:StartFlightRecording=duration={HMI_RECORDING_SECONDS}s,'
               f'filename={HMI_RECORDING_PATH}\\n')
    if vm_log or lsc_timeout_ms is not None:
                                                                           
                                                                          
        options=[]
        if vm_log:
            options.extend(('-XX:+LogVMOutput','-XX:LogFile='+HMI_VM_LOG_PATH))
        if lsc_timeout_ms is not None:
            options.append(f'-Dde.vw.mib.lsc.initial.timeout={lsc_timeout_ms}')
        unit+=r'Environment=\047_JAVA_OPTIONS='+' '.join(options)+r'\047\n'
    return ("h=/run/systemd/system/vw-hmi.service.d;mkdir -p $h&&printf '"+unit+
            "'>$h/90.conf; ")
