import unittest
from unittest.mock import patch

import android_abox_watch as abox
import qnx_audio_clock_watch as watch


class Rsp:
    def __init__(self, reads=None):
        self.reads = reads or {}
        self.commands = []

    def command(self, command):
        self.commands.append(command)
        if command.startswith('vCont;'):
            return b'S05'
        if command.startswith('m'):
            return self.reads.get(command, b'E14')
        return b'OK'


def snap(pc, vmid, x0=0, attachment=None):
    return [{
        'thread': 'p03.05',
        'core': {'pc': hex(pc), 'x0': hex(x0), 'x30': hex(pc-4), 'x20': attachment},
        'system': {'VTTBR_EL2': hex(vmid << 48), 'TTBR0_EL1': '0x701234000'},
    }]


class ClockLifecycleTest(unittest.TestCase):
    def test_lower_va_only(self):
        rsp = Rsp({'m100e3188,4': b'01000000', 'm800,4': b'XYZ00000'})
        self.assertEqual(watch.mem(rsp, 0x100e3188, 4), b'\x01\0\0\0')
        self.assertIsNone(watch.mem(rsp, 0x800, 4))
        self.assertIsNone(watch.mem(rsp, (1<<39)-1, 2))
        self.assertIsNone(watch.mem(rsp, 0x100, 33))
        self.assertEqual(rsp.commands, ['m100e3188,4', 'm800,4'])

    def test_name_attach_capture_checks_original_and_channel(self):
        pc = watch.BIASES[0] + watch.SERVICE_SITES['name-attach-return']
        state = {}
        rsp = Rsp({'m100e3188,4': b'01000000'})
        with patch.object(watch, 'original_library') as elf:
            elf.return_value.at.return_value = b'12345678'
            watch.arm_service(rsp, state)
        rsp.reads[f'm{pc:x},8'] = b'3132333435363738'
        state['event_sequence'] = 4
        self.assertTrue(watch.observe_service(rsp, state, snap(pc, 2, 0x100e3180)))
        item, = state['qnx_clock_service']['events']
        self.assertEqual((item['kind'], item['channel_id'], item['failed'], item['sequence']),
                         ('name-attach-return', 1, False, 5))
        self.assertNotIn(str(pc), state['qnx_clock_service']['armed'])
        self.assertTrue(abox.pending({'armed': [], **state}))
        watch.close_service(rsp, state)
        self.assertFalse(abox.pending({'armed': [], **state}))
        self.assertTrue(state['qnx_clock_service']['withdrawn'])

    def test_wrong_vmid_bounded_without_reading_foreign_memory(self):
        pc = watch.BIASES[1] + watch.SERVICE_SITES['receive-entry']
        arm = {'kind': 'receive-entry', 'original': '3132333435363738', 'collisions': 0}
        state = {'qnx_clock_service': {'armed': {str(pc): arm}, 'events': [], 'collisions': []}}
        rsp = Rsp()
        for _ in range(watch.MAX_SITE_COLLISIONS):
            self.assertTrue(watch.observe_service(rsp, state, snap(pc, 4)))
        session = state['qnx_clock_service']
        self.assertEqual(session['events'], [])
        self.assertEqual(session['armed'], {})
        self.assertEqual(len(session['collisions']), watch.MAX_SITE_COLLISIONS)
        self.assertEqual(rsp.commands.count('vCont;s:p03.05'), watch.MAX_SITE_COLLISIONS-1)
        self.assertFalse(any(c.startswith('m') for c in rsp.commands))

    def test_dynamic_site_checks_runtime_text(self):
        rsp = Rsp({'m7800,8': b'3132333435363738'})
        armed = {}
        self.assertFalse(watch.arm_live_site(rsp, armed, 0x7800, 'site', b'12345670', 'root'))
        self.assertTrue(watch.arm_live_site(rsp, armed, 0x7800, 'site', b'12345678', 'root'))
        self.assertEqual(armed[str(0x7800)]['ttbr0'], 'root')
        self.assertFalse(watch.arm_live_site(rsp, armed, 0x7800, 'other', b'12345678', 'root'))
        self.assertEqual(rsp.commands.count('Z1,7800,4'), 1)


    def test_mapping_walk_detects_cleared_server_leaf(self):
        class Qmp:
            values = {0xe0000000: 0xe0001003, 0xe0001000: 0xe0002003,
                      0xe0002008: 0xe0003003, 0xe0003010: 0x1}

            def execute(self, cmd, args):
                addr = int(args['command-line'].split()[-1], 16)
                return f'{addr:x}: {self.values[addr]:#x}'

        identity = {'ttbr0':'0x70000e0000000', 'addresses':{'attachment':0x1010}}
        qmp = Qmp()
        row = watch.snapshot_server(qmp, identity)['attachment']
        self.assertEqual((row['physical'],row['aligned_word']), ('0xe0003010','0x1'))
        qmp.values[0xe0002008] = 0
        row = watch.snapshot_server(qmp, identity)['attachment']
        self.assertEqual(row['walk'][-1], ['0xe0002008','0x0'])
        self.assertNotIn('physical', row)

    def test_report_rejects_unrelated_process(self):
        proc = 0xffffff8000000000
        path = proc + 0x1000
        siginfo = proc + 0x2000
        rsp = Rsp({f'm{proc+0x1a0:x},8':path.to_bytes(8,'little').hex().encode(),
                   f'm{path:x},10':b'/bin/other\0'.ljust(16,b'\0').hex().encode()})
        result = watch.read_proc_report(rsp, {'x0':hex(proc),'x1':hex(siginfo)})
        self.assertIsNone(result)
        self.assertFalse(any(f'm{siginfo:x},' in cmd for cmd in rsp.commands))


if __name__ == '__main__':
    unittest.main()
