
import hashlib
import inspect
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock,patch
import clock_rpc_platform as clock
from patch_qemu_exynos import qnx_vclk_watch_rows
from test_qnx_gtf_watch import executable_bytes
import vlm_platform as platform


def table(**entries):
    data=bytearray(4096)
    for key,value in entries.items():struct.pack_into('<Q',data,int(key[1:])*8,value)
    return bytes(data)


class ClockObserverTests(unittest.TestCase):
    def test_qnx_vrpc_bound_to_original_backend(self):
        raw=clock.BACKEND.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),clock.BACKEND_SHA)
        model=Path(__file__).with_name('exynos-auto9-qnx-vclk-watch.inc.c').read_text(encoding='utf-8')
        sites=qnx_vclk_watch_rows(model)
        self.assertEqual({name:addr for name,(addr,_) in sites.items()},
                         {'receive':0x6de8,'worker':0x6614,'dispatch':0x640c,
                          'return':0x6410,'reply':0x6434})
        for addr,words in sites.values():
            self.assertEqual(executable_bytes(raw,addr,16),struct.pack('<4I',*words))
                                                                          
                                                                            
        for addr,words in ((0x3e38,(0x90000001,0x2a1403e3,0xaa1503e2,0x91296021)),
                           (0x76d8,(0x34000183,0xf0ffffe0,0xb900e27f,0x910e4000)),
                           (0x7720,(0xaa1303e2,0x91164021,0x91032260,0x94000d99)),
                           (0x7690,(0xf9400c00,0x91003000,0xd65f03c0))):
            self.assertEqual(executable_bytes(raw,addr,len(words)*4),struct.pack('<'+'I'*len(words),*words))

    def test_original_failure_and_tls_contract(self):
        row=clock.contract()
        self.assertFalse(row['memory_modified'])
        self.assertEqual(row['ifs_member_offset'],'0x3cc000')
        self.assertIn('name_open',row['failure_semantics'])
        self.assertEqual(row['libc']['code_hex'],'60d03bd5000840f9c0035fd6')
        self.assertEqual(row['backend']['callback_table'][:4],['0x4668','0x4408','0x45b8','0x4370'])

    def test_clock_source_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'library';p.write_bytes(b'changed')
            with patch.object(clock,'LIBRARY',p):
                with self.assertRaisesRegex(ValueError,'identity mismatch'):clock.contract()

    def test_libc_source_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'library';p.write_bytes(b'changed')
            with patch.object(clock,'LIBC',p):
                with self.assertRaisesRegex(ValueError,'libc identity'):clock.contract()

    def test_three_level_page_and_asid(self):
        pages={0x1000:table(i0=0x2003),0x2000:table(i1=0x3003),0x3000:table(i5=0xe0010003)}
        rows=clock.enumerate_user_maps(25,0xf000000001000,pages.__getitem__)
        self.assertEqual(rows,[{'virtual':0x205000,'physical':0xe0010000,'bytes':4096,'descriptor':'0xe0010003'}])

    def test_blocks_terminate_without_fake_child_tables(self):
        read=Mock(return_value=table(i2=0x80000001))
        rows=clock.enumerate_user_maps(25,0x1000,read)
        self.assertEqual(rows[0]['bytes'],1<<30);self.assertEqual(read.call_count,1)

    def test_empty_root_is_observed_absence(self):
        self.assertEqual(clock.enumerate_user_maps(25,0x1000,lambda a:bytes(4096)),[])

    def test_non_native_granule_or_size_rejected(self):
        for tcr in (24,26,25|(1<<14),25|(2<<14)):
            with self.assertRaisesRegex(ValueError,'39-bit'):clock.enumerate_user_maps(tcr,0x1000,Mock())

    def test_budget_is_bounded(self):
        with self.assertRaisesRegex(ValueError,'budget'):
            clock.enumerate_user_maps(25,0x1000,lambda a:table(i0=0x2003),budget=1)

    def test_short_tables_rejected(self):
        with self.assertRaisesRegex(ValueError,'Truncated'):clock.enumerate_user_maps(25,0x1000,lambda a:b'')

    def test_reserved_last_level_rejected(self):
        pages={0x1000:table(i0=0x2003),0x2000:table(i0=0x3003),0x3000:table(i0=0x4001)}
        with self.assertRaisesRegex(ValueError,'Reserved'):clock.enumerate_user_maps(25,0x1000,pages.__getitem__)

    def test_observer_is_opt_in(self):
        self.assertFalse(inspect.signature(platform.probe).parameters['clock_backend_watch'].default)
        self.assertIsNone(inspect.signature(platform.probe).parameters['lk_pwm_profile'].default)
        with self.assertRaisesRegex(ValueError,'Backend clock observer'):
            platform.probe(None,1,0,clock_backend_watch=True)

    def test_pwm_guard_precedes_launch(self):
        for value,board in [('bad',True),('virtual-lk-pwm24-v1',False)]:
            with self.assertRaisesRegex(ValueError,'LK PWM'):
                platform.probe(None,1,0,lk_pwm_profile=value,board_adapter=board)

    def test_hardware_breakpoints_only(self):
        rsp=Mock();rsp.command.return_value=b'OK';rows=[{'pc':0x1234}]
        clock.arm(rsp,rows);clock.withdraw(rsp,rows)
        self.assertEqual([c.args[0] for c in rsp.command.call_args_list],['Z1,1234,4','z1,1234,4'])

    def test_async_stop_is_drained(self):
        rsp=Mock();rsp.command.return_value=b'T05thread:p03.01;';rsp.receive.return_value=b'OK'
        clock.command_ok(rsp,'z1,1234,4');rsp.receive.assert_called_once()

    def test_breakpoint_errors_are_not_ignored(self):
        rsp=Mock();rsp.command.return_value=b'E01'
        with self.assertRaisesRegex(ValueError,'failed'):clock.command_ok(rsp,'Z1,1234,4')


if __name__=='__main__':unittest.main()
