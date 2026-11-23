
from pathlib import Path
import inspect
import tempfile
import unittest
from unittest.mock import patch
import cal_init_platform as cal
import vlm_platform as platform


class CalInitTests(unittest.TestCase):
    def test_original_ect_exact_source_slice_and_reservation(self):
        data,meta=cal.original_ect();raw=Path(meta['source']).read_bytes()
        self.assertEqual(data,raw[0x40c00:0x40c00+35076]);self.assertEqual(len(data),35076)
        self.assertEqual(data[:8],b'PARA000a');self.assertEqual(meta['physical'],'0xe9a00000')
        self.assertEqual(meta['reserved_bytes'],0x3a000);self.assertFalse(meta['generated_tables'])
        self.assertEqual([r['name'] for r in meta['blocks']],['PLL','DVFS','ASV','GEN','NEWTIME','THERMAL'])

    def test_sectionless_original_consumer_load_window(self):
        raw=cal.ECT_LIB.read_bytes();self.assertEqual(raw[0x3a:0x40],bytes(6))
        self.assertEqual(len(cal.load_window(raw,0x21c8,0x58)),0x58)

    def test_program_window_rejects_missing_ranges(self):
        raw=cal.ECT_LIB.read_bytes()
        for address,size in ((-1,4),(0x21c8,0),(0x21c8,-1),(0x21c8,1048577),(0x70000000,64)):
            with self.assertRaises(ValueError):cal.load_window(raw,address,size)

    def test_truncated_program_headers_rejected(self):
        with self.assertRaises(ValueError):cal.load_window(cal.ECT_LIB.read_bytes()[:65],0x21c8,4)

    def test_changed_original_bl2_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'changed';p.write_bytes(b'changed')
            with patch.object(cal.platform,'factory_file',return_value=p):
                with self.assertRaisesRegex(ValueError,'BL2 ECT source'):cal.original_ect()

    def test_changed_original_consumer_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'changed';p.write_bytes(b'changed')
            with patch.object(cal,'ECT_LIB',p):
                with self.assertRaisesRegex(ValueError,'ECT consumer'):cal.original_ect()

    def test_original_cal_contains_91_config_rows(self):
        c=cal.contract();self.assertEqual(len(c['lpm_init']),91)
        self.assertEqual({r['op'] for r in c['lpm_init']},{1,3})
        self.assertEqual(sum(r['op']==3 for r in c['lpm_init']),4)
        self.assertEqual(c['board_init_hook'],'0x4ae00')

    def test_cold_dvfs_arrays_are_not_runtime_capacities(self):
        nodes=cal.contract()['nodes'];self.assertEqual(len(nodes),11)
        self.assertTrue(all(n['cold_member_count']==0 and n['cold_members_pointer']=='0x0' for n in nodes))
        self.assertNotIn('member_capacity',nodes[0])

    def test_source_masks_and_sparse_register_groups(self):
        groups=cal.config_groups();self.assertEqual(len(groups['sysreg']),42)
        hold=next(r for r in groups['pmu'] if r['address']==0x1046030c)
        self.assertEqual(hold['mask'],0x80000100)
        self.assertTrue(all(r['mask']==0xffffffff for r in groups['sysreg']))
        self.assertEqual(len({r['address'] for rows in groups.values() for r in rows}),sum(map(len,groups.values())))

    def test_generated_model_is_deterministic_and_uses_sparse_memory_regions(self):
        with tempfile.TemporaryDirectory() as d:
            result=cal.emit_configs(Path(d));actual=Path(result['path']).read_bytes()
            self.assertEqual(actual,Path(cal.__file__).with_name('exynos-auto9-cal-init.inc.c').read_bytes())
            self.assertNotIn(b'sysbus_init_mmio',actual)
            self.assertIn(b'memory_region_add_subregion',actual)
            self.assertIn(b'if (!enabled) { return; }',actual)
            self.assertTrue(result['configuration_retention_only']);self.assertFalse(result['physical_clock_power_effects'])

    def test_profiles_are_opt_in(self):
        signature=inspect.signature(platform.probe)
        self.assertIsNone(signature.parameters['ect_handoff_profile'].default)
        self.assertIsNone(signature.parameters['cal_init_config_profile'].default)
        with self.assertRaisesRegex(ValueError,'Original ECT handoff'):
            platform.probe(None,1,0,ect_handoff_profile='original-bl2-ect-v1')
        with self.assertRaisesRegex(ValueError,'CAL init config'):
            platform.probe(None,1,0,cal_init_config_profile='virtual-qnx-cal-config-v1')

    def test_live_qnx_log_uses_factory_reservation_and_keeps_tail(self):
        self.assertTrue(cal.qnx_log_source().is_file())
        payload=cal.QNX_LOG_PREFIX+b'x'*0x21000+b'\nstartup\n'
        area=(payload+b'\0').ljust(cal.QNX_LOG_BYTES,b'x')
        self.assertEqual(cal.decode_qnx_log_area(area),(payload,True))
        self.assertEqual(cal.decode_qnx_log_area(bytes(cal.QNX_LOG_BYTES)),(b'',False))
        with self.assertRaisesRegex(ValueError,'Incomplete original QNX'):
            cal.decode_qnx_log_area(area[:-1])


if __name__=='__main__':unittest.main()
