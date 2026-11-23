
from pathlib import Path
import inspect
import tempfile
import unittest
from unittest.mock import Mock,patch
import linux_i2c_platform as i2c
import vlm_platform as platform


class LinuxI2cTests(unittest.TestCase):
    def test_default_profile_is_off(self):
        self.assertEqual(i2c.profile_options(None),[])
        self.assertIsNone(inspect.signature(platform.probe).parameters['usi_i2c_profile'].default)

    def test_profile_needs_original_board(self):
        for name,board in (('bad',True),('virtual-linux-i2c-v1',False)):
            with self.assertRaises(ValueError):i2c.profile_options(name,board)

    def test_dotted_driver_global_is_unambiguous(self):
        self.assertEqual(i2c.profile_options('virtual-linux-i2c-v1'),['-global','driver=exynos-auto9.i2c,property=virtual-linux-ports,value=1'])

    def test_native_topology_from_original_dt(self):
        rows=i2c.native_layout()
        self.assertEqual([r['index'] for r in rows],[1,5,9,19])
        self.assertEqual([r['spi'] for r in rows],[344,348,352,363])
        self.assertEqual([r['base'] for r in rows],[0x10310000,0x10350000,0x10390000,0x10970000])

    def test_no_overlap_with_existing_qnx_ports(self):
        selectors={r['selector'] for r in i2c.native_layout()}
        self.assertEqual(len(selectors),4)
        self.assertFalse(selectors&{0x10821014,0x10821018,0x10221008,0x10821000,0x10821008,0x10821020})

    def test_physical_peers_not_created_from_dt_metadata(self):
        d=i2c.inventory()
        self.assertFalse(d['factory_peer_implemented'])
        self.assertTrue(any('iam20680@69' in n for n in d['ports'][0]['children']))

    def test_pinned_original_instruction_contract(self):
        d=i2c.original_contract()
        self.assertEqual((d['selector_mask'],d['selector_value']),(7,4))
        self.assertFalse(d['factory_images_modified']);self.assertFalse(d['silicon_timing_verified'])

    def test_source_guard_rejects_changed_dt(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'changed';p.write_bytes(b'not-original')
            with patch.object(i2c.spi,'DT',p):
                with self.assertRaisesRegex(ValueError,'identity mismatch'):i2c.inventory()

    def test_runtime_observation_is_only_qom_reads(self):
        qmp=Mock();qmp.execute.return_value=0;d=i2c.runtime_states(qmp)
        self.assertEqual(set(d),{'1','5','9','19'})
        self.assertEqual(len(qmp.execute.call_args_list),80)
        self.assertTrue(all(c.args[0]=='qom-get' for c in qmp.execute.call_args_list))

    def test_legacy_and_linux_completion_are_separate(self):
        s=Path(i2c.__file__).with_name('exynos-auto9-i2c.inc.c').read_text(encoding='utf-8')
        self.assertIn('if (s->linux_port && s->reading && s->rx_num)',s)
        self.assertIn('!linux_port || s->linux_profile == 1',s)
        self.assertIn('i2c_start_transfer(s->bus',s)
        self.assertIn('s->linux_port && !(s->regs[0x40 / 4] & 0x80000000U)',s)
        self.assertNotIn('0xa5000000',s.lower())

    def test_observer_requires_original_linux_and_profile(self):
        with self.assertRaisesRegex(ValueError,'Clock observer needs'):
            platform.probe(None,.1,0,clock_rpc_watch=True)

    def test_observer_uses_hardware_breakpoint_not_image_write(self):
        rsp=Mock();rsp.command.return_value=b'OK'
        state=i2c.install_clock_watch(rsp)
        self.assertFalse(state['memory_modified']);self.assertFalse(state['hit'])
        self.assertEqual(rsp.command.call_args.args[0],'Z1,ffffffc010686d44,4')

    def test_observer_drains_async_stop_before_remove_reply(self):
        rsp=Mock();rsp.command.return_value=b'T05thread:p03.02;';rsp.receive.return_value=b'OK'
        qmp=Mock();qmp.execute.return_value='CPU#1\n PC=ffffffc010686d44 X00=0000000000000001'
        with tempfile.TemporaryDirectory() as d,patch.object(i2c,'capture_clock_rpc') as capture:
            state=i2c.collect_clock_watch(rsp,qmp,Path(d)/'watch',{'guest':'linux'})
            self.assertTrue(state['hit']);self.assertFalse(state['memory_modified'])
            capture.assert_called_once();rsp.receive.assert_called_once()


if __name__=='__main__':unittest.main()
