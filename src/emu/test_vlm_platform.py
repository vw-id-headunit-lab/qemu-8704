
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock
import tempfile
import vlm_platform as platform
import patch_qemu_exynos as patch


class Stream:
    def __init__(self,data):self.data=bytearray(data);self.sent=[]
    def recv(self,count):
        value=bytes(self.data[:count]);del self.data[:count];return value
    def sendall(self,data):self.sent.append(data)


def packet(raw):return b'$'+raw+b'#'+f'{sum(raw)%256:02x}'.encode()


class ContractTests(unittest.TestCase):
    def test_qnx_g3d00_screen_ram_is_exclusive_and_excludes_dtb_debug(self):
        from unittest.mock import patch as mock_patch
        from qnx_g3d00_platform import QNX_DTB, VM2_SCREEN_LEAF_BANK, validate_dma_banks
        hcb=platform.original('hcb/hcb.img').read_bytes()
        raw=platform.factory_file(QNX_DTB).read_bytes()
        validate_dma_banks(hcb,raw)
        p=hcb.index(b'VDTHYP\0\0')+8
        tree=platform.parse_dtb(hcb[p:p+struct.unpack_from('>I',hcb,p+4)[0]])
        guest=platform.parse_dtb(raw)
        guest['/reserved-memory/overlap']={'reg':struct.pack('>QI',VM2_SCREEN_LEAF_BANK[0],4096)}
        with mock_patch('qnx_g3d00_platform.parse_dtb',side_effect=[tree,guest]):
            with self.assertRaisesRegex(ValueError,'overlaps reserved'):
                validate_dma_banks(hcb,raw)
        tree['/vlm/vm@3/vm-memory']['reg']+=struct.pack('>QQ',VM2_SCREEN_LEAF_BANK[0],4096)
        with mock_patch('qnx_g3d00_platform.parse_dtb',return_value=tree):
            with self.assertRaisesRegex(ValueError,'overlaps another VM'):
                validate_dma_banks(hcb,raw)

    def test_stock_weston_trace_preserves_original_renderer_arguments(self):
        from dissect.extfs import ExtFS
        from linux_weston_platform import CONFIG, TRACE_ENV, configure
        with platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4').open('rb') as disk:
            with ExtFS(disk).get(CONFIG).open() as stream:
                raw=stream.read()
        stock=configure(raw,trace_protocol=True,pixman=False)
        self.assertEqual(len(stock),len(raw))
        self.assertEqual(stock.count(TRACE_ENV),1)
        self.assertEqual([l for l in stock.splitlines() if l.startswith(b'argv=')],
                         [l for l in raw.splitlines() if l.startswith(b'argv=')])
        self.assertIn(b'argv=--use-pixman\n',configure(raw,trace_protocol=True))

    def test_native_scene_page_walk_rejects_kernel_only_or_wrong_granule(self):
        va=0x412018
        tables={0x1000:0x2003,0x2010:0x3003,0x3090:0xa000|0x43}
        self.assertEqual(platform.walk_user_4k(va,25,0x1000,tables.__getitem__),0xa018)
        tables[0x3090]=0xa003
        with self.assertRaisesRegex(ValueError,'EL0'):
            platform.walk_user_4k(va,25,0x1000,tables.__getitem__)
        with self.assertRaisesRegex(ValueError,'Unsupported'):
            platform.walk_user_4k(va,25|1<<14,0x1000,tables.__getitem__)

    def test_new_vw_never_inherits_old_verity_completion(self):
        from linux_hmi_userdata import require_no_stale_vw_verity_marker
        missing=Mock()
        missing.get.side_effect=FileNotFoundError
        require_no_stale_vw_verity_marker(missing)
        missing.get.assert_called_once_with('/fsverity_vw.flag')
        with self.assertRaisesRegex(ValueError,'different VW volume'):
            require_no_stale_vw_verity_marker(Mock())

    def test_vw_install_transition_accepts_only_its_backing_and_no_slot_shadow(self):
        from linux_hmi_overlay import require_no_slot23_shadow, seed_backing

        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'stock.qcow2'
            new=Path(tmp)/'install.qcow2'
            for path in (old,new):
                path.touch()
            media=dict(overlay=str(new),overlay_sha256='b'*64,offset=65536,length=65536,
                       install_metadata_transition=dict(predecessor=str(old),predecessor_sha256='a'*64))
            self.assertEqual(seed_backing(media,dict(parent=str(old),parent_sha256='a'*64)),old.resolve())
            self.assertEqual(seed_backing(media,dict(parent=str(new),parent_sha256='b'*64)),new.resolve())
            with self.assertRaises(ValueError):
                seed_backing(media,dict(parent=str(old),parent_sha256='c'*64))
            with self.assertRaises(ValueError):
                seed_backing({k:v for k,v in media.items() if k!='install_metadata_transition'},
                             dict(parent=str(old),parent_sha256='a'*64))
            require_no_slot23_shadow([dict(depth=0,start=0,length=65536,data=True),
                                     dict(depth=1,start=65536,length=65536,data=True),
                                     dict(depth=0,start=131072,length=4096,data=True)],media)
            for start,length in ((61440,8192),(65536,4096),(126976,8192)):
                with self.subTest(start=start,length=length),self.assertRaises(ValueError):
                    require_no_slot23_shadow([dict(depth=0,start=start,length=length,
                                                   data=False,present=True)],media)

    def test_full_oem_cli_requires_explicit_extended_observation(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        from unittest.mock import patch as mock_patch
        from linux_data_overlay import PROFILE

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for seconds, opt_in, allowed in ((120, False, True), (240, False, True), (241, False, False), (241, True, True),
                                             (1800, False, False), (1800, True, True)):
                with self.subTest(seconds=seconds, opt_in=opt_in):
                    out = root / 'var' / f'boot-{seconds}-{int(opt_in)}'
                    argv = ['vlm_platform.py', 'probe', '--output', str(out), '--seconds', str(seconds),
                            '--board-adapter', '--load-qnx', '--load-linux', '--load-android',
                            '--abox-dsp-off', 'virtual-abox-dsp-off-v1', '--abox-dsp-mailbox',
                            '--linux-hmi-overlay', str(root / 'overlay.json'), '--android-ufs-overlay',
                            '--linux-ivi-data', PROFILE, '--linux-factory-bank', '--qnx-factory-bank']
                    if opt_in:
                        argv.append('--extended-observation')
                    stderr = StringIO()
                    with mock_patch.object(sys, 'argv', argv), mock_patch.object(platform.vm, 'ROOT', root), \
                         mock_patch.object(platform, 'probe', return_value={}) as probe, \
                         redirect_stderr(stderr), redirect_stdout(StringIO()):
                        if allowed:
                            platform.main()
                            self.assertEqual(probe.call_args.kwargs['seconds'], seconds)
                        else:
                            with self.assertRaises(SystemExit) as stop:
                                platform.main()
                            self.assertEqual(stop.exception.code, 2)
                            self.assertIn('--extended-observation', stderr.getvalue())
                            probe.assert_not_called()
                            self.assertFalse(out.exists())

    def test_local_vm3_shell_is_original_unprivileged_and_volatile(self):
        import linux_early_uart as early
        import linux_local_shell as local
        local.validate_factory(platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
        stage=local.stage_script()
        self.assertNotIn('"',stage)
        self.assertNotIn('mktemp',stage)
        self.assertNotIn('openssl',stage)
        self.assertIn('/run/systemd/system/serial-getty@ttySAC0.service',stage)
        self.assertIn(r"PS1='8704-vm3$\n'",stage)
        self.assertIn('</dev/ttySAC0 >/dev/ttySAC0 2>&1',stage)
        self.assertTrue(stage.endswith('exec /sbin/init'))
        self.assertLess(len(stage),1400)
        self.assertEqual(early.COMMAND[:3],(early.SETSID,'-w',early.CAPSH))
        self.assertEqual(early.COMMAND[-4:],('--noprofile','--norc','--noediting','-i'))
        self.assertNotIn('-c',early.COMMAND)                                      
        for option in ('--no-new-privs','--noamb','--inh=','--drop=all','--groups=',
                       '--gid=65534','--uid=65534','--caps=','--shell=/bin/bash.bash'):
            self.assertIn(option,early.COMMAND)

    def test_local_vm3_shell_proof_does_not_accept_echo_or_wrong_domain(self):
        import linux_local_shell as local

        marker=b'8704-vm3-check:cafef00d:65534:Linux'
        self.assertIsNone(local.response_source(b'printf \'%s\' cafef00d\r\n',marker))
        self.assertIsNone(local.response_source(b'1 4 0:'+marker+b'\r\n',marker))
        self.assertIsNone(local.response_source(marker,marker))
        self.assertEqual(local.response_source(b'1 3 0:'+marker+b'\r\n',marker),'original-vm3-tag')
        self.assertEqual(local.response_source(marker+b'\r\n',marker),'shared-physical-uart')
        self.assertIsNone(local.response_source(b'1 3 0:'+marker+b'\r\n',marker,dedicated=True))
        self.assertEqual(local.response_source(marker+b'\r\n',marker,dedicated=True),'dedicated-linux-usi03')

    def test_local_vm4_observes_stock_ramdisk_without_enabling_debug(self):
        import gzip
        import android_local_shell as android

        def record(name):
            name=name.encode()+b'\0'
            row=b'070701'+b'00000000'*11+f'{len(name):08x}00000000'.encode()+name
            return row+b'\0'*(-len(row)%4)

        generic=gzip.compress(record('init')+record('TRAILER!!!'),mtime=0)
        vendor=(gzip.compress(record('./first_stage_ramdisk/adb_debug.prop')+record('TRAILER!!!'),mtime=0)+
                gzip.compress(record('force_debuggable')+record('TRAILER!!!'),mtime=0))
        meta=android.stock_console_policy(generic,vendor)
        self.assertTrue(meta['local_android_shell'])
        self.assertEqual(meta['local_android_shell_mode'],'stock-console-observe')
        self.assertFalse(meta['avb_partitions_changed'])
        self.assertFalse(meta['ramdisk_changed'])
        self.assertTrue(meta['debug_ramdisk_members_verified'])
        self.assertTrue(meta['root_debug_members']['force_debuggable'])
        self.assertTrue(meta['first_stage_debug_members']['adb_debug.prop'])
        self.assertFalse(meta['root_debug_members']['adb_debug.prop'])
        self.assertFalse(meta['first_stage_debug_members']['userdebug_plat_sepolicy.cil'])
        bad=android.stock_console_policy(generic,vendor[:-3])
        self.assertFalse(bad['debug_ramdisk_members_verified'])
        self.assertFalse(bad['ramdisk_changed'])
        marker=b'8704-vm4-check:cafef00d:2000:Linux'
        self.assertIsNone(android.response_source(b'printf cafef00d\r\n',marker))
        self.assertIsNone(android.response_source(b'1 3 0:'+marker+b'\r\n',marker))
        self.assertIsNone(android.response_source(marker,marker))
        self.assertEqual(android.response_source(b'3 4 0:'+marker+b'\r\n',marker),'original-vm4-tag')
        self.assertEqual(android.response_source(marker+b'\r\n',marker),'shared-physical-uart')

    def test_native_nav_coldplug_targets_known_devices_without_replacing_original_commands(self):
        from linux_early_uart import coldplug_dropin, stage_coldplug_order
        trigger='ExecStartPre=-/bin/udevadm trigger --type=devices --action=add --subsystem-match=block'
        body=coldplug_dropin()
        setup=stage_coldplug_order()
        self.assertEqual(body.splitlines(), ['[Unit]', 'After=systemd-udevd.service',
            '[Service]', trigger+' -y sda26 -y sda18'])
        self.assertEqual(coldplug_dropin((20,)).splitlines()[-1],trigger+' -y sda26 -y sda18 -y sda20')
        all_slots=(20,3,19,21,22,29)
        full=coldplug_dropin(all_slots)
        self.assertEqual(full.splitlines()[3:], [trigger+' -y sda29',
            trigger+' -y sda26 -y sda18 -y sda20 -y sda3 -y sda19 -y sda21 -y sda22'])
        self.assertIn('/run/systemd/system/systemd-udev-trigger.service.d',setup)
        self.assertIn("printf '"+body.replace('\n',r'\n')+"'",setup)
        self.assertIn("printf '"+full.replace('\n',r'\n')+"'",stage_coldplug_order(all_slots))
        self.assertIn('>$d/90-native-coldplug.conf',setup)
        self.assertNotIn('"',setup)                                                         
        self.assertNotIn('ExecStart=',setup)
        self.assertNotIn('by-partlabel',setup)
        self.assertNotIn('fstab',setup)

    def test_early_nav_moves_only_the_verified_sda20_selector(self):
        from linux_early_uart import stage_coldplug_order, trace_backup_settle_units
        slots=(20,3,19,21,22,29)
        baseline=trace_backup_settle_units(slots)
        unit,dropin=trace_backup_settle_units(slots,early_nav=True)
        self.assertEqual(unit,baseline[0].replace(' -y sda29\n',' -y sda29 -y sda20\n'))
        self.assertEqual(dropin,baseline[1].replace(' -y sda20',''))
        self.assertIn('TimeoutSec=8\n',unit)
        old=stage_coldplug_order(slots,compact=True,trace_backup_settle=True)
        new=stage_coldplug_order(slots,compact=True,trace_backup_settle=True,early_nav=True)
        self.assertEqual(len(old),len(new))                                             
        with self.assertRaisesRegex(ValueError,'verified original sda20'):
            trace_backup_settle_units((29,),early_nav=True)
        with self.assertRaisesRegex(ValueError,'bounded settle unit'):
            stage_coldplug_order(slots,early_nav=True)

    def test_early_nav_core_coldplug_only_moves_sda26_and_sda18(self):
        from linux_early_uart import stage_coldplug_order, trace_backup_settle_units
        slots=(20,3,19,21,22,29)
        for early_nav in (False, True):
            with self.subTest(early_nav=early_nav):
                flags=dict(early_nav=early_nav)
                old_unit,old_dropin=trace_backup_settle_units(slots,**flags)
                unit,dropin=trace_backup_settle_units(slots,early_nav_core=True,**flags)
                self.assertEqual(unit,old_unit.rstrip('\n')+' -y sda26 -y sda18\n')
                self.assertEqual(dropin,old_dropin.replace(' -y sda26 -y sda18',''))
                self.assertIn('TimeoutSec=8\n',unit)
                flags.update(compact=True,trace_backup_settle=True)
                self.assertEqual(len(stage_coldplug_order(slots,**flags)),
                                 len(stage_coldplug_order(slots,early_nav_core=True,**flags)))
        with self.assertRaisesRegex(ValueError,'bounded settle unit'):
            stage_coldplug_order(slots,early_nav_core=True)

    def test_single_early_coldplug_keeps_all_selectors_in_one_bounded_pass(self):
        from linux_early_uart import stage_coldplug_order, trace_backup_settle_units
        slots=(20,3,19,21,22,29)
        flags=dict(early_nav=True,early_nav_core=True)
        stock_first,stock_second=trace_backup_settle_units(slots,**flags)
        first,second=trace_backup_settle_units(slots,single_batch=True,**flags)
        self.assertEqual(first,stock_first.rstrip('\n')+' -y sda3 -y sda19 -y sda21 -y sda22\n')
        self.assertEqual(second,stock_second.split('[Service]\n',1)[0])
        self.assertIn('TimeoutSec=8\n',first)
        self.assertEqual(stage_coldplug_order(slots,compact=True,trace_backup_settle=True,
                                             single_batch=True,**flags).count('/bin/udevadm trigger'),1)
        with self.assertRaisesRegex(ValueError,'requires the /nav and navigation core'):
            trace_backup_settle_units(slots,single_batch=True,early_nav=True)

    def test_early_swup_only_moves_verified_sda3_between_existing_batches(self):
        from linux_early_uart import stage_coldplug_order, trace_backup_settle_units
        slots=(20,3,19,21,22,29)
        flags=dict(early_nav=True,early_nav_core=True)
        a,b=trace_backup_settle_units(slots,**flags)
        first,second=trace_backup_settle_units(slots,early_swup=True,**flags)
        self.assertEqual(first,a.rstrip('\n')+' -y sda3\n')
        self.assertEqual(second,b.replace(' -y sda3','',1))
        staged=dict(flags,compact=True,trace_backup_settle=True)
        self.assertEqual(len(stage_coldplug_order(slots,early_swup=True,**staged)),
                         len(stage_coldplug_order(slots,**staged)))
        with self.assertRaisesRegex(ValueError,'verified original sda3'):
            trace_backup_settle_units((20,29),early_swup=True)
        with self.assertRaisesRegex(ValueError,'bounded settle unit'):
            stage_coldplug_order(slots,early_swup=True)

    def test_radio_coldplug_moves_only_verified_sda21(self):
        from linux_early_uart import trace_backup_settle_units
        slots=(3,19,20,21,22,29)
        flags=dict(early_nav=True,early_nav_core=True,early_swup=True)
        first,later=trace_backup_settle_units(slots,**flags)
        a,b=trace_backup_settle_units(slots,early_radio=True,**flags)
        self.assertEqual(a,first.rstrip('\n')+' -y sda21\n')
        self.assertEqual(b,later.replace(' -y sda21',''))
        with self.assertRaisesRegex(ValueError,'verified sda21'):
            trace_backup_settle_units((3,20,22,29),early_radio=True,**flags)

    def test_coldplug_map_requires_clean_completed_native_seed_with_original_ext4_mount(self):
        from linux_early_uart import prior_local_fs_slots, prior_nav_mount
        from linux_pid1_context import LINUX_SHA
        row=dict(mountpoint='/nav',source='/dev/sda20',fstype='ext4',dev_major=259,dev_minor=3,read_only=False)
        snap=dict(kernel_sha256=LINUX_SHA,status='verified_process_snapshot',mounts_verified=True,mounts=[row])
        self.assertFalse(prior_nav_mount(None))
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            (p/'result.json').write_text('{}')
            (p/'ufs-vh1-data-session.qcow2').touch()
            (p/'integrity.json').write_text('{"original_unchanged":true,"exit_code":0}')
            f=p/'linux-original-userspace.json'
            self.assertFalse(prior_nav_mount(p))
            f.write_text(json.dumps(snap))
            self.assertTrue(prior_nav_mount(p))
            self.assertEqual(prior_local_fs_slots(p),(20,))
            speech=dict(row,mountpoint='/lge/speech/db',source='/dev/sda22',dev_minor=5)
            f.write_text(json.dumps(dict(snap,mounts=[speech,row])))
            self.assertEqual(prior_local_fs_slots(p),(20,22))
            f.write_text(json.dumps(dict(snap,mounts=[row,row,speech])))
            self.assertEqual(prior_local_fs_slots(p),(22,))
            for wrong in (dict(row,source='/dev/sda18'),dict(row,read_only=True),dict(row,fstype='tmpfs')):
                f.write_text(json.dumps(dict(snap,mounts=[wrong])))
                self.assertFalse(prior_nav_mount(p))

    def test_clean_seed_stages_only_original_post_init_ordered_cleanup(self):
        from linux_early_uart import clean_seed_unit, stage_clean_seed, validate_clean_seed_policy
        validate_clean_seed_policy(platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
        body=clean_seed_unit()
        self.assertIn('After=post-init.service\n',body)
        self.assertIn('ConditionPathIsReadWrite=/data\n',body)
        self.assertIn('ConditionPathExists=!/tmp/post_init_fail.tmp\n',body)
        self.assertIn('ChineseMainlandUser.dic\n',body)
        self.assertIn('ExecStartPre=/bin/grep -qF icas3cn-gp /data/post_init_done.flag\n',body)
        self.assertEqual([line for line in body.splitlines() if line.startswith('ExecStart=')],
                         ['ExecStart=/lge/app_ro/bin/vw_shutdown.sh cleanup'])
        staged=stage_clean_seed()
        self.assertIn('/run/systemd/system',staged)
        self.assertIn('post-init.service.wants',staged)
        self.assertIn("printf '"+body.replace('\n',r'\n')+"'",staged)
        self.assertNotIn('"',staged)
        self.assertNotIn('tune2fs',staged)

    def test_accelerated_seed_keeps_oem_completion_gate_and_stock_units(self):
        from linux_early_uart import (PRIOR_LOCAL_FS, SEED_COMPLETION_GATE, SEED_HMI_GUARD,
                                      stage_stock_seed, trace_backup_settle_units, validate_clean_seed_policy)
        validate_clean_seed_policy(platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'),accelerated=True)
        slots=tuple(PRIOR_LOCAL_FS)
        normal=stage_stock_seed(slots)
        staged=stage_stock_seed(slots,accelerated=True)
        unit,drop=trace_backup_settle_units(slots,early_nav=True,early_nav_core=True,single_batch=True)
        for content in (unit,drop):
            self.assertIn(content.replace('\n',r'\n'),staged)
        self.assertIn(r'After=post-init.service\nRequires=post-init.service\n',staged)
        self.assertIn(r'ConditionPathExists=!/tmp/post_init_fail.tmp\n',staged)
        self.assertIn(r'ExecStart=/lge/app_ro/bin/vw_shutdown.sh cleanup\n',staged)
        self.assertIn(SEED_COMPLETION_GATE+r'\n',staged)
        self.assertIn(SEED_HMI_GUARD+r'\n',staged)
        self.assertIn(r'Before=shutdown.target alias.service manifest.service sitm.service\n',staged)
        self.assertNotIn('sitm.service',normal)
        self.assertIn(r'After=8704-clean-seed.service post-init.service\n',normal)
        self.assertIn(r'[Service]\nNice=-20\n',staged)
        self.assertNotIn('Nice=',normal)
        self.assertNotIn('tune2fs',staged)
        self.assertTrue(staged.endswith('exec /sbin/init'))
        self.assertNotIn('"',staged)
        with self.assertRaises(ValueError):
            stage_stock_seed(tuple(slot for slot in slots if slot!=21),accelerated=True)

    def test_seed_display_isolation_has_identical_media_and_cleanup_gates(self):
        from linux_early_uart import (PRIOR_LOCAL_FS, SEED_HMI_GUARD,
                                      stage_stock_seed, validate_clean_seed_policy)
        validate_clean_seed_policy(platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'),
                                   accelerated=True,no_display=True)
        slots=tuple(PRIOR_LOCAL_FS)
        stock=stage_stock_seed(slots,accelerated=True)
        idle=stage_stock_seed(slots,accelerated=True,no_display=True)
        self.assertEqual(stock.split('u/9.conf;',1)[0],idle.split('u/9.conf;',1)[0])
        self.assertIn('for i in vw-hmi weston displaymgr;',idle)
        self.assertNotIn('weston displaymgr',stock)
        self.assertIn(SEED_HMI_GUARD,idle)
        self.assertIn(r'Requires=sysinit.target local-fs.target\n',idle)
        self.assertLessEqual(len(idle),len(stock))
        with self.assertRaisesRegex(ValueError,'accelerated'):
            stage_stock_seed(slots,no_display=True)

    def test_accelerated_seed_window_cannot_extend_regular_native_boot(self):
        f=platform.accelerated_seed_window
        self.assertTrue(f(240,prepare=True,accelerated=True))
        self.assertTrue(f(600,prepare=True,accelerated=True))
        for seconds in (0,239,241,599,601,1200):
            self.assertFalse(f(seconds,prepare=True,accelerated=True))
        self.assertFalse(f(600,prepare=True))
        self.assertFalse(f(600,accelerated=True))

    def test_targeted_seed_uevents_change_only_early_coldplug(self):
        from linux_early_uart import (PRIOR_LOCAL_FS, seed_targeted_coldplug_unit,
                                     stage_stock_seed, trace_backup_settle_units)
        slots=tuple(PRIOR_LOCAL_FS)
        old,stock_drop=trace_backup_settle_units(slots,early_nav=True,early_nav_core=True,
                                                 single_batch=True)
        new,new_drop=seed_targeted_coldplug_unit(slots)
        self.assertEqual(stock_drop,new_drop)
        self.assertEqual(old.split('ExecStart=',1)[0],new.split('ExecStart=',1)[0])
        self.assertIn('26 18 20 3 21 19 22 29;',new)
        self.assertIn('echo add >/sys/block/sda/sda$$n/uevent||exit',new)
        original=stage_stock_seed(slots,accelerated=True,no_display=True)
        targeted=stage_stock_seed(slots,accelerated=True,no_display=True,direct_uevents=True)
        encoded=new.replace("'",r'\047').replace('\n',r'\n')
        self.assertEqual(targeted.replace(encoded,old.replace('\n',r'\n'),1),original)
        self.assertNotIn('"',targeted)
        self.assertLessEqual(2037+len(targeted)-len(original),2048)
        with self.assertRaisesRegex(ValueError,'accelerated'):
            stage_stock_seed(slots,direct_uevents=True)
        with self.assertRaises(ValueError):
            seed_targeted_coldplug_unit(tuple(i for i in slots if i!=21))

    def test_coldplug_read_only_mount_requires_original_clean_seed_proof(self):
        from linux_early_uart import CLEAN_SEED_MARKERS, prior_local_fs_slots
        from linux_pid1_context import LINUX_SHA
        row=dict(mountpoint='/nav',source='/dev/sda20',fstype='ext4',dev_major=259,dev_minor=3,read_only=True)
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            (p/'result.json').write_text('{}')
            (p/'ufs-vh1-data-session.qcow2').write_bytes(b'checked seed')
            (p/'integrity.json').write_text('{"original_unchanged":true,"exit_code":0}')
            (p/'linux-original-userspace.json').write_text(json.dumps(dict(kernel_sha256=LINUX_SHA,
                status='verified_process_snapshot',mounts_verified=True,mounts=[row])))
            self.assertEqual(prior_local_fs_slots(p),())
            path=p/'linux-clean-seed-proof.json'
            proof=dict(status='original_clean_seed_verified',session=p.name,original_unchanged=True,
                read_only_inspection=True,overlay_sha256=hashlib.sha256(b'checked seed').hexdigest(),mounts=[row],
                cleanup_markers=[dict(text=x) for x in CLEAN_SEED_MARKERS],
                sda29=dict(check_interval_seconds=86400,fs_state=1,journal_recovery=False),
                sda15=dict(fs_state=1,journal_recovery=False),sda26=dict(fs_state=1,journal_recovery=False),
                post_init_variant='0x65')
            path.write_text(json.dumps(proof))
            self.assertEqual(prior_local_fs_slots(p),())                                                    
            proof['navigation']=dict(archive='VW.03.680333-01.25.01_02-CA.tgz',regular_files=1728,
                directories=188,file_payload_bytes=643493992,all_file_sha256_match=True)
            path.write_text(json.dumps(proof))
            self.assertEqual(prior_local_fs_slots(p),(20,))
            (p/'ufs-vh1-data-session.qcow2').write_bytes(b'other seed')
            self.assertEqual(prior_local_fs_slots(p),())

    def test_post_init_priority_only_adds_dependency_order(self):
        from linux_early_uart import stage_post_init_priority
        stage=stage_post_init_priority()
        self.assertIn('/run/systemd/system/post-init.service.d',stage)
        self.assertIn(r'Requires=sysinit.target local-fs.target\n',stage)
        self.assertIn(r'After=sysinit.target local-fs.target trace.service\n',stage)
        self.assertIn(r'Before=shutdown.target\nConflicts=shutdown.target\n',stage)
        self.assertNotIn('ExecStart',stage)
        self.assertNotIn('fsck',stage)
        self.assertNotIn('"',stage)

    def test_oem_hmi_start_grace_only_extends_notify_start_timeout(self):
        from linux_early_uart import (COLDPLUG_COMMAND, COLDPLUG_COMPACT, PRIOR_LOCAL_FS,
                                      coldplug_dropin, stage_coldplug_order,
                                      stage_hmi_start_grace, validate_hmi_start_grace)
        validate_hmi_start_grace(platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
        stage=stage_hmi_start_grace()
        self.assertIn('/run/systemd/system/vw-hmi.service.d',stage)
        self.assertIn(r"printf '[Service]\nTimeoutStartSec=240\n'",stage)
        self.assertNotIn('ExecStart',stage)
        self.assertNotIn('READY',stage)
        self.assertNotIn('Watchdog',stage)
        self.assertNotIn('"',stage)
        interpreted=stage_hmi_start_grace(interpreter=True)
        self.assertEqual(interpreted.replace(r'Environment=_JAVA_OPTIONS=-Xint\n',''),stage)
        slots=tuple(PRIOR_LOCAL_FS)
        self.assertEqual(coldplug_dropin(slots,compact=True),
                         coldplug_dropin(slots).replace(COLDPLUG_COMMAND,COLDPLUG_COMPACT))
        self.assertLessEqual(len(stage_coldplug_order(slots,compact=True))+len(stage),
                             len(stage_coldplug_order(slots))+57)

    def test_oem_hmi_recording_preserves_stock_compiler_and_uart_policy(self):
        import linux_early_uart as early
        early.validate_hmi_recording(platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
        stage=early.stage_hmi_start_grace(flight_recording=True)
        extra=(f'Environment=_JAVA_OPTIONS=-XX:StartFlightRecording=duration={early.HMI_RECORDING_SECONDS}s,'
               f'filename={early.HMI_RECORDING_PATH}\\n')
        self.assertIn(extra,stage)
        self.assertEqual(stage.replace(extra,''),early.stage_hmi_start_grace())
        self.assertNotIn('ExecStart',stage)
        with self.assertRaisesRegex(ValueError,'original compiler mode'):
            early.stage_hmi_start_grace(interpreter=True,flight_recording=True)
        with self.assertRaisesRegex(ValueError,'exclusive native UART'):
            early.stage_script(early.TTY,compact=True)
        compact=early.stage_script(early.DIRECT_TTY,compact=True)
        self.assertIn(' '.join(early.COMMAND),compact)
        self.assertTrue(compact.endswith('exec /sbin/init'))
        self.assertLess(len(compact)+len(extra),len(early.stage_script(early.DIRECT_TTY)))
        vmlog=early.stage_hmi_start_grace(vm_log=True)
        line=(r'Environment=\047_JAVA_OPTIONS=-XX:+LogVMOutput -XX:LogFile='
              +early.HMI_VM_LOG_PATH+r'\047\n')
        self.assertEqual(vmlog.replace(line,''),early.stage_hmi_start_grace())
        self.assertLess(len(compact)+len(line),len(early.stage_script(early.DIRECT_TTY)))
        with self.assertRaisesRegex(ValueError,'Choose one'):
            early.stage_hmi_start_grace(flight_recording=True,vm_log=True)

    def test_oem_hmi_userdata_stays_in_one_native_data_slot(self):
        from types import SimpleNamespace
        from linux_hmi_userdata import _other_allocations, copy_slot
        def extent(start, length, depth=0, present=True):
            return dict(start=start,length=length,depth=depth,present=present)
        rows=[extent(0,4),extent(4,12),extent(16,4),extent(20,4,1),extent(24,4,present=False)]
        self.assertEqual(_other_allocations(rows,8,4),[(0,8),(12,20)])
        source=SimpleNamespace(size=4<<30,flags=2)
        target=SimpleNamespace(size=4<<30,flags=4)
        for first,length in ((0,1<<30),(4096,1<<30),(1<<30,2<<30),(4<<30,1<<30)):
            with self.subTest(first=first,length=length), self.assertRaisesRegex(ValueError,'one bounded'):
                copy_slot(source,target,first,length)
        with self.assertRaisesRegex(ValueError,'one bounded'):
            copy_slot(target,source,1<<30,1<<30)

    def test_native_performance_cpu_sets_use_only_allowed_fast_cores(self):
        from native_cpu_policy import parse_cpu_sets, select_fast
        def rec(ident,index,eff,flags=0,group=0):
            return struct.pack('<IIIH6B',20,0,ident,group,index,index,0,0,eff,flags)
        payload=rec(10,0,0)+rec(11,1,1)+rec(12,2,1,2)+rec(13,3,1,1)+rec(14,4,1,6)
        self.assertEqual([r['id'] for r in select_fast(parse_cpu_sets(payload),0xf)],[11,13])
        self.assertEqual([r['id'] for r in select_fast(parse_cpu_sets(payload),0x1f)],[11,13,14])
        with self.assertRaisesRegex(ValueError,'distinct'):
            select_fast(parse_cpu_sets(payload),1)
        with self.assertRaisesRegex(ValueError,'record length'):
            parse_cpu_sets(payload[:-1])
        with self.assertRaisesRegex(ValueError,'single Windows'):
            select_fast(parse_cpu_sets(payload+rec(15,5,2,group=1)),0x3f)

    def test_native_seed_split_keeps_three_distinct_performance_cores(self):
        from native_cpu_policy import linux_seed_cpu_pools
        rows = [dict(id=100+i, group=0, index=i, core=(i//2 if i<8 else i),
                     efficiency=int(i<8), parked=i in (1,3,5,7), allocated=False, owned=False)
                for i in range(16)]
        pools = linux_seed_cpu_pools(rows, 0xffff)
        self.assertEqual([pools[i] for i in (1,2,5)], [[100],[102],[104]])
        self.assertTrue(all(pools[i] == list(range(108,116)) for i in (0,3,4,6,7)))
        with self.assertRaisesRegex(ValueError, 'five other'):
            linux_seed_cpu_pools(rows, 0x0fff)
        with self.assertRaisesRegex(ValueError, 'three fast'):
            linux_seed_cpu_pools(rows, 0xff0f)

    def test_native_qnx_split_keeps_other_ap_off_both_smt_cores(self):
        from native_cpu_policy import qnx_display_cpu_pools
        rows = [dict(id=100+i, group=0, index=i, core=(i//2 if i<8 else i),
                     efficiency=int(i<8), parked=i in (1,3,5,7), allocated=False, owned=False)
                for i in range(16)]
        rows[8].update(allocated=True)
        pools=qnx_display_cpu_pools(rows,0xffff,(0,4))
        self.assertEqual((pools[0],pools[4]),([100],[102]))
        other={i: ids for i,ids in pools.items() if i not in (0,4)}
        self.assertTrue(all(ids==[104,105,106,107,109,110,111,112,113,114,115]
                            for ids in other.values()))
        with self.assertRaisesRegex(ValueError,'AP0/AP4'):
            qnx_display_cpu_pools(rows,0xffff,(0,5))
        with self.assertRaisesRegex(ValueError,'separate active fast'):
            qnx_display_cpu_pools(rows,0xff0f,(0,4))
        with self.assertRaisesRegex(ValueError,'six other'):
            qnx_display_cpu_pools(rows,0x037f,(0,4))

    def test_native_parallel_tcg_never_mixes_instruction_clock_or_boot_steps(self):
        serial,clock=platform.tcg_execution_policy(False,True,96000000,8,0,1,'win32')
        self.assertEqual((serial,clock['clock']),('tcg,thread=single','instruction'))
        parallel,clock=platform.tcg_execution_policy(True,True,96000000,8,0,None,'win32')
        self.assertEqual((parallel,clock['clock'],clock['auxiliary_vcpu_contexts']),
                         ('tcg,thread=multi,x-auxiliary-vcpu=on','qemu-virtual-wall',1))
        self.assertFalse(clock['physical_cycle_accuracy_claimed'])
        for board,mgmt,cpus,steps,icount,host in ((True,1,8,0,1,'win32'),(True,1,8,1,None,'win32'),
                (False,1,8,0,None,'win32'),(True,0,8,0,None,'win32'),(True,1,1,0,None,'win32'),
                (True,1,8,0,None,'linux')):
            with self.assertRaisesRegex(ValueError,'Native parallel TCG'):
                platform.tcg_execution_policy(True,board,mgmt,cpus,steps,icount,host)

    def test_native_parallel_requires_actual_auxiliary_thread_not_phantom_smp(self):
        rows=[{'cpu-index':i,'thread-id':i+21,'qom-path':f'/machine/unattached/device[{i}]'} for i in range(8)]
        aux={'cpu-index':8,'thread-id':42,'qom-path':'/machine/exynos-apm-core/management-cluster/armv7m/cpu'}
        self.assertEqual(platform.native_parallel_threads(rows+[aux],8)[-1]['role'],'acpm')
        for bad in (rows,rows+[dict(aux,**{'thread-id':21})],rows+[dict(aux,**{'cpu-index':7})],
                    rows+[dict(aux,**{'qom-path':'/machine/unattached/device[8]'})]):
            with self.assertRaisesRegex(ValueError,'distinct host threads'):
                platform.native_parallel_threads(bad,8)

    def test_local_vm3_usi03_does_not_change_oem_login(self):
        import linux_early_uart as early
        import linux_uart_platform as uart
        stage=early.stage_script(early.DIRECT_TTY)
        self.assertIn('</dev/ttySAC3 >/dev/ttySAC3 2>&1',stage)
        self.assertIn(r"PS1='8704-vm3$\n'",stage)
        self.assertNotIn('ttySAC0',stage)
        self.assertNotIn('serial-getty',stage)
        profile=uart.policy(uart.PROFILE,True)
        args,detail=early.direct_serial(Path('D:/session'),32229,profile)
        self.assertEqual(args[-2:],['-serial','chardev:oem-linux-usi03'])
        self.assertEqual((detail['vmid'],detail['serial_index'],detail['physical'],detail['spi']),
                         (3,3,0x10360000,351))

    def test_boot_trace_keeps_native_sector_evidence_and_failures(self):
        boot=set(platform.ufs_runtime_trace_events('boot', True))
        full=set(platform.ufs_runtime_trace_events('errors', True))
        self.assertIn('ufs_native_read_result',boot)
        self.assertIn('ufs_process_uiccmd',boot)
        self.assertIn('ufs_err_dma_read_prdt',boot)
        chatter={'ufs_mmio_write','ufs_irq_raise','ufs_irq_lower','ufs_complete_req','blk_co_preadv'}
        self.assertFalse(boot&chatter)
        self.assertTrue(chatter<=full)

    def test_native_rx_tracks_vfifo_without_exporting_command_bytes(self):
        import linux_uart_platform as u
        u.native_rx_contract()
        raw=bytearray(u.VLM_RX_CAPTURE)
        def put(fmt,addr,value):
            struct.pack_into(fmt,raw,addr-u.VLM_RAM,value)
        q=lambda a,v:put('<Q',a,v)
        w=lambda a,v:put('<I',a,v)
        table,ring,vcons,tx,rx,buf,uart=(u.VLM_RAM+i for i in
            (0x300000,0x301000,0x500000,0x501000,0x502000,0x503000,0x504000))
        q(u.VLM_RX_TABLE,table); q(table+3*8,ring); q(u.VLM_VCONS,vcons)
        w(ring+8,2); w(ring+12,2); w(ring+0x10,0x410d)
        w(vcons,3); w(vcons+4,1); q(vcons+0x10,tx)
        q(tx,buf); q(tx+8,rx); w(tx+0x14,2); w(rx+0x14,1); w(rx+0x18,1)
        w(buf+0x20,256); w(buf+0x24,255); q(buf+0xd0,13); q(buf+0xd8,65)
        q(rx+0x28,u.VLM_VSUART_RX); q(rx+0x30,uart); q(uart+0x100,rx)
        w(uart+0x10,377); w(uart+0x18,1); w(uart+0x48,1)
        result=u.native_rx_snapshot(raw)['vmids']['3']
        self.assertEqual(result['ring'],dict(produced=2,consumed=2,pending=0,recent_samples=2,recent_cr=1))
        row,=result['vfifos']
        self.assertEqual((row['produced'],row['consumed'],row['recent_cr']),(2,1,1))
        self.assertEqual(row['virtual_uart'],dict(irq_id=377,rx_mode=1,rx_pending=True,
                                                 rx_masked=False,deferred_events=0))
        self.assertNotIn('bytes',row)

    def test_pscdc_behavior_is_explicit_and_requires_management_core(self):
        import pscdc_platform
        self.assertEqual(pscdc_platform.machine_options(None),'')
        self.assertEqual(pscdc_platform.machine_options('virtual-oem-v1',64,96000000),
                         ',x-exynos-pscdc-profile=1,x-exynos-pscdc-cycles=64')
        for profile,cycles,clock in ((None,1,0),('unknown',64,96000000),
                                    ('virtual-oem-v1',64,0),('virtual-oem-v1',0,96000000),
                                    ('virtual-oem-v1',1000001,96000000)):
            with self.assertRaises(ValueError):pscdc_platform.machine_options(profile,cycles,clock)

    def test_pscdc_policy_does_not_claim_silicon_or_patch_factory_images(self):
        import pscdc_platform
        policy=pscdc_platform.profile_policy('virtual-oem-v1',64)
        self.assertFalse(policy['silicon_instruction_semantics_verified'])
        self.assertFalse(policy['physical_transition_timing_verified'])
        self.assertFalse(policy['factory_images_modified'])
        self.assertFalse(policy['runtime_ready_value_injected'])
        self.assertTrue(any('Logical pause' in line for line in policy['assumptions']))

    def test_pscdc_work_owns_busy_and_has_cancellable_timer(self):
        source=(platform.vm.ROOT/'src/emu/exynos-auto9-cmu.inc.c').read_text(encoding='utf-8')
        self.assertIn('s->queue_control = value | 0x100',source)
        self.assertIn('timer_del(s->pscdc_deadline)',source)
        self.assertIn('exynos_auto9_pscdc_sources_ready(s)',source)
        self.assertIn('if (s->pscdc_profile)',source)

    def test_arm32_local_base_preserves_thumb_mclass_system_instruction(self):
        raw=b'\x00\0'*0x12c+b'\x80\xf3\x08\x88'
        wrapped=platform.arm32_analysis_elf(raw,0x3259,0x3000)
        self.assertEqual(wrapped[4096:],raw)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'mclass.elf';path.write_bytes(wrapped)
            result=platform.elf_window(path,0x3258,4)
            self.assertEqual(result['instructions'][0]['mnemonic'],'msr')
            self.assertIn('msp',result['instructions'][0]['operands'])
            with self.assertRaises(ValueError):platform.elf_window(path,0x258,4)
        with self.assertRaises(ValueError):platform.arm32_analysis_elf(raw,0x3259,0x3001)

    def test_mclass_vectors_allow_nonzero_reserved_and_absent_nmi(self):
        words=[0x10000,0x81,0,0x91,0,0,0,0x91,0x91,0x91,0x91,0x91,0,0x91,0xa1,0xb1]
        result=platform.mclass_vector_candidates(struct.pack('<16I',*words))
        self.assertEqual(result[0]['file_offset'],'0x0')
        self.assertFalse(result[0]['reserved_vectors_all_zero'])
        self.assertEqual(platform.mclass_vector_candidates(struct.pack('<16I',0x14000000,*([0xd503201f]*15))),[])
        self.assertEqual(platform.mclass_vector_candidates(b'\0'*128),[])

    def test_lk_linux_root_and_slot_are_derived_not_kernel_patches(self):
        source=platform.factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes()
        nodes=platform.parse_dtb(source)
        original_args=nodes['/chosen']['bootargs']
        derived,meta=platform.lk_linux_boot_dtb(source,'a')
        updated=platform.parse_dtb(derived)
        args=updated['/chosen']['bootargs']
        self.assertIn(b'root=/dev/dm-0 ',args)
        self.assertIn(b'bootloader.boot.om=a ',args)
        self.assertIn(b'panic_on_corruption',args)
        self.assertEqual(args.split(b'dm-mod.create=',1)[1].split(b'"')[1],original_args.split(b'dm-mod.create=',1)[1].split(b'"')[1])
        updated['/chosen']['bootargs']=original_args
        self.assertEqual(updated,nodes)
        self.assertEqual(meta['changed_properties'],['/chosen/bootargs'])

    def test_lk_linux_b_slot_changes_only_original_verity_devices(self):
        source=platform.factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes()
        a,_=platform.lk_linux_boot_dtb(source,'a');b,_=platform.lk_linux_boot_dtb(source,'b')
        a_nodes=platform.parse_dtb(a);b_nodes=platform.parse_dtb(b)
        args=b_nodes['/chosen']['bootargs']
        self.assertIn(b'/dev/sda31 /dev/sda55 ',args)
        self.assertEqual(args.replace(b'/dev/sda31 ',b'/dev/sda14 ').replace(b'/dev/sda55 ',b'/dev/sda27 ').replace(b'bootloader.boot.om=b ',b'bootloader.boot.om=a '),a_nodes['/chosen']['bootargs'])
        b_nodes['/chosen']['bootargs']=a_nodes['/chosen']['bootargs']
        self.assertEqual(a_nodes,b_nodes)

    def test_lk_linux_contract_mismatch_rejected(self):
        source=platform.factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes()
        nodes=platform.parse_dtb(source);args=nodes['/chosen']['bootargs']
        bad=(args[:-1],args+b'x',args.replace(b'/dev/sda14 ',b'/dev/sda15 '),
            b'root=/dev/sda2 '+args,args[:-1]+b' bootloader.boot.om=a bootloader.boot.om=b\0')
        for argument in bad:
            nodes['/chosen']['bootargs']=argument
            with self.subTest(argument_length=len(argument)):
                with self.assertRaises(ValueError):platform.lk_linux_boot_dtb(platform.rebuild_fdt(source,nodes),'a')
        with self.assertRaises(ValueError):platform.lk_linux_boot_dtb(source,'c')

    def test_arm64_materialization_candidate_and_branch_boundary(self):
                                                
        words=(0xb0000000,0x9108d001,0xd65f03c0)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'refs.elf'
            path.write_bytes(platform.analysis_elf(struct.pack('<3I',*words)))
            self.assertEqual(platform.arm64_materialized_refs(path,0x1234)['candidates'][0]['address'],'0x4')
                                                                            
            path.write_bytes(platform.analysis_elf(struct.pack('<4I',words[0],0x14000001,words[1],words[2])))
            self.assertEqual(platform.arm64_materialized_refs(path,0x1234)['candidates'],[])

    def test_arm32_analysis_wrapper_keeps_original_bytes(self):
        raw=b'\0'*128+b'\x00\xbf\x70\x47'
        wrapped=platform.arm32_analysis_elf(raw,0x81)
        self.assertEqual(wrapped[4096:],raw)
        self.assertEqual(struct.unpack_from('<H',wrapped,18)[0],40)
        self.assertEqual(struct.unpack_from('<I',wrapped,24)[0],0x81)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'candidate.elf';path.write_bytes(wrapped)
            result=platform.elf_window(path,128,4)
            self.assertEqual(result['bytes_hex'],'00bf7047')
            self.assertEqual([r['mnemonic'] for r in result['instructions']],['nop','bx'])
            with self.assertRaises(ValueError):platform.elf_window(path,130,4)

    def test_arm32_invalid_entry_and_truncated_header(self):
        for entry in (-1,4):
            with self.assertRaises(ValueError):platform.arm32_analysis_elf(b'abcd',entry)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.elf';path.write_bytes(b'\x7fELF')
            with self.assertRaises(ValueError):platform.elf_window(path,0,4)
            raw=bytearray(platform.arm32_analysis_elf(b'abcd',1))
            struct.pack_into('<I',raw,28,0xfffffff0);path.write_bytes(raw)
            with self.assertRaises(ValueError):platform.elf_window(path,0,4)

    def client(self,data):
        result=platform.RSP.__new__(platform.RSP);result.socket=Stream(data);return result

    def test_rsp_packet_checksum_and_ack(self):
        client=self.client(b'+'+packet(b'OK'))
        self.assertEqual(client.receive(),b'OK');self.assertEqual(client.socket.sent,[b'+'])

    def test_rsp_wrong_checksum_rejected(self):
        client=self.client(b'$OK#00')
        with self.assertRaises(ValueError):client.receive()
        self.assertEqual(client.socket.sent,[b'-'])

    def test_rsp_binary_escape(self):
        client=self.client(packet(b'A}'+bytes([ord('#')^32])+b'B'))
        self.assertEqual(client.receive(),b'A#B')

    def test_rsp_repeat_decoding(self):
        self.assertEqual(self.client(packet(b'0* ')).receive(),b'0000')

    def test_rsp_repeat_without_previous_rejected(self):
        with self.assertRaises(ValueError):self.client(packet(b'* ')).receive()

    def test_rsp_missing_packet_bounded(self):
        with self.assertRaises(ValueError):self.client(b'+'*256).receive()

    def test_rsp_disconnection_explicit(self):
        with self.assertRaises(OSError):self.client(b'').receive()

    def test_arm64_register_decoding(self):
        client=self.client(b'')
        client.command=Mock(return_value=(struct.pack('<33QI',*range(33),0x3c9)).hex().encode())
        registers=client.registers()
        self.assertEqual(registers['x0'],'0x0');self.assertEqual(registers['pc'],'0x20');self.assertEqual(registers['cpsr'],'0x3c9')

    def test_elf_short_and_wrong_architecture(self):
        for data in (b'',b'not-elf',b'\x7fELF\x02\x01'+b'\0'*58):
            with self.assertRaises(ValueError):platform.Elf(data)

    def test_elf_original_entry_and_hvc_selectors(self):
        elf=platform.Elf(platform.original('hypervisor').read_bytes())
        self.assertEqual(elf.entry,0x80080330)
        symbols={row['name']:row for row in elf.symbols}
        functions={row['address']:row['name'] for row in elf.symbols if row['type']==2}
        table=struct.unpack('<128Q',elf.at(symbols['nk_hyp_calls']['address'],1024))
        self.assertEqual(functions[table[22]],'_hyp_call_prop_get')
        self.assertEqual(functions[table[54]],'_hyp_call_xirq_sysconf')
        self.assertEqual(functions[table[57]],'_hyp_call_get_cpuid')
        self.assertEqual(functions[table[60]],'_hyp_call_mem_map_get')

    def test_elf_address_not_file_backed(self):
        elf=platform.Elf(platform.original('hypervisor').read_bytes())
        with self.assertRaises(ValueError):elf.at(0,16)

    def test_original_hcb_has_three_distinct_vplatform_records(self):
        data=platform.original('hcb/hcb.img').read_bytes()
        for vmid in (2,3,4):self.assertIn(('VDTVM'+str(vmid)).encode()+b'\0\0\xd0\x0d\xfe\xed',data)

    def test_fdt_rebuild_roundtrip_preserves_every_property(self):
        data=platform.original('hcb/hcb.img').read_bytes()
        size=struct.unpack_from('>I',data,4)[0]
        nodes=platform.parse_dtb(data[:size])
        rebuilt=platform.rebuild_fdt(data[:size],nodes)
        self.assertEqual(platform.parse_dtb(rebuilt),nodes)

    def test_hardware_ram_matches_qemu_supplied_bank(self):
        raw=platform.original('hcb/hcb.img').read_bytes()
        derived,metadata=platform.hardware_hcb_copy(raw)
        size=struct.unpack_from('>I',derived,4)[0]
        nodes=platform.parse_dtb(derived[:size])
        self.assertEqual(nodes['/memory@80000000']['reg'],struct.pack('>QI',0x80000000,0x80000000))
        self.assertEqual(metadata['banks'][0]['bytes'],0x80000000)
        self.assertEqual(derived[derived.index(b'VDTHYP\0\0'):],raw[raw.index(b'VDTHYP\0\0'):])

    def test_hwp_aliases_hyp_cluster_watchdog_compatible(self):
        raw=platform.original('hcb/hcb.img').read_bytes()
        original=platform.parse_dtb(raw[:struct.unpack_from('>I',raw,4)[0]])
        self.assertEqual(original['/watchdog_cl0@10050000']['compatible'],b'samsung,exynosauto9-v1-wdt\0')
        self.assertEqual(original['/watchdog_cl1@10060000']['compatible'],b'samsung,exynosauto9-v2-wdt\0')
        derived,metadata=platform.hardware_hcb_copy(raw)
        nodes=platform.parse_dtb(derived[:struct.unpack_from('>I',derived,4)[0]])
        self.assertEqual(nodes['/watchdog_cl0@10050000']['compatible'],
                         b'samsung,exynos9-v1-wdt\0samsung,exynosauto9-v1-wdt\0')
        self.assertEqual(nodes['/watchdog_cl1@10060000']['compatible'],b'samsung,exynosauto9-v2-wdt\0')
        self.assertIn('watchdog_cl0@10050000',metadata['hyp_watchdog_compatible'])

    def test_cluster_watchdog_maps_hwp_apertures(self):
        text=Path(__file__).with_name('exynos-auto9-apm-wdt.inc.c').read_text(encoding='utf-8')
        self.assertIn('exynos_auto9_cluster_wdt_create(vms, "exynos-cluster-wdt0", 0x10050000);',text)
        self.assertIn('exynos_auto9_cluster_wdt_create(vms, "exynos-cluster-wdt1", 0x10060000);',text)
        self.assertIn('object_property_set_uint(OBJECT(dev), "profile", 1, &error_fatal);',text)
        self.assertIn('EXYNOS_AUTO9_APM_WDT(dev)->tick = 0;',text)

    def test_existing_hardware_ram_is_not_overwritten(self):
        raw=platform.original('hcb/hcb.img').read_bytes()
        derived,_=platform.hardware_hcb_copy(raw)
        with self.assertRaises(ValueError):platform.hardware_hcb_copy(derived)

    def test_payload_reservation_changes_only_one_property(self):
        raw=platform.original('hcb/hcb.img').read_bytes()
        derived,metadata=platform.payload_sized_hcb(raw,178302976)
        def config(data):
            start=data.index(b'VDTHYP\0\0')+8
            size=struct.unpack_from('>I',data,start+4)[0]
            return platform.parse_dtb(data[start:start+size])
        old,new=config(raw),config(derived)
        path='/vlm/vm@2/vm-memory/kernel-region'
        self.assertEqual(struct.unpack('>QQ',new[path]['reg']),(0x1080000,178302976))
        new[path]['reg']=old[path]['reg']
        self.assertEqual(new,old)
        self.assertEqual(metadata['physical_base'],'0x91080000')
        self.assertEqual(derived[derived.index(b'VDTVM2\0\0'):],raw[raw.index(b'VDTVM2\0\0'):])

    def test_payload_bounds_reject_zero_and_overflow(self):
        data=platform.original('hcb/hcb.img').read_bytes()
        for size in (0,-1,0x10000000):
            with self.assertRaises(ValueError):platform.payload_sized_hcb(data,size)

    def test_payload_size_rounds_up_and_keeps_kernel_offset(self):
        data=platform.original('hcb/hcb.img').read_bytes()
        _,metadata=platform.payload_sized_hcb(data,4097)
        self.assertEqual(metadata['derived_bytes'],8192)
        self.assertEqual(metadata['physical_base'],'0x91080000')

    def test_invalid_cbi_record_is_rejected(self):
        for data,tag in ((b'',b'VDTHYP\0\0'),(b'',b'bad')):
            with self.assertRaises(ValueError):platform.replace_cbi_record(data,tag,lambda _:None)

    def test_local_loader_ram_has_actual_separate_bank(self):
        raw=platform.original('hcb/hcb.img').read_bytes()
        derived,metadata=platform.hardware_hcb_copy(raw,0x40000000)
        nodes=platform.parse_dtb(derived[:struct.unpack_from('>I',derived,4)[0]])
        self.assertEqual(nodes['/memory@80000000']['reg'],struct.pack('>QIQI',0x80000000,0x80000000,0xc00000000,0x40000000))
        self.assertEqual(metadata['banks'][1],{'base':'0xc00000000','bytes':0x40000000})

    def test_loader_ram_arbitrary_size_is_rejected(self):
        with self.assertRaises(ValueError):platform.hardware_hcb_copy(platform.original('hcb/hcb.img').read_bytes(),123)

    def test_linux_factory_bank_keeps_guest_cbi_and_rejects_conflicting_owner(self):
        raw=platform.original('hcb/hcb.img').read_bytes()
        derived,metadata=platform.hardware_hcb_copy(raw,0x40000000,linux_factory_bank=True)
        nodes=platform.parse_dtb(derived[:struct.unpack_from('>I',derived,4)[0]])
        self.assertEqual(list(struct.iter_unpack('>QI',nodes['/memory@80000000']['reg'])),
                         [(0x80000000,0x80000000),(0xc00000000,0x40000000),(0xa80000000,0x80000000)])
        self.assertEqual(metadata['banks'][-1]['owner'],'factory VM3')
        bank=platform.LINUX_FACTORY_HIGH_BANK
        self.assertTrue(platform.native_low_or_high_ram(0xaf9f38000,0x4000,bank))
        self.assertFalse(platform.native_low_or_high_ram(0xaf9f38000,0x4000))
        self.assertFalse(platform.native_low_or_high_ram(0xafffff000,0x4000,bank))
        tag=b'VDTHYP\0\0'
        self.assertEqual(derived[derived.index(tag):],raw[raw.index(tag):])
        def conflict(nodes):
            props=nodes['/vlm/vm@4/vm-memory']
            props['reg']+=struct.pack('>QQ',0xa80000000,0x200000)
        with self.assertRaisesRegex(ValueError,'overlapping guests'):
            platform.linux_factory_high_bank(platform.replace_cbi_record(raw,tag,conflict))

    def test_qnx_factory_bank_does_not_back_the_wider_dtb_region(self):
        raw=platform.original('hcb/hcb.img').read_bytes()
        derived,meta=platform.hardware_hcb_copy(raw,0x40000000,
                                                linux_factory_bank=True,qnx_factory_bank=True)
        self.assertEqual(platform.qnx_factory_high_bank(raw),(0x880000000,0x4e000000))
        hwp=platform.parse_dtb(derived[:struct.unpack_from('>I',derived,4)[0]])
        self.assertIn((0x880000000,0x4e000000),list(struct.iter_unpack(
            '>QI',hwp['/memory@80000000']['reg'])))
        self.assertEqual(meta['banks'][-1]['owner'],'factory VM2')
        tag=b'VDTHYP\0\0'
        self.assertEqual(derived[derived.index(tag):],raw[raw.index(tag):])
        def conflict(nodes):
            nodes['/vlm/vm@3/vm-memory']['reg']+=struct.pack('>QQ',0x880000000,0x200000)
        with self.assertRaisesRegex(ValueError,'overlapping guests'):
            platform.qnx_factory_high_bank(platform.replace_cbi_record(raw,tag,conflict))

    def test_native_vgen_apertures_and_five_layouts(self):
        rows=platform.native_vgen_layout()
        self.assertEqual(len(rows),20)
        self.assertEqual({row['layout'] for row in rows},set(range(5)))
        self.assertEqual(len({row['base'] for row in rows}),20)
        self.assertEqual(next(row for row in rows if row['base']==0x19a30000)['layout'],4)

    def test_s2mpu_native_interrupt_routes(self):
        rows=platform.native_s2mpu_layout()
        self.assertEqual(len(rows),22)
        self.assertEqual(next(row for row in rows if row['base']==0x17ca0000)['irq'],384)
        self.assertEqual(next(row for row in rows if row['base']==0x18dc0000)['controller'],'exynos-dintc2')

    def test_gpio_native_pin_widths(self):
        rows=platform.native_gpio_layout()
        self.assertEqual(len(rows),5)
        row=next(row for row in rows if row['base']==0x10230000)
        self.assertEqual(row['pins'],[8,8,8,5]);self.assertEqual(row['irq'],338)

    def test_alive_and_aud_pin_banks_are_factory_records(self):
        alive=platform.native_alive_gpio_layout();aud=platform.native_aud_gpio_layout()
        self.assertEqual(alive['base'],0x10450000);self.assertEqual(alive['pins'],[8,2,2])
        self.assertEqual(alive['pin_irqs'],list(range(13,23)))
        self.assertEqual(aud['base'],0x19c60000);self.assertEqual(aud['pins'],[5,8,8,8])
        self.assertFalse(aud['irq_route_verified'])

    def test_4k_walk_real_descriptor_layout_and_page_offset(self):
        far=0xffffffc010035024;memory={0xb8ea6800:0xe4bbf003,0xe4bbf400:0xe4bbe003,0xe4bbe1a8:0x68000010000707}
        result=platform.walk_guest_4k(far,0x400034b5593519,0xb8ea6000,memory.__getitem__)
        self.assertEqual(result['physical_target'],'0x10000024');self.assertEqual(len(result['walk']),3)

    def test_walk_invalid_and_other_granules_are_rejected(self):
        for tcr in (0,0x400034b5593519):
            with self.assertRaises(ValueError):platform.walk_guest_4k(0xffffffc010035000,tcr,0x1000,lambda _:0)

    def test_symbolized_container_roundtrips_original_bytes(self):
        raw=b'\x1f\x20\x03\xd5'*32;base=0xffffffc010080000
        symbols=[{'name':'_text','type':'T','address':hex(base)},{'name':'data','type':'D','address':hex(base+32)}]
        parsed=platform.Elf(platform.symbolized_analysis_elf(raw,symbols))
        self.assertEqual(parsed.at(base,len(raw)),raw)
        self.assertEqual({r['name']:r['address'] for r in parsed.symbols},{'_text':base,'data':base+32})

    def test_lk_container_keeps_entire_original(self):
        original=platform.original('lk-boot/lk.bin').read_bytes()
        container=platform.analysis_elf(original)
        self.assertEqual(container[4096:],original)
        parsed=platform.Elf(container)
        self.assertEqual(parsed.entry,0);self.assertEqual(parsed.at(0,len(original)),original)

    def test_loaded_kernel_sizes_and_no_ivi_initrd(self):
        raw=platform.original('hcb/hcb.img').read_bytes()
        data,changes=platform.loaded_kernels_hcb(raw,[{'vmid':3,'kernel_bytes':18522112}])
        self.assertEqual(changes[0]['physical'],'0xb8080000')
        self.assertEqual(changes[0]['after_bytes'],18522112)
        self.assertEqual(changes[1]['after_bytes'],0)
        self.assertEqual(data[data.index(b'VDTVM2\0\0'):],raw[raw.index(b'VDTVM2\0\0'):])

    def test_loaded_kernel_cannot_cross_bank(self):
        with self.assertRaises(ValueError):platform.loaded_kernels_hcb(platform.original('hcb/hcb.img').read_bytes(),[{'vmid':3,'kernel_bytes':0xffffffff}])

    def test_gdb_xml_external_dtd_prefix_is_bound_locally(self):
        client=self.client(b'')
        target=b'l<?xml version="1.0"?><target version="1.0"><xi:include href="core.xml"/><xi:include href="sys.xml"/></target>'
        core=b'l<feature><reg name="x0" bitsize="64" regnum="0"/><reg name="pc" bitsize="64" regnum="32"/></feature>'
        system=b'l<feature><reg name="ESR_EL1" bitsize="64" regnum="100"/><reg name="FAR_EL1" bitsize="64"/></feature>'
        client.command=Mock(side_effect=[b'PacketSize=1000',target,core,system])
        registers,documents=client.xml_registers()
        self.assertEqual(registers['ESR_EL1']['number'],100)
        self.assertEqual(registers['FAR_EL1']['number'],101)
        self.assertEqual(len(documents),3)

    def test_gdb_management_inferior_never_uses_arm64_packet_offsets(self):
        client=self.client(b'')
        a64={'X0':{'number':0,'bits':64}}
        m3={name:{'number':number,'bits':32} for name,number in (('R0',0),('SP',13),('LR',14),('PC',15),('XPSR',25))}
        client.xml_registers=Mock(side_effect=[(a64,{}),(a64,{}),(m3,{})])
        client.registers=Mock(return_value={'pc':'0x80080330'})
        def command(text):
            if text=='qfThreadInfo':return b'mp1.1,p2.9'
            if text=='qsThreadInfo':return b'l'
            if text.startswith('Hg'):return b'OK'
            if text.startswith('p'):return struct.pack('<I',0x3258 if text=='pf' else 0xb000).hex().encode()
            if text=='me000ed28,18':return b'00'*24
            raise AssertionError(text)
        client.command=Mock(side_effect=command)
        result=client.system_snapshot()
        self.assertEqual(client.registers.call_count,1)
        self.assertEqual(result['cpus'][1]['architecture'],'arm-m')
        self.assertEqual(result['cpus'][1]['core']['pc'],'0x3258')
        self.assertEqual(result['cpus'][1]['system'],{})

    def test_boot_selector_resolves_architecture_not_cluster_relative_thread_id(self):
        client=self.client(b'')
        m3={'R0':{'number':0,'bits':32}};a64={'X0':{'number':0,'bits':64}}
        client.command=Mock(side_effect=[b'PacketSize=1000',b'mp02.09,p03.01',b'l',b'OK',b'OK'])
        client.xml_registers=Mock(side_effect=[(m3,{}),(a64,{})])
        client.registers=Mock(return_value={'pc':'0x80080330'})
        self.assertEqual(client.select_application_cpu(),'p03.01')
        self.assertEqual(client.registers.call_count,1)

    def test_apm_patch_is_opt_in_and_does_not_prefill_runtime_header(self):
        root=platform.vm.ROOT/'var/inputs/support/vlm-exynos-bringup/qemu-baseline/qemu-11.1.0'
        result=patch.edits('hw/arm/virt.c',(root/'hw/arm/virt.c').read_text(encoding='utf-8'))
        self.assertIn('if (!vms->exynos_acpm) { return; }',result)
        self.assertIn('mc->minimum_page_bits = 10;',result)
        self.assertIn('armv7m_load_kernel(s->arm->cpu, vms->exynos_acpm_firmware',result)
        self.assertIn('qdev_connect_clock_in(armdev, "refclk", s->clock)',result)
        self.assertIn('qemu_system_debug_request()',result)
        self.assertNotIn('object_property_add_bool_ptr',result)

    def test_sci_model_uses_queued_work_and_actual_peripheral_forwarding(self):
        root=platform.vm.ROOT/'src/emu'
        sci=(root/'exynos-auto9-sci.inc.c').read_text(encoding='utf-8')
        core=(root/'exynos-auto9-apm-core.inc.c').read_text(encoding='utf-8')
        self.assertIn('timer_mod(s->completion',sci)
        self.assertIn('s->busy ? 4 : 0',sci)
        self.assertIn('value & ~4U',sci)
        self.assertIn('address_space_read(&address_space_memory',core)
        self.assertIn('address_space_write(&address_space_memory',core)
        self.assertIn('if (result != MEMTX_OK)',core)

    def test_cmu_uses_original_ports_and_real_clock_timer(self):
        ports=platform.native_cmu_layout()
        self.assertEqual(len(ports),39)
        self.assertIn(0x1b240000,[r['base'] for r in ports])
        source=(platform.vm.ROOT/'src/emu/exynos-auto9-cmu.inc.c').read_text(encoding='utf-8')
        self.assertIn('timer_mod(p->deadline',source)
        self.assertIn('clock_update(p->raw',source)
        self.assertIn('value & ~AUTO9_PLL_LOCKED',source)
        self.assertIn('!m || !divisor || !cycles',source)
        self.assertIn('memory_region_add_subregion(get_system_memory()',source)
        self.assertNotIn('sysbus_init_mmio',source)                                           

    def test_pmic_bootstrap_constants_are_original_and_condition_is_explicit(self):
        import pmic_bootstrap
        result=pmic_bootstrap.contract()
        self.assertEqual(len(result['writes']),5)
        self.assertEqual([r['value'] for r in result['writes']],[0x16,0x16,0x7c,0xfc,0x7c])
        self.assertFalse(result['physical_pmic_identity_verified'])
        self.assertFalse(result['full_bl2_executed'])
        self.assertEqual(result['fvmap_handoff']['value'],'0x2d000')
        self.assertFalse(result['fvmap_handoff']['performed'])
        self.assertTrue(all('Selected virtual' in r['condition'] for r in result['writes'][:2]))
        selectors=result['voltage_selectors']
        self.assertEqual(len(selectors),12)
        self.assertEqual([s['register'] for s in selectors],
                         [0x4a,0x4c,0x4e,0x50,0x52,0x48,0x48,0x4a,0x4c,0x4e,0x50,0x56])
        self.assertTrue(all(s['virtual_reset_byte'] is None for s in selectors))
        self.assertTrue(all(s['minimum_uv']==450000 and s['step_uv']==6250 for s in selectors))

    def test_mif_plls_use_finite_banks_and_independent_timer_state(self):
        source=(platform.vm.ROOT/'src/emu/exynos-auto9-cmu.inc.c').read_text(encoding='utf-8')
        self.assertIn('#define AUTO9_PLL_COUNT (13 + G_N_ELEMENTS(exynos_auto9_aux_plls))',source)
        self.assertIn('0x1b400000, 0x1b600000, 0x1c400000, 0x1c600000',source)
        self.assertIn('first = 5 + 2 * bank; count = 2;',source)
        self.assertIn('s->words[p->port][p->slot]',source)
        self.assertIn('s->words[p->port][word]',source)
        self.assertIn('s->plls[p].port = i;',source)
        self.assertIn('i < 13 ? (i - 5) % 2 : exynos_auto9_aux_plls[i - 13].slot',source)
        self.assertIn('timer_mod(p->deadline',source)
        self.assertIn('clock_update(p->raw',source)

    def test_queue_operand_decoder_preserves_unresolved_semantics(self):
        rows=platform.cmu_queue_operands((0,0x10000,0x1207109c,0x80040000,0x80040123),15)
        self.assertEqual(rows[0]['kind'],'zero-word')
        self.assertTrue(rows[0]['written'])
        self.assertEqual(rows[1]['kind'],'unresolved-word')
        self.assertEqual((rows[2]['offset'],rows[2]['data_byte']),('0x109c',7))
        self.assertFalse(rows[2]['execution_semantics_verified'])
        self.assertEqual(rows[3]['number'],4)
        self.assertEqual(rows[4]['kind'],'unresolved-word')
        self.assertFalse(rows[4]['written'])

    def test_queue_inventory_has_real_original_bl2_start_contrast(self):
        import pmic_bootstrap
        raw=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes()
        refs=pmic_bootstrap.mailbox_accesses(raw,0x944,start=0x854,horizon=256,
            address_begin=0x1b243c00,address_end=0x1b243f00)
        indexed={row['offset']:row for row in refs}
        self.assertEqual(indexed['0x8b0']['address'],'0x1b243e00')
        self.assertEqual(indexed['0x8b0']['value'],17)
        self.assertEqual(indexed['0x934']['value'],0x241)
        self.assertEqual(pmic_bootstrap.mailbox_accesses(raw,0x944,start=0x854,horizon=256),[])

    def test_original_bl2_builder_and_strict_dependency_stops(self):
        import bl2_fvmap
        raw=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes()
        candidate,result=bl2_fvmap.execute(raw,100000,True,True,True)
        self.assertTrue(result['fragment_returned'])
        self.assertIsNone(result['invalid_access'])
        self.assertEqual(result['parameter_parser']['result'],'0x0')
        self.assertTrue(result['validation']['abi_valid'])
        self.assertEqual(len(result['validation']['domains']),11)
        self.assertEqual(result['validation']['domains'][0]['opps'][2],
                         {'rate_khz':2132000,'voltage_uv':887500})
        self.assertFalse(result['full_bl2_executed'])
        self.assertFalse(result['physical_calibration_inputs_verified'])
        self.assertFalse(result['native_boot_approved'])
        for chipid,address in ((False,'0x10000004'),(True,'0x10009000')):
            _,strict=bl2_fvmap.execute(raw,100000,True,chipid,False)
            self.assertFalse(strict['fragment_returned'])
            self.assertEqual(strict['invalid_access']['address'],address)
        for offset,value in ((0,255),(1,0),(18,0),(21,255)):
            damaged=bytearray(candidate);damaged[offset]=value
            with self.assertRaises(ValueError):bl2_fvmap.validate(bytes(damaged))
        with self.assertRaises(ValueError):bl2_fvmap.validate(candidate[:-1])
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            bl2_fvmap.execute(bytes(len(raw)),100000,True,True,True)

    def test_bl2_parser_budget_cannot_become_unlimited_count_zero(self):
        import bl2_fvmap
        raw=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes()
        _,result=bl2_fvmap.execute(raw,1,True,True,True)
        self.assertEqual(result['instructions'],1)
        self.assertFalse(result['fragment_returned'])
        self.assertIn('budget exhausted',result['error'])

    def test_bl2_original_newtime_catalog_and_fvmap_preservation(self):
        import bl2_fvmap
        raw=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes()
        candidate,result=bl2_fvmap.execute(raw,100000,True,True,True,True,0x40400631)
        timing=result['ddr_timing']
        self.assertIsNone(result['error'])
        self.assertEqual(result['fvmap_instructions'],32441)
        self.assertEqual((timing['columns'],timing['rows']),(12,19))
        self.assertEqual(timing['keys'][0],'0x40400631')
        self.assertEqual(timing['keys'][-1],'0x840400132')
        self.assertEqual(len(timing['keys']),19)
        self.assertEqual(timing['bytes'],2476)
        image=bytes.fromhex(timing['image_hex'])
        self.assertEqual(hashlib.sha256(image).hexdigest(),timing['sha256'])
        self.assertEqual(struct.unpack_from('<Q',image)[0],0x40400631)
        self.assertEqual(struct.unpack_from('<I',image,16)[0],2476)
        self.assertEqual(timing['sha256'],'5c4bb33ed791279da16eb4601a5414bf16e3f6b268c49aa70151f3d2cf37c19d')
        self.assertEqual(hashlib.sha256(candidate).hexdigest(),timing['pre_ddr_fvmap_sha256'])
        self.assertFalse(timing['hardware_training_performed'])
        self.assertFalse(timing['native_boot_approved'])
        self.assertFalse(timing['fixed_header_offsets_populated'])
        self.assertTrue(timing['table_catalog_only'])
        self.assertEqual(len(timing['sections']),11)
        ranges=[]
        for row in timing['sections']:
            data=image[row['offset']:row['offset']+row['bytes']]
            self.assertEqual(data,raw[row['original_file_offset']:row['original_file_offset']+row['bytes']])
            self.assertEqual(hashlib.sha256(data).hexdigest(),row['sha256'])
            ranges.append((row['offset'],row['offset']+row['bytes']))
        self.assertEqual(ranges[0][0],576)
        self.assertEqual(ranges[-1][1],len(image))
        self.assertTrue(all(left[1]==right[0] for left,right in zip(ranges,ranges[1:])))

    def test_bl2_newtime_64bit_key_not_truncated(self):
        import bl2_fvmap
        raw=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes()
        _,result=bl2_fvmap.execute(raw,100000,True,True,True,False,0x840400132)
        self.assertIsNone(result['error'])
        timing=result['ddr_timing']
        self.assertEqual(timing['selected_key'],'0x840400132')
        self.assertEqual(struct.unpack_from('<Q',bytes.fromhex(timing['image_hex']))[0],0x840400132)
        self.assertEqual(timing['sha256'],'29daeab30cfcc06e8063fe00c559a1f5ec85dc9f8b1ff2cfc044bed991359039')

    def test_bl2_newtime_unknown_key_and_budget_leave_no_image(self):
        import bl2_fvmap
        raw=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes()
        for limit,key,expected in ((100000,0xdeadbeef,'absent from original'),
                                   (32854,0x40400631,'budget exhausted')):
            _,result=bl2_fvmap.execute(raw,limit,True,True,True,True,key)
            self.assertIn(expected,result['ddr_timing']['error'])
            self.assertNotIn('image_hex',result['ddr_timing'])
            self.assertEqual(result['stop_reason'],'ddr_data_function_failed')
            self.assertLessEqual(result['instructions'],limit)

    def test_bl2_newtime_requires_parser_and_uint64_key(self):
        import bl2_fvmap
        raw=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes()
        with self.assertRaisesRegex(ValueError,'requires original parameter parser'):
            bl2_fvmap.execute(raw,100000,inspect_ddr_keys=True)
        for key in (-1,1<<64):
            with self.assertRaisesRegex(ValueError,'outside uint64'):
                bl2_fvmap.execute(raw,100000,True,True,True,True,key)

    def test_bl2_fvmap_profile_requires_explicit_native_boot_context(self):
        for clock,pmic,steps in ((0,'virtual-bl2-rev0',0),(96000000,None,0),
                                (96000000,'virtual-bl2-rev0',1)):
            with self.assertRaisesRegex(ValueError,'BL2 FVMap profile requires'):
                platform.probe(platform.vm.ROOT/'var/inputs/support/unused-fvmap-policy',.1,steps,
                    acpm_clock_hz=clock,pmic_bootstrap_profile=pmic,bl2_fvmap_profile='virtual-asv0')

    def test_boot_memory_write_is_bounded_chunked_and_read_back(self):
        rsp=platform.RSP.__new__(platform.RSP)
        raw=b'\xa5'*1025
        rsp.command=Mock(side_effect=[b'OK',raw[:1024].hex().encode(),b'OK',b'a5'])
        rsp.write_boot_memory(0x2066000,raw)
        self.assertEqual(rsp.command.call_count,4)
        self.assertTrue(rsp.command.call_args_list[0].args[0].startswith('M2066000,400:'))
        self.assertEqual(rsp.command.call_args_list[2].args[0],'M2066400,1:a5')
        rsp.command=Mock(return_value=b'E14')
        with self.assertRaises(ValueError):rsp.write_boot_memory(0x2066000,b'\0')
        for raw in (b'',bytes(0x38001)):
            with self.assertRaises(ValueError):rsp.write_boot_memory(0x2066000,raw)

    def test_pmic_bootstrap_rejects_modified_original(self):
        import pmic_bootstrap
        raw=bytearray(platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes())
        raw[0x5040]^=1
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            pmic_bootstrap.contract_from_bytes(bytes(raw))

    def test_pmic_profile_is_never_implicit(self):
        with self.assertRaisesRegex(ValueError,'Virtual BL2 profile requires'):
            platform.probe(platform.vm.ROOT/'var/inputs/support/unused-policy-validation',.1,0,
                acpm_clock_hz=96000000,pmic_bootstrap_profile='virtual-bl2-rev0')

    def test_virtual_endpoint_requires_explicit_native_configuration(self):
        for clock,value in ((0,0),(96000000,-1),(96000000,256)):
            with self.assertRaisesRegex(ValueError,'Explicit virtual PMIC-interface byte'):
                platform.probe(platform.vm.ROOT/'var/inputs/support/unused-policy-validation',.1,0,
                    acpm_clock_hz=clock,pmic_interface_reset_byte=value)

    def test_pmic_pmu_input_is_explicit_bounded_and_native_only(self):
        for clock,profile,value in ((0,'virtual-bl2-rev0',0),(96000000,None,0),
                                    (96000000,'virtual-bl2-rev0',-1),(96000000,'virtual-bl2-rev0',256)):
            with self.assertRaisesRegex(ValueError,'PMIC PMU input requires'):
                platform.probe(platform.vm.ROOT/'var/inputs/support/unused-pmu-input-validation',.1,0,
                    acpm_clock_hz=clock,pmic_bootstrap_profile=profile,pmic_pmu_input_byte=value)

    def test_pmu_profile_remains_explicit_and_not_silicon_reset(self):
        import pmu_platform
        self.assertEqual(pmu_platform.machine_options(None),'')
        for name,clock in (('virtual-cold-v1',0),('invalid',96000000)):
            with self.assertRaisesRegex(ValueError,'PMU requires explicit'):
                pmu_platform.machine_options(name,clock)
        self.assertIn('x-exynos-pmu-profile=1',pmu_platform.machine_options('virtual-cold-v1',96000000))
        policy=pmu_platform.profile_policy()
        for key in ('physical_reset_snapshot_verified','silicon_semantics_verified','factory_images_modified','ready_value_injected'):
            self.assertFalse(policy[key])

    def test_instruction_clock_is_bounded_and_requires_native_cpu(self):
        for clock,shift,steps in ((0,0,0),(96000000,-1,0),(96000000,11,0),(96000000,0,1)):
            with self.assertRaisesRegex(ValueError,'Instruction clock requires'):
                platform.probe(platform.vm.ROOT/'var/inputs/support/unused-icount-validation',.1,steps,
                    acpm_clock_hz=clock,icount_shift=shift)

    def test_pmu_link_is_separate_explicit_transport_not_hcu_execution(self):
        import pmu_platform
        self.assertIsNone(pmu_platform.link_policy(None,None,0))
        for profile,pmu,clock in (('virtual-pmlink-v1',None,96000000),
                                  ('invalid','virtual-cold-v1',96000000),
                                  ('virtual-pmlink-v1','virtual-cold-v1',0)):
            with self.assertRaisesRegex(ValueError,'PMU link requires'):
                pmu_platform.link_policy(profile,pmu,clock)
        policy=pmu_platform.link_policy('virtual-pmlink-v1','virtual-cold-v1',96000000)
        self.assertFalse(policy['physical_command_effects_verified'])
        self.assertFalse(policy['hcu_instruction_set_recovered'])
        self.assertFalse(policy['ready_value_injected'])
        self.assertEqual(policy['latency_ns'],2462)
        self.assertIn(0x5e,policy['payload_allowlist'])

    def test_tmu_profile_requires_declared_calibration_and_temperature_bounds(self):
        import tmu_platform
        self.assertEqual(tmu_platform.machine_options(None),'')
        for profile,temp,clock in ((None,70,96000000),('virtual-onepoint-v1',25,0),
                                    ('wrong',25,96000000),('virtual-onepoint-v1',151,96000000),
                                    ('virtual-onepoint-v1',-41,96000000)):
            with self.assertRaises(ValueError):tmu_platform.machine_options(profile,temp,clock)
        self.assertIn('x-exynos-tmu-profile=1',tmu_platform.machine_options('virtual-onepoint-v1',25,96000000))
        policy=tmu_platform.profile_policy()
        for key in ('physical_trim_verified','physical_temperature_verified','factory_images_modified','ready_value_injected'):
            self.assertFalse(policy[key])

    def test_apm_watchdog_requires_explicit_clock_and_keeps_route_boundary(self):
        import apm_watchdog
        self.assertIsNone(apm_watchdog.policy(None))
        for name,clock,cpu in ((None,96000000,96000000),('wrong',96000000,96000000),
                                ('virtual-samsung32-v1',0,96000000),('virtual-samsung32-v1',96000000,0),
                                ('virtual-samsung32-v1',1000000001,96000000)):
            with self.assertRaisesRegex(ValueError,'APM watchdog requires'):
                apm_watchdog.policy(name,clock,cpu)
        policy=apm_watchdog.policy('virtual-samsung32-v1',96000000,96000000)
        for key in ('physical_irq_route_verified','physical_reset_route_verified','ready_value_injected','factory_images_modified'):
            self.assertFalse(policy[key])
        contract=apm_watchdog.original_contract()
        self.assertEqual(contract['original_programmed_count'],100000)
        self.assertEqual(contract['original_control_after_enable'],'0xE25')

    def test_signal_addresses_are_original_156c_1570_not_1554(self):
        import pmu_platform
        self.assertIsNone(pmu_platform.signal_policy(None,None,0))
        for name,base,clock in (('wrong','virtual-cold-v1',96000000),
                                 ('virtual-latched-signals-v1',None,96000000),
                                 ('virtual-latched-signals-v1','virtual-cold-v1',0)):
            with self.assertRaisesRegex(ValueError,'PMU signals require'):
                pmu_platform.signal_policy(name,base,clock)
        p=pmu_platform.signal_policy('virtual-latched-signals-v1','virtual-cold-v1',96000000)
        self.assertFalse(p['physical_cpu_or_rail_routes_implemented'])
        self.assertFalse(p['physical_signal_meanings_verified'])
        c=pmu_platform.original_signal_contract()
        self.assertEqual(c['ack_addresses'],['A046156C','A0461570'])
        self.assertTrue(c['ack_addresses_original_instruction_verified'])
        self.assertEqual(len(c['finite_core_input_literals']),8)
        for status,mask in ((2,0),(0,-1),(0,256)):
            with self.assertRaises(ValueError):
                pmu_platform.signal_policy('virtual-latched-signals-v1','virtual-cold-v1',96000000,status,mask)
        for status,mask in ((1,0),(0,255)):
            with self.assertRaisesRegex(ValueError,'PMU signals require'):
                pmu_platform.signal_policy(None,'virtual-cold-v1',96000000,status,mask)

    def test_tmu_original_descriptors_and_conversion_are_byte_bound(self):
        import tmu_platform
        evidence=tmu_platform.conversion_evidence()
        self.assertTrue(evidence['passed'])
        self.assertEqual(len(evidence['contract']['sensors']),20)
        self.assertEqual({row['ap_base'] for row in evidence['contract']['sensors']},{'0x10090000','0x10094000'})
        sensors={row['sensor']:row for row in evidence['contract']['sensors']}
        self.assertEqual(sensors[8]['trim_offset'],0x400)
        self.assertEqual(sensors[11]['trim_offset'],0x40c)
        self.assertEqual(sensors[24]['trim_offset'],0x400)
        self.assertEqual([row['original_return_c'] for row in evidence['conversions']],[-40,14,20,25,70,125,150])

    def test_speedy_without_slave_never_invents_completion_or_data(self):
        source=(platform.vm.ROOT/'src/emu/exynos-auto9-speedy.inc.c').read_text(encoding='utf-8')
        self.assertIn('s->status |= 2;',source)
        self.assertIn('if (s->bl2_rev0 != 1)',source)
        self.assertIn('write && !writable',source)
        self.assertIn('s->interface_byte = 256',source)
        self.assertIn('s->interface_byte = s->tx_data',source)
        self.assertIn('s->status &= ~value',source)
        self.assertIn('timer_mod(s->deadline',source)
        self.assertIn('RX read without responding slave',source)
        self.assertIn('if (!vms->exynos_acpm)',source)

    def test_patch_preserves_stock_mode_and_uses_real_uart(self):
        root=platform.vm.ROOT/'var/inputs/support/vlm-exynos-bringup/qemu-baseline/qemu-11.1.0'
        text=(root/'hw/arm/virt.c').read_text(encoding='utf-8')
        result=patch.edits('hw/arm/virt.c',text)
        self.assertIn('return vms->exynos_vlm ? 512 : NUM_IRQS;',result)
        self.assertIn('exynos4210_uart_create(0x10300000, 256, 0, serial_hd(1)',result)
        self.assertIn('vms->memmap[VIRT_GIC_DIST].base = 0x10101000;',result)
        self.assertIn('x-exynos-vlm requires gic-version=2',result)
        self.assertIn('((uint64_t)(idx / 4) << 16)',result)
        self.assertIn('exynos_auto9_dintc_create(vms, 2, 0x100b0000, 455, 2)',result)
        self.assertIn('s->pending[bank] & s->enable[bank]',result)
        self.assertIn('qemu_set_irq(s->irq, active)',result)
        self.assertIn('s->banks < 1 || s->banks > 2',result)

    def test_unknown_patch_anchor_rejected(self):
        with self.assertRaises(ValueError):patch.edits('hw/arm/virt.c','unrelated source')


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests))
    sys.exit(0 if result.wasSuccessful() else 1)
