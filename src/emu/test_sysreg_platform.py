
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import sysreg_platform as spi
import vlm_platform as platform


class SpiContractTests(unittest.TestCase):
    def test_default_does_not_enable_new_apertures(self):
        self.assertEqual(spi.profile_options(None),[])
        self.assertIsNone(inspect.signature(platform.probe).parameters['usi_spi_profile'].default)

    def test_explicit_known_profiles_and_board(self):
        for profile,board in (('unknown',True),('virtual-spi1-v1',False),('virtual-linux-spi-v1',False)):
            with self.assertRaises(ValueError):spi.profile_options(profile,board)

    def test_dotted_type_uses_unambiguous_global_syntax(self):
        self.assertEqual(spi.profile_options('virtual-spi1-v1'),['-global','driver=exynos-auto9.usi-spi,property=virtual-profile,value=1'])
        self.assertEqual(spi.profile_options('virtual-linux-spi-v1')[-1].split(',')[-1],'value=2')

    def test_original_dt_distinct_fifo_capacities(self):
        rows=spi.native_layout()
        self.assertEqual([r['port'] for r in rows],[1,6,7,10])
        self.assertEqual([r['fifo_bytes'] for r in rows],[256,256,64,64])

    def test_native_dma_and_gic_route(self):
        rows=spi.native_layout()
        self.assertEqual([r['spi'] for r in rows],[347,358,360,366])
        self.assertEqual([(r['tx_request'],r['rx_request']) for r in rows],[(3,2),(13,12),(15,14),(-1,-1)])

    def test_selector_and_existing_qnx_ports_do_not_overlap(self):
        rows=spi.native_layout()
        self.assertEqual(len({r['selector'] for r in rows}),4)
        self.assertFalse({r['selector'] for r in rows}&{0x10821014,0x10821018})

    def test_original_instruction_contract(self):
        c=spi.original_contract('virtual-linux-spi-v1')
        self.assertEqual((c['selector_mask'],c['spi_value']),(7,2))
        self.assertEqual((c['tx_fifo_shift'],c['rx_fifo_shift'],c['tx_done_bit']),(6,15,25))
        self.assertEqual(c['dma_burst_words']['mode_bit_0_clear'],1)
        self.assertEqual(c['dma_burst_words']['mode_bit_0_set'],4)

    def test_source_guard_rejects_changed_dt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'changed';p.write_bytes(b'not-original')
            with patch.object(spi,'DT',p):
                with self.assertRaisesRegex(ValueError,'identity mismatch'):spi.inventory()

    def test_no_silicon_or_application_response_claim(self):
        c=spi.original_contract()
        self.assertFalse(c['factory_peer_implemented']);self.assertFalse(c['factory_images_modified'])
        self.assertFalse(c['physical_reset_and_timing_verified'])

    def test_runtime_queries_read_only_and_explicit_port(self):
        qmp=Mock();qmp.execute.side_effect=lambda op,args: [{'name':'tx-overruns'}] if op=='qom-list' else 0
        result=spi.runtime_state(qmp,10)
        self.assertEqual(set(result),set(spi.KEYS)|{'tx-overruns'})
        for c in qmp.execute.call_args_list:
            self.assertIn(c.args[0],('qom-get','qom-list'));self.assertEqual(c.args[1]['path'],'/machine/exynos-spi10')

    def test_no_full_page_or_completion_marker_model(self):
        source=Path(spi.__file__).with_name('exynos-auto9-usi-spi.inc.c').read_text(encoding='utf-8')
        self.assertIn('timer_mod(s->deadline',source)
        self.assertIn('ssi_transfer(s->bus, value)',source)
        self.assertIn('return MEMTX_DECODE_ERROR;',source)
        self.assertIn('qdev_get_gpio_in(dma, rx_req)',source)
        self.assertNotIn('0xa5000000',source.lower())
        self.assertNotIn('0x1000);',source)


if __name__=='__main__':unittest.main()
