
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from unittest.mock import Mock, patch

import original_8704_vm as vm
from linux_pid1_context import without_oem_autoreboot
import qnx_display_identity as qnx_identity
import linux_weston_platform as weston_platform


class OemLinuxBootPolicyTests(unittest.TestCase):
    def test_uses_only_the_shipped_auto_reboot_word(self):
        stock = 'root=/dev/dm-0  auto_reboot=1 init=/sbin/init dm=verity'
        self.assertEqual(without_oem_autoreboot(stock),
                         'root=/dev/dm-0  auto_reboot=0 init=/sbin/init dm=verity')
        for invalid in (stock + ' auto_reboot=0', stock.replace('auto_reboot=1', 'auto_reboot'),
                        stock.replace('auto_reboot=1', 'auto_reboot=0'),
                        stock.replace('root=/dev/dm-0', 'root=/dev/sda1'),
                        stock.replace('init=/sbin/init', 'init=/init')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                without_oem_autoreboot(invalid)

    def test_pixman_uses_only_the_original_early_config_and_verity_root(self):
        root=Path(weston_platform.__file__).resolve().parents[2]
        base=root/'var/inputs/support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.raw'
        selected=weston_platform.plan(base)
        config,*hashes=selected.changes
        self.assertEqual((config.slot,config.block),(14,9910))
        self.assertEqual([(c.slot,c.block) for c in hashes],[(27,127),(27,2),(27,1)])
        self.assertEqual(len(config.before),len(config.after))
        self.assertIn(b'argv=--idle-time=0\nargv=--use-pixman\n',config.after)
        self.assertEqual(weston_platform.digest(hashes[-1].after).hex(),selected.root_sha256)
        arg=f'root=/dev/dm-0 dm-mod.create="dm-verity,,,ro, 0 6291456 {weston_platform.ORIGINAL_TABLE}" init=/sbin/init'
        changed=weston_platform.bootargs(arg,selected)
        self.assertEqual(changed.replace(selected.root_sha256,weston_platform.ROOT_SHA),arg)
        self.assertIn('panic_on_corruption',changed)
        with self.assertRaises(ValueError):weston_platform.bootargs(changed,selected)
        with self.assertRaises(ValueError):weston_platform.configure(b'not original')

    def test_wayland_trace_changes_only_actual_early_weston_environment(self):
        root=Path(weston_platform.__file__).resolve().parents[2]
        base=root/'var/inputs/support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.raw'
        ordinary=weston_platform.plan(base)
        traced=weston_platform.plan(base,trace_protocol=True)
        self.assertNotEqual(ordinary.root_sha256,traced.root_sha256)
        self.assertEqual(weston_platform.inherited_sectors(traced,ordinary),tuple(c.after for c in ordinary.changes))
        first, rest = traced.changes[0].after.split(b'\n',1)
        self.assertTrue(first.startswith(b'#'))
        previous = ordinary.changes[0].after.split(b'\n',1)[1]
                                                                          
                                                                               
        self.assertEqual(rest.replace(weston_platform.TRACE_ENV,b'',1),previous)
        weston=rest.split(b'[weston]\n',1)[1].split(b'<end>\n',1)[0]
        self.assertIn(weston_platform.TRACE_ENV,weston)
        self.assertEqual(rest.count(weston_platform.TRACE_ENV),1)
        self.assertIn(b'log=/run/weston.txt\n',weston)
        self.assertEqual(traced.evidence()['wayland_debug'],'server')
        self.assertNotIn('wayland_debug',ordinary.evidence())


class QnxDisplayIdentityTests(unittest.TestCase):
    def test_write_plan_and_native_readback(self):
        self.assertEqual(qnx_identity.loader_options(), [
            '-device', 'loader,addr=0xe5d01001,data=0x7200,data-len=2',
            '-device', 'loader,addr=0xe5d01008,data=0x10101,data-len=3'])
        qmp = Mock()
        qmp.execute.side_effect = ['00000000e5d01001: 0x00 0x72\r\n',
                                   '00000000e5d01008: 0x01 0x01 0x01\r\n']
        self.assertTrue(qnx_identity.readback(qmp)['all_match'])
        qmp.execute.side_effect = ['00000000e5d01001: 0x00 0x00\r\n',
                                   '00000000e5d01008: 0x01 0x01 0x00\r\n']
        self.assertFalse(qnx_identity.readback(qmp)['all_match'])
        qmp.execute.side_effect = ['00000000e5d02001: 0x00 0x72\r\n']
        with self.assertRaisesRegex(ValueError, 'Cannot read back'):
            qnx_identity.readback(qmp)


def fixture():
    boot = bytearray(12288)
    boot[:8] = b'ANDROID!'
    struct.pack_into('<4I', boot, 8, 64, 12, 0, 1580)
    struct.pack_into('<I', boot, 40, 3)
    boot[4096:4160] = b'K'*64
    boot[8192:8204] = b'GENERIC-CPIO'
    vendor = bytearray(8192)
    vendor[:8] = b'VNDRBOOT'
    struct.pack_into('<2I', vendor, 8, 3, 4096)
    struct.pack_into('<I', vendor, 24, 11)
    struct.pack_into('<I', vendor, 2096, 2112)
    vendor[4096:4107] = b'VENDOR-CPIO'
    return bytes(boot), bytes(vendor)


class LayoutTests(unittest.TestCase):
    def test_v3_payloads(self):
        kernel, generic, vendor = vm.android_parts(*fixture())
        self.assertEqual(kernel, b'K'*64)
        self.assertEqual(generic, b'GENERIC-CPIO')
        self.assertEqual(vendor, b'VENDOR-CPIO')

    def test_short_boot_rejected(self):
        with self.assertRaises(ValueError):
            vm.android_parts(b'', fixture()[1])

    def test_other_header_rejected(self):
        boot, vendor = fixture()
        boot = bytearray(boot)
        struct.pack_into('<I', boot, 40, 4)
        with self.assertRaises(ValueError):
            vm.android_parts(boot, vendor)

    def test_truncated_ramdisk_rejected(self):
        boot, vendor = fixture()
        with self.assertRaises(ValueError):
            vm.android_parts(boot[:8195], vendor)

    def test_vendor_magic_rejected(self):
        with self.assertRaises(ValueError):
            vm.android_parts(fixture()[0], b'x'*8192)

    def test_vendor_page_rejected(self):
        boot, vendor = fixture()
        vendor = bytearray(vendor)
        struct.pack_into('<I', vendor, 12, 0)
        with self.assertRaises(ValueError):
            vm.android_parts(boot, vendor)

    def test_vendor_2048_page_and_2112_header(self):
        boot, vendor = fixture()
        vendor = bytearray(vendor)
        struct.pack_into('<I', vendor, 12, 2048)
        self.assertEqual(vm.android_parts(boot, vendor)[2], b'VENDOR-CPIO')

    def test_unknown_guest_rejected(self):
        with self.assertRaises(ValueError):
            vm.Guest({}, 'host')

    def test_initial_status_not_factory_boot(self):
        guest = vm.Guest({}, 'linux')
        self.assertFalse(guest.status()['running'])
        self.assertFalse(guest.status()['factory_hmi_verified'])

    def test_monitor_commands_restricted(self):
        guest = vm.Guest({}, 'android')
        with self.assertRaises(ValueError):
            guest.monitor('change vnc 0.0.0.0:0')

    def test_invalid_serial_input(self):
        guest = vm.Guest({}, 'linux')
        for command in ('', 'x\x00y', 12, 'x'*4097):
            with self.assertRaises(ValueError):
                guest.serial(command)

    def test_atomic_json_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            vm.save(path, {'verified':False})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'verified':False})
            self.assertEqual(len(vm.sha(path)),64)
            self.assertFalse(path.with_name('state.json.tmp').exists())

    def test_printk_records_not_plain_strings(self):
        self.assertEqual(vm.printk_records(b'Booting Linux on physical CPU 0x0 [0x410fd083]'), [])

    def test_printk_consecutive_chain(self):
        rows = (b'Booting Linux on physical CPU 0x0 [0x410fd083]', b'Machine model: linux,dummy-virt', b'Kernel panic - not syncing: test')
        data=b''
        for number, row in enumerate(rows):
            length=vm.align(16+len(row),4)
            data+=struct.pack('<QHHHBB',number,length,len(row),0,0,0)+row+b'\0'*(length-16-len(row))
        self.assertEqual([x['text'] for x in vm.printk_records(data)], [x.decode() for x in rows])

    def test_vendor_extended_printk_header(self):
        rows = (b'Booting Linux on physical CPU 0x0 [0x410fd083]', b'Call trace:', b' logging_core_sector+0x6c/0x2f0')
        data = b''
        for row in rows:
            length = vm.align(40+len(row), 4)
            data += struct.pack('<QHHHBB', 0, length, len(row), 0, 0, 194)+b'\x00'*24+row+b'\x00'*(length-40-len(row))
        found = vm.printk_records(data)
        self.assertEqual(len(found), 3)
        self.assertEqual(found[0]['header_bytes_inferred'], 40)

    def test_native_mke2fs_progress_does_not_hide_later_mount_records(self):
        def record(index,text):
            size=(40+len(text)+3)&~3
            return struct.pack('<QHHHBB',index,size,len(text),0,1,6)+bytes(24)+text+bytes(size-40-len(text))
        rows=[b'Booting Linux on physical CPU 0x0',b'init first stage',b'mke2fs start',
              b'mke2fs: Writing superblocks: 0/1\b\b\b   \b\b\bdone',
              b'EXT4: target=/metadata mounted']
        buf=b''.join(record(i+1,t) for i,t in enumerate(rows))
        self.assertEqual([r['text'] for r in vm.printk_records(buf)],[x.decode() for x in rows])
        bad=buf.replace(b'mke2fs: Writing',b'foreign Writing',1)
        self.assertEqual(len(vm.printk_records(bad)),3)

    def test_vendor_empty_printk_record_keeps_following_chain(self):
        messages = (b'Booting Linux on physical CPU 0x0 [0x410fd083]', b'', b'Kernel initcall returned')
        data = bytearray()
        for index, message in enumerate(messages, 1):
            length = vm.align(40+len(message), 4) if message else 48
            data += struct.pack('<QHHHBB', index, length, len(message), 0, 0, 194)
            data += bytes(24)+message+bytes(length-40-len(message))
        found = vm.printk_records(data)
        self.assertEqual([row['text'] for row in found], [m.decode() for m in messages])

    def test_truncated_record_chain_rejected(self):
        self.assertEqual(vm.printk_records(b'\0'*40+b'Booting Linux on physical CPU 0x0'), [])

    def test_core_probe_rejects_other_bytes(self):
        for guest in ('linux', 'android', 'qnx'):
            with self.assertRaises(ValueError):
                vm.core_id_probe_copy(b'not-the-original-kernel', guest)

    def test_real_original_probe_copies_preserve_all_other_bytes(self):
        cfg = vm.load_config()
        for name, path in (('linux', cfg['original']/'ivi/Image'), ('android', cfg['assets']/'android-kernel')):
            if not path.exists():
                self.skipTest('Retained original kernel inputs are not present')
            original = path.read_bytes()
            for skip_logger in (False, True):
                copy, metadata = vm.core_id_probe_copy(original, name, skip_logger)
                offset = metadata['offset']
                self.assertEqual(copy[:offset], original[:offset])
                self.assertEqual(copy[offset+4:], original[offset+4:])
                self.assertNotEqual(copy[offset:offset+4], original[offset:offset+4])
                self.assertEqual(metadata['cpus'], 1)

    def test_real_boot_record_recovery(self):
        root = vm.ROOT/'var/sessions'
        for session, expected in (('20261107T154254.814734Z-linux', 86), ('20261107T154530.369651Z-android', 77)):
            path = root/session/'boot-ram-64m.bin'
            if not path.exists():
                self.skipTest('Retained boot RAM inputs are not present')
            records = vm.printk_records(path.read_bytes())
            self.assertEqual(len(records), expected)
            self.assertEqual(records[0]['header_bytes_inferred'], 40)
            self.assertTrue(any('logging_core_sector' in row['text'] for row in records))
            self.assertTrue(any('Kernel panic - not syncing:' in row['text'] for row in records))

    def test_three_display_roles_are_separate(self):
        displays = vm.Displays({name: vm.Guest({}, name) for name in ('linux', 'android')})
        self.assertEqual(set(displays.status()), {'center', 'ic', 'hud'})
        for name in ('ic', 'hud'):
            self.assertFalse(displays.status()[name]['available'])
            self.assertIsNone(displays.status()[name]['source'])
            with self.assertRaises(ValueError):
                displays.frame(name)
        with self.assertRaises(ValueError):
            displays.frame('android')

    def test_native_console_only_routes_complete_tagged_lines(self):
        raw = (b'1 HYP:vm report\r\n0 2 0:qnx\r\n\x1b[0;32m1 3 0:linux\r\n'
               b'\x1b[0;34m3 4 1:android\r\n2 4 0:\x00\x1b bytes\n'
               b'prefix fragment\n3 4 0:unfinished')
        self.assertEqual(vm.native_console_lines(raw),
                         {'linux': 'linux', 'android': 'android\n\ufffd\ufffd bytes'})

    def test_native_ring_uses_live_indices_and_accepts_wrapped_records(self):
        raw=bytearray(256)
        for offset,text in [(120,b'boot\x00old'),(168,b'active'),(0,b'after wrap')]:
            size=(40+len(text)+3)&~3
            struct.pack_into('<QHHHBB',raw,offset,1,size,len(text),0,0,0)
            raw[offset+40:offset+40+len(text)]=text
        options=dict(first=120,next_index=52,first_seq=103,next_seq=106,physical=0x89000000)
        rows=vm.printk_live_ring(raw,**options)
        self.assertEqual([r['text'] for r in rows],['boot\x00old','active','after wrap'])
        self.assertEqual([r['sequence'] for r in rows],[103,104,105])
        with self.assertRaisesRegex(ValueError,'log_next_idx'):
            vm.printk_live_ring(raw,**dict(options,next_index=56))

    def test_linux_ring_uses_oem_header_for_every_record(self):
        from linux_pid1_context import LINUX_SHA
        raw=bytearray(256)
        for offset,stamp,text in [(116,5,b'before'),(0,17,b'after')]:
            size=(48+len(text)+3)&~3
            struct.pack_into('<QQHHHBB',raw,offset,0x123456789,stamp,size,len(text),0,0,0)
            raw[offset+48:offset+48+len(text)]=text
        options=dict(first=116,next_index=56,first_seq=103,next_seq=105,physical=0x89000000)
        rows=vm.printk_live_ring(raw,**options,**vm.original_printk_layout(LINUX_SHA))
        self.assertEqual([(r['text'],r['timestamp_ns'],r['physical_address']) for r in rows],
                         [('before',5,'0x89000074'),('after',17,'0x89000000')])
        self.assertEqual(rows[0]['header_bytes_inferred'],48)
        with self.assertRaises(ValueError):
            vm.printk_live_ring(raw,**options)

    def test_native_command_waits_for_new_writable_focus_before_sending(self):
        bridge=vm.NativeInteractiveConsole.__new__(vm.NativeInteractiveConsole)
        bridge.keys={'linux':(3,b'\x1bOR'),'android':(4,b'\x1bOS')}
        bridge.lock=threading.Lock()
        bridge.closed=threading.Event()
        bridge.byte_delay=.003
        bridge.socket=Mock()
        bridge.focus=Mock(side_effect=[{'host_monotonic':9,'vmid':3,'read_only':False},
                                       {'host_monotonic':9,'vmid':4,'read_only':False},
                                       {'host_monotonic':11,'vmid':4,'read_only':True},
                                       {'host_monotonic':11.5,'vmid':4,'read_only':False},
                                       {'host_monotonic':11.5,'vmid':4,'read_only':False}])
        with patch.object(vm.time,'monotonic',side_effect=[10,10.1,10.7,11.5]), patch.object(vm.time,'sleep'):
            self.assertEqual(bridge.serial('android','id'),dict(
                sent=True,focus_verified=True,vmid=4,execution_verified=False))
        self.assertEqual([call.args[0] for call in bridge.socket.sendall.call_args_list],[b'\x1bOS',b'i',b'd',b'\r'])

    def test_native_focus_loss_mid_line_never_sends_carriage_return(self):
        bridge=vm.NativeInteractiveConsole.__new__(vm.NativeInteractiveConsole)
        bridge.keys={'linux':(3,b'\x1bOR')}
        bridge.lock=threading.Lock()
        bridge.closed=threading.Event()
        bridge.byte_delay=.003
        bridge.socket=Mock()
        bridge.focus=Mock(side_effect=[{'host_monotonic':9,'vmid':4,'read_only':False},
                                       {'host_monotonic':10.2,'vmid':3,'read_only':False},
                                       {'host_monotonic':10.3,'vmid':4,'read_only':False}])
        with patch.object(vm.time,'monotonic',side_effect=[10,10.1]),patch.object(vm.time,'sleep') as sleep:
            with self.assertRaisesRegex(ValueError,'transmission was interrupted'):
                bridge.serial('linux','id')
        self.assertEqual([call.args[0] for call in bridge.socket.sendall.call_args_list],[b'\x1bOR',b'i',b'd'])
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[.003,.003])

    def test_native_wrong_focus_never_sends_command_payload(self):
        bridge=vm.NativeInteractiveConsole.__new__(vm.NativeInteractiveConsole)
        bridge.keys={'linux':(3,b'\x1bOR')}
        bridge.lock=threading.Lock()
        bridge.closed=threading.Event()
        bridge.socket=Mock()
        bridge.focus=Mock(return_value={'host_monotonic':11,'vmid':4,'read_only':False})
        with patch.object(vm.time,'monotonic',side_effect=[10,11,14]), patch.object(vm.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'command bytes not sent'):
                bridge.serial('linux','id')
        bridge.socket.sendall.assert_called_once_with(b'\x1bOR')

    def test_native_return_sends_only_cr_after_new_focus(self):
        bridge=vm.NativeInteractiveConsole.__new__(vm.NativeInteractiveConsole)
        bridge.keys={'linux':(3,b'\x1bOR')}
        bridge.lock=threading.Lock()
        bridge.closed=threading.Event()
        bridge.socket=Mock()
        bridge.focus=Mock(side_effect=[{'host_monotonic':9,'vmid':4,'read_only':False},
                                       {'host_monotonic':10.2,'vmid':3,'read_only':False}])
        with patch.object(vm.time,'monotonic',side_effect=[10,10.1]):
            self.assertEqual(bridge.return_key('linux'),dict(
                sent=True,focus_verified=True,vmid=3,execution_verified=False))
        self.assertEqual([call.args[0] for call in bridge.socket.sendall.call_args_list],
                         [b'\x1bOR',b'\r'])

    def test_native_usi03_does_not_switch_or_write_the_mux(self):
        import linux_early_uart as early
        bridge=vm.NativeInteractiveConsole.__new__(vm.NativeInteractiveConsole)
        bridge.direct=dict(source=early.DIRECT_SOURCE)
        bridge.direct_wire=Mock(lock=threading.Lock(),closed=threading.Event(),socket=Mock())
        bridge.socket=Mock()
        bridge.focus=Mock(return_value=dict(vmid=4,read_only=True))
        bridge.byte_delay=.003
        with patch.object(vm.time,'sleep'):
            self.assertEqual(bridge.serial('linux','id'),dict(sent=True,route_verified=True,
                route=early.DIRECT_SOURCE,vmid=3,execution_verified=False))
        bridge.socket.sendall.assert_not_called()
        self.assertEqual([c.args[0] for c in bridge.direct_wire.socket.sendall.call_args_list],
                         [b'i',b'd',b'\r'])
        bridge.focus.return_value=None
        with self.assertRaisesRegex(ValueError,'heartbeat'):
            bridge.return_key('linux')
        self.assertEqual(bridge.direct_wire.socket.sendall.call_count,3)

    def test_native_usi03_proof_rechecks_binding_and_separate_wire(self):
        import linux_early_uart as early
        from linux_local_shell import read_verified_proof
        with tempfile.TemporaryDirectory() as directory:
            session=Path(directory)
            vm.save(session/'linux-native-console.json',dict(source='vlm-console-mux',
                serial_index=1,host='127.0.0.1'))
            profile=dict(ports=[dict(index=3,base=early.DIRECT_BASE,spi=early.DIRECT_SPI,fifo=64)],
                         hcb_sha256='a'*64)
            args,meta=early.direct_serial(session,32229,profile)
            command=['qemu','-machine','virt,x-exynos-linux-usi-uart=1']
            command+=['-serial','null']*3+args
            vm.save(session/early.DIRECT_META,meta)
            vm.save(session/'linux-usi-uart-profile.json',profile)
            vm.save(session/'launch.json',dict(command=command))
            vm.save(session/'linux-initcall-dtb.json',dict(local_linux_shell=True,
                local_linux_shell_tty=early.DIRECT_TTY))
            nonce='c'*24
            raw=f'8704-vm3-check:{nonce}:65534:Linux\r\n'.encode()
            (session/'vlm-console-rx.bin').write_bytes(raw)
            vm.save(session/'linux-local-shell-proof.json',dict(status='original_command_executed',
                command_execution_verified=True,route_verified=True,route=early.DIRECT_SOURCE,vmid=3,
                uid=65534,kernel='Linux',nonce=nonce,raw_start=0,raw_end=len(raw),
                raw_sha256=hashlib.sha256(raw).hexdigest(),response_source=early.DIRECT_SOURCE))
            self.assertIsNone(read_verified_proof(session))
            (session/early.DIRECT_CAPTURE).write_bytes(raw)
            tap=vm.NativeConsoleTap(session)
            status=tap.status()
            self.assertTrue(status['linux']['command_execution_verified'])
            self.assertFalse(status['android']['command_execution_verified'])
            command[command.index('-serial')+1]='chardev:'+early.DIRECT_CHR
            vm.save(session/'launch.json',dict(command=command))
            self.assertIsNone(read_verified_proof(session))
            with self.assertRaisesRegex(ValueError,'serial binding'):
                vm.NativeConsoleTap(session)

    def test_native_attach_has_no_diagnostic_guest_or_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            vm.save(session/'linux-native-console.json', dict(
                source='vlm-console-mux', serial_index=1, host='127.0.0.1'))
            serial=session/'vlm-console-rx.bin'
            serial.write_bytes(b'1 3 0:linux\n3 4 0:android\n1 3 0:login: ')
            (session/'vlm-console-events.jsonl').write_bytes(b'{"event":"eof"}\n')
            server, guests, token = vm.create_server({}, 0, native_session=session)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{server.server_address[1]}'

            def request(path, data=None):
                return urllib.request.urlopen(urllib.request.Request(base+path,
                    headers={'X-8704-Token': token}, data=data), timeout=3)

            try:
                self.assertEqual(guests, {})
                with request('/api/status') as response:
                    states = json.load(response)
                for guest, state in states.items():
                    self.assertEqual(state['console'], guest)
                    self.assertEqual(state['console_pending'], 'login: ' if guest=='linux' else '')
                    self.assertTrue(state['capture_ended'])
                    self.assertFalse(state['serial_writable'])
                    self.assertFalse(state['board_running'])
                serial.write_bytes(b'1 3 0:linux\n3 4 0:android\n1 2 0:qnx\nplain output\r\nlogin: ')
                with request('/api/status') as response:
                    states=json.load(response)
                self.assertEqual(states['linux']['shared_uart_unattributed'],'plain output\nlogin: ')
                self.assertEqual(states['linux']['console'],'linux')
                self.assertEqual(states['android']['console'],'android')
                self.assertFalse(states['linux']['console_pending'])
                vm.save(session/'vlm-console-focus.json', dict(source='original-vlm-focus',
                    running=True, host_monotonic=vm.time.monotonic(), vmid=3, read_only=False))
                with request('/api/status') as response:
                    for state in json.load(response).values():
                        self.assertTrue(state['board_running'])
                        self.assertFalse(state['running'])                                        
                        self.assertFalse(state['serial_writable'])
                with request('/api/displays') as response:
                    outputs = json.load(response)
                self.assertEqual(set(outputs), {'center', 'ic', 'hud'})
                self.assertTrue(all(row['source'] is None and not row['available'] for row in outputs.values()))
                for path, data in (('/api/display-frame?display=center', None),
                                   ('/api/serial', b'{"guest":"linux","command":"id"}')):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        request(path, data)
                    self.assertEqual(caught.exception.code, 409)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)

    def test_native_proof_loader_failure_does_not_break_uart_status(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            session=Path(directory)
            vm.save(session/'linux-native-console.json',dict(source='vlm-console-mux',serial_index=1,host='127.0.0.1'))
            vm.save(session/'linux-local-shell-proof.json',{})
            (session/'vlm-console-rx.bin').write_bytes(b'1 3 0:still-visible\n')
            (session/'vlm-console-events.jsonl').write_text('{"event":"eof"}\n')
            loader=SimpleNamespace(read_verified_proof=Mock(side_effect=ValueError('Source SHA256 mismatch')))
            with patch.dict(sys.modules,{'linux_local_shell':loader}):
                status=vm.NativeConsoleTap(session).status()
            self.assertIn('still-visible',status['linux']['console'])
            self.assertTrue(status['linux']['capture_ended'])
            self.assertFalse(status['linux']['serial_writable'])
            self.assertFalse(status['linux']['command_execution_verified'])
            self.assertFalse(status['android']['command_execution_verified'])
            self.assertEqual(status['linux']['command_proof_error'],'Source SHA256 mismatch')

    def test_linux_command_status_revalidates_original_uart_and_never_claims_android(self):
        with tempfile.TemporaryDirectory() as directory:
            session=Path(directory)
            vm.save(session/'linux-native-console.json',dict(source='vlm-console-mux',serial_index=1,host='127.0.0.1'))
            vm.save(session/'linux-initcall-dtb.json',dict(local_linux_shell=True))
            serial=session/'vlm-console-rx.bin'
            proof=session/'linux-local-shell-proof.json'
            nonce='1234abcd'*3
            marker=f'8704-vm3-check:{nonce}:65534:Linux'.encode()
            initial=b'irrelevant boot output\n'
            tap=vm.NativeConsoleTap(session)

            def save_proof(wire,source='shared-physical-uart'):
                serial.write_bytes(initial+wire)
                vm.save(proof,dict(status='original_command_executed',command_execution_verified=True,
                     vmid=3,focus_verified=True,uid=65534,kernel='Linux',nonce=nonce,
                     raw_start=len(initial),raw_end=len(initial)+len(wire),
                     raw_sha256=hashlib.sha256(wire).hexdigest(),response_source=source))

            save_proof(marker+b'\r\n')
            status=tap.status()
            self.assertTrue(status['linux']['command_execution_verified'])
            self.assertFalse(status['android']['command_execution_verified'])
            self.assertFalse(status['linux']['running'])                                            
            self.assertIn(marker.decode(),status['linux']['shared_uart_unattributed'])
            self.assertNotIn(marker.decode(),status['linux']['console'])
            serial.write_bytes(initial+b'forged')
            self.assertFalse(tap.status()['linux']['command_execution_verified'])
            save_proof(b'1 4 0:'+marker+b'\r\n','original-vm3-tag')
            self.assertFalse(tap.status()['linux']['command_execution_verified'])
            save_proof(b'1 3 0:'+marker+b'\r\n','original-vm3-tag')
            self.assertTrue(tap.status()['linux']['command_execution_verified'])

    def test_android_command_status_revalidates_mux_and_never_claims_linux(self):
        from android_local_shell import read_verified_proof
        with tempfile.TemporaryDirectory() as directory:
            session=Path(directory)
            vm.save(session/'linux-native-console.json',dict(source='vlm-console-mux',serial_index=1,host='127.0.0.1'))
            vm.save(session/'android-local-shell.json',dict(local_android_shell=True))
            serial=session/'vlm-console-rx.bin'
            proof=session/'android-local-shell-proof.json'
            nonce='abcd1234'*3
            marker=f'8704-vm4-check:{nonce}:2000:Linux'.encode()
            initial=b'irrelevant boot output\n'
            tap=vm.NativeConsoleTap(session)

            def save_proof(wire,source='original-vm4-tag'):
                serial.write_bytes(initial+wire)
                vm.save(proof,dict(status='original_command_executed',command_execution_verified=True,
                     vmid=4,focus_verified=True,uid=2000,kernel='Linux',nonce=nonce,
                     raw_start=len(initial),raw_end=len(initial)+len(wire),
                     raw_sha256=hashlib.sha256(wire).hexdigest(),response_source=source))

            save_proof(b'3 4 0:'+marker+b'\r\n')
            status=tap.status()
            self.assertTrue(status['android']['command_execution_verified'])
            self.assertFalse(status['linux']['command_execution_verified'])
            self.assertEqual(read_verified_proof(session)['uid'],2000)
            serial.write_bytes(initial+b'forged')
            self.assertFalse(tap.status()['android']['command_execution_verified'])
            save_proof(b'1 3 0:'+marker+b'\r\n','original-vm4-tag')
            self.assertFalse(tap.status()['android']['command_execution_verified'])
            save_proof(marker+b'\r\n','shared-physical-uart')
            self.assertTrue(tap.status()['android']['command_execution_verified'])

    def test_qnx_live_frame_requires_original_role_and_recent_source_progress(self):
        from qnx_native_display import MEMBER_SHA256, ROLES, to_png, verified_plane
        port,pipeline,pipe,dsi,w,h=ROLES['ic']
        size=(h<<16)|w
        addr=0x90100000
        window={'window':0,'owner':pipe,'commits':3,'active-control':0x21,'active-start':0,
                'active-end':((h-1)<<16)|(w-1),'source-address':addr,'source-control':0,
                'source-frame-size':size,'source-crop-size':size,'source-output-size':size,
                'source-crop-offset':0,'source-writes':2}
        state={'active-dsi':dsi,'active-size':size,'running':1,'frame-ticks':10,
               'scanned-frames':3,'scanned-source':addr,'scanned-nonzero':1}
        allocation=dict(physical=hex(addr),iova='0x0',format=8,bpp=4,width=w,height=h,stride=w*4,usage=0x20)
        plane=verified_plane('ic',[window],state,[allocation])
        self.assertIsNotNone(plane)
        self.assertIsNone(verified_plane('ic',[window,window],state,[allocation]))
        self.assertIsNone(verified_plane('ic',[window],state,[dict(allocation,usage=0x8000)]))
        for invalid in ({'scanned-frames':0},{'scanned-source':addr+4096},{'scanned-nonzero':0}):
            self.assertIsNone(verified_plane('ic',[window],state|invalid,[allocation]))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            native=Mock(session=path,focus=Mock(return_value={'running':True}))
            displays=vm.Displays({},native)
            png=to_png(bytes((5,17,37,255))*w*h,w,h)
            self.assertIsNone(to_png(bytes(4*w*h),w,h))
            digest=vm.hashlib.sha256(png).hexdigest()
            name=f'native-ic-{digest}.png'
            (path/name).write_bytes(png)
            proof=dict(plane,status='active_qnx_source_verified',frame_source='original_vm2_dpum',
                       dpu_sha256=MEMBER_SHA256,nonzero=True,png=name,png_sha256=digest,captured_monotonic=100.0)
            with patch.object(vm.time,'monotonic',return_value=101):
                vm.save(path/'native-ic-frame.json',proof)
                self.assertIsNone(displays.native_qnx('ic'))
                vm.save(path/'native-ic-frame.json',dict(proof,live_proof_monotonic=99.0))
                self.assertEqual(displays.frame('ic'),png)
                self.assertEqual(displays.status()['ic']['frame_version'],digest)
                self.assertEqual(displays.frame('ic',digest),png)
                with self.assertRaisesRegex(ValueError,'原厂帧已切换'):
                    displays.frame('ic','0'*64)
                vm.save(path/'native-ic-frame.json',dict(proof,live_proof_monotonic=99.0,scanned_frames=0))
                self.assertIsNone(displays.native_qnx('ic'))
                vm.save(path/'native-ic-frame.json',dict(proof,live_proof_monotonic=99.0,port=11))
                self.assertIsNone(displays.native_qnx('ic'))

    def test_qnx_capture_rechecks_allocator_before_reading_committed_pixels(self):
        import qnx_native_display as display
        import cal_init_platform as cal
        port,_,pipe,dsi,w,h=display.ROLES['ic']
        size=(h<<16)|w
        addr=0x90100000
        route=(f'wfdSetPortMode:487] port:{port} mode:{w}x{h} @60\n'
               f'wfd_hw_set_dsim_enable:876] Port-{port} DSI{dsi} powered-on\n')
        window={'owner':pipe,'commits':1,'active-control':1,'active-start':0,
                'active-end':((h-1)<<16)|(w-1),'source-address':addr,'source-control':0,
                'source-frame-size':size,'source-crop-size':size,'source-output-size':size,
                'source-crop-offset':0,'source-writes':1}
        props={f'window-0-{k}':v for k,v in window.items()}
        props.update({f'{k}-{pipe}':v for k,v in dict(
            running=1,**{'active-dsi':dsi,'active-size':size,'frame-ticks':2,
                        'scanned-frames':1,'scanned-source':addr,'scanned-nonzero':1}).items()})
        props.setdefault('scanned-frames-0',0)
        props.setdefault('scanned-frames-1',0)
        allocation=dict(physical=hex(addr),iova='0x0',format=8,bpp=4,width=w,height=h,stride=4*w,usage=0x20)
        operations=[]
        snapshot_state='free'

        def qmp(op,args=None):
            if op in ('stop','cont'):
                operations.append(op)
            elif op=='qom-list':
                return [{'name':k} for k in props]
            elif op=='qom-get':
                return props.get(args['property'],0xff if args['property'].endswith('-owner') else 0)
            elif op=='query-status':
                return {'running':True}
            elif op=='pmemsave':
                val=args['val']
                operations.append(val)
                self.assertEqual(operations[0],'stop')
                raw=((cal.QNX_LOG_PREFIX+b'\n'+route.encode()+snapshot_state.encode()+b'\n').ljust(cal.QNX_LOG_BYTES,b'\0')
                     if val==cal.QNX_LOG_BASE else b'\x05\x11\x25\xff'*(w*h))
                self.assertEqual(len(raw),args['size'])
                Path(args['filename']).write_bytes(raw)
            else:
                self.fail(f'Unexpected QMP request: {op}')

        def allocations(log):
            return [allocation] if log.endswith('alloc\n') else []

        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            session=Mock(execute=Mock(side_effect=qmp))
            display.capture_live(session,path,route+'alloc\n',allocations)
            self.assertEqual(operations,['stop',cal.QNX_LOG_BASE,'cont'])
            self.assertEqual(json.loads((path/'native-ic-frame.json').read_bytes())['status'],'no_verified_qnx_pixels')
            snapshot_state='alloc'
            operations.clear()
            display.capture_live(session,path,route+'alloc\n',allocations)
            self.assertEqual(operations,['stop',cal.QNX_LOG_BASE,addr,'cont'])
            meta=json.loads((path/'native-ic-frame.json').read_bytes())
            self.assertEqual(meta['status'],'active_qnx_source_verified')
            self.assertIsNone(meta['live_proof_monotonic'])
            props[f'scanned-frames-{pipe}']=2
            operations.clear()
            image=path/meta['png']
            previous_mtime=image.stat().st_mtime_ns
                                                                            
                                                                      
            session.execute.reset_mock()
            display.capture_live(session,path,route+'alloc\n',allocations,call=qmp,properties=set(props))
            updated=json.loads((path/'native-ic-frame.json').read_bytes())
            session.execute.assert_not_called()
            self.assertEqual(operations,['stop',cal.QNX_LOG_BASE,addr,'cont'])
            self.assertEqual(updated['png_sha256'],meta['png_sha256'])
            self.assertEqual(image.stat().st_mtime_ns,previous_mtime)
            self.assertEqual(updated['scanned_frames'],2)
            self.assertIsNotNone(updated['live_proof_monotonic'])

    def test_live_native_center_is_bound_to_fresh_oem_plane_only(self):
        from linux_pid1_context import LINUX_SHA
        import hashlib
        import zlib

        def chunk(kind, data):
            return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))

        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vm.save(root/'linux-native-console.json',dict(
                source='vlm-console-mux',serial_index=1,host='127.0.0.1'))
            tap=vm.NativeConsoleTap(root)
            vm.save(root/'vlm-console-focus.json',dict(source='original-vlm-focus',
                running=True,host_monotonic=vm.time.monotonic(),vmid=4,read_only=False))
            png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1560,700,8,2,0,0,0))+
                 chunk(b'IDAT',zlib.compress((b'\0'+b'\x10\x20\x30'*1560)*700))+
                 chunk(b'IEND',b''))
            sha=hashlib.sha256(png).hexdigest()
            frame=root/f'native-center-{sha}.png'
            frame.write_bytes(png)
            metadata=dict(status='active_scanout_pages_verified',kernel_sha256=LINUX_SHA,
                frame_source='original_vm3_drm_primary',connector='Virtual-1',format='XR24',
                width=1560,height=700,modifier=0,nonzero=True,secure=False,
                captured_monotonic=vm.time.monotonic(),png_sha256=sha,png=frame.name)
            vm.save(root/'native-center-frame.json',metadata)
            outputs=vm.Displays({},native=tap)
            status=outputs.status()
            self.assertTrue(status['center']['available'])
            self.assertFalse(status['center']['factory_hmi_verified'])
            self.assertEqual(outputs.frame('center'),png)
            self.assertTrue(all(not status[role]['available'] for role in ('ic','hud')))
            gem=dict(metadata,status='active_gem_pages_verified',capture_origin='original_exynos_gem_sg',
                     sysmmu_matches_gem=False)
            vm.save(root/'native-center-frame.json',gem)
            self.assertFalse(outputs.status()['center']['available'])
            vm.save(root/'native-center-frame.json',dict(gem,gem_plane_verified=True,
                         last_frame_change_monotonic=vm.time.monotonic(),
                         last_buffer_switch_monotonic=vm.time.monotonic()))
            self.assertEqual(outputs.frame('center'),png)
            self.assertEqual(outputs.status()['center']['pixel_source'],'original-gem')
            self.assertFalse(outputs.status()['center']['frame_change_verified'])
            self.assertTrue(outputs.status()['center']['buffer_switch_verified'])
            vm.save(root/'native-center-frame.json',dict(gem,gem_plane_verified=True,
                         last_pixel_change_monotonic=vm.time.monotonic()))
            self.assertTrue(outputs.status()['center']['frame_change_verified'])
            self.assertFalse(outputs.status()['center']['buffer_switch_verified'])
            vm.save(root/'native-center-frame.json',dict(metadata,format='AR24'))
            self.assertFalse(outputs.status()['center']['available'])
            vm.save(root/'native-center-frame.json',dict(metadata,format='AR24',opaque_alpha_verified=True))
            self.assertEqual(outputs.frame('center'),png)
            no_blend=dict(metadata,format='AR24',alpha_ignored_by_original_plane=True,
                          planes_on_crtc=1,crtc_active=True)
            vm.save(root/'native-center-frame.json',no_blend)
            self.assertFalse(outputs.status()['center']['available'])
            no_blend['original_wod_blend']=dict(verified=True,plane_alpha8=255,
                                                drm_pixel_blend_mode=2,wod_blend=0)
            vm.save(root/'native-center-frame.json',no_blend)
            self.assertEqual(outputs.frame('center'),png)
            no_blend['original_wod_blend']['wod_blend']=1
            vm.save(root/'native-center-frame.json',no_blend)
            self.assertFalse(outputs.status()['center']['available'])
            vm.save(root/'native-center-frame.json',dict(metadata,secure=True))
            self.assertFalse(outputs.status()['center']['available'])
            vm.save(root/'native-center-frame.json',dict(metadata,captured_monotonic=vm.time.monotonic()-20))
            self.assertFalse(outputs.status()['center']['available'])
            vm.save(root/'native-center-frame.json',metadata)
            frame.write_bytes(png+b'x')
            with self.assertRaises(ValueError):
                outputs.frame('center')


class NativeForegroundTests(unittest.TestCase):
    def test_native_foreground_endpoint_requires_current_exact_original_color(self):
        from linux_pid1_context import LINUX_SHA
        import zlib

        def chunk(kind,data):
            return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            vm.save(root/'linux-native-console.json',dict(
                source='vlm-console-mux',serial_index=1,host='127.0.0.1'))
            focus=dict(source='original-vlm-focus',running=True,vmid=3,read_only=False)
            vm.save(root/'vlm-console-focus.json',dict(focus,host_monotonic=vm.time.monotonic()))
            stamp=vm.time.monotonic()
            row=b'\x00\x33\x66\x99\xff'+b'\x00'*4*1559
            png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1560,700,8,6,0,0,0))+
                 chunk(b'IDAT',zlib.compress(row*700))+chunk(b'IEND',b''))
            frame=root/'native-center-last-nonzero-foreground.png'
            frame.write_bytes(png)
            content='a'*64
            proof=dict(status='original_premultiplied_foreground_profile',kernel_sha256=LINUX_SHA,secure=False,
                capture_origin='original_sysmmu',full_display=False,width=1560,height=700,
                out_of_premultiplied_range_pixels=0,nonzero_rgb_pixels=700,captured_monotonic=stamp,
                active_content_sha256=content,gpu_generation=3,png_sha256=hashlib.sha256(png).hexdigest())
            active=dict(capture_monotonic=stamp,status='matching_physical_rows',
                        latest_write_content_matches=True,active_content_sha256=content,
                        latest_write=dict(content=content,rgb=700,generation=3))
            vm.save(frame.with_suffix('.json'),proof)
            active_path=root/'native-center-gpu-correlation.json'
            vm.save(active_path,active)
            server,_,token=vm.create_server({},0,native_session=root)
            worker=threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            base='http://127.0.0.1:'+str(server.server_address[1])

            def get(path,authenticated=True):
                headers={'X-8704-Token':token} if authenticated else {}
                return urllib.request.urlopen(urllib.request.Request(base+path,headers=headers),timeout=3)

            def denied(path,code,authenticated=True):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    get(path,authenticated)
                self.assertEqual(caught.exception.code,code)

            try:
                denied('/api/center-foreground',403,False)
                with get('/api/displays') as response:
                    center=json.load(response)['center']
                self.assertTrue(center['foreground_available'])
                self.assertFalse(center['available'])
                self.assertFalse(center['factory_hmi_verified'])
                digest=hashlib.sha256(png).hexdigest()
                self.assertEqual(center['frame_version'],digest)
                with get('/api/center-foreground') as response:
                    self.assertEqual(response.headers.get_content_type(),'image/png')
                    self.assertEqual(response.read(),png)
                with get('/api/center-foreground?version='+digest) as response:
                    self.assertEqual(response.read(),png)
                for query in ('version=', 'version=0', 'version='+'0'*64,
                              'version='+digest+'&version='+digest):
                    denied('/api/center-foreground?'+query,409)
                denied('/api/display-frame?display=center',409)
                vm.save(active_path,dict(active,capture_monotonic=stamp+1))
                denied('/api/center-foreground',409)
                vm.save(active_path,active)
                frame.write_bytes(png+b'x')
                denied('/api/center-foreground',409)
                frame.write_bytes(png)
                vm.save(root/'integrity.json',dict(original_unchanged=True))
                denied('/api/center-foreground',409)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.guests = {name: Mock(spec=vm.Guest) for name in ('linux', 'android')}
        for name, guest in self.guests.items():
            guest.status.return_value = {'guest': name, 'running': False, 'factory_hmi_verified': False}
        self.server, _, self.token = vm.create_server({}, 0, self.guests, 'host-test-token')
        self.origin = 'http://127.0.0.1:'+str(self.server.server_address[1])
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)

    def request(self, path, body=None, headers=None):
        headers = headers if headers is not None else {'X-8704-Token': self.token}
        request = urllib.request.Request(self.origin+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        return urllib.request.urlopen(request, timeout=3)

    def test_page_and_both_templates(self):
        with self.request('/') as response:
            page = response.read().decode('utf-8')
            self.assertIn('console-template', page)
            self.assertIn('display-template', page)
            self.assertIn('05 / HUD', page)
            self.assertIn('01 / Linux', page)
            self.assertIn('02 / Android', page)

    def test_missing_token_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/status', headers={})
        self.assertEqual(caught.exception.code, 403)

    def test_cross_origin_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/status', headers={'X-8704-Token': self.token, 'Origin': 'https://unrelated.invalid'})
        self.assertEqual(caught.exception.code, 403)

    def test_two_consoles_and_three_displays(self):
        with self.request('/api/status') as response:
            self.assertEqual(set(json.load(response)), {'linux', 'android'})
        with self.request('/api/displays') as response:
            self.assertEqual(set(json.load(response)), {'center', 'ic', 'hud'})

    def test_unbound_qnx_displays_do_not_return_fake_png(self):
        for name in ('ic', 'hud', 'invalid'):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request('/api/display-frame?display='+name)
            self.assertEqual(caught.exception.code, 409)
        self.guests['linux'].frame.assert_not_called()
        self.guests['android'].frame.assert_not_called()

    def test_center_only_reads_linux_frame(self):
        self.guests['linux'].frame.return_value = b'fixture-not-a-factory-frame'
        with self.request('/api/display-frame?display=center') as response:
            self.assertEqual(response.read(), b'fixture-not-a-factory-frame')
        self.guests['linux'].frame.assert_called_once_with()
        self.guests['android'].frame.assert_not_called()

    def test_android_command_not_sent_to_linux(self):
        with self.request('/api/serial', {'guest': 'android', 'command': 'id'}) as response:
            self.assertEqual(json.load(response), {'sent': True, 'execution_verified': False})
        self.guests['android'].serial.assert_called_once_with('id')
        self.guests['linux'].serial.assert_not_called()

    def test_unknown_guest_fails(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/start', {'guest': 'qnx'})
        self.assertEqual(caught.exception.code, 409)

    def test_invalid_json_shape_and_flag_rejected(self):
        for body in ([], {'guest': 'linux', 'original_dtb': 'false'}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request('/api/start', body)
            self.assertEqual(caught.exception.code, 409)
        self.guests['linux'].start.assert_not_called()

    def test_shutdown_is_authenticated(self):
        with self.request('/api/shutdown', {}) as response:
            self.assertTrue(json.load(response)['stopping'])
        self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())


if __name__ == '__main__':
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                              for case in (OemLinuxBootPolicyTests, LayoutTests, HTTPTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
