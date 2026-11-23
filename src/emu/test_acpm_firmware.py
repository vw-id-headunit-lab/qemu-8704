
import struct
import sys
import unittest
import acpm_firmware as firmware


class FirmwareTests(unittest.TestCase):
    def test_original_scatter_is_not_flat_candidate(self):
        raw,meta=firmware.original_image()
        self.assertEqual(len(raw),0x38000)
        self.assertEqual([s['local_destination'] for s in meta['segments']],['0x3000','0xb000'])
        self.assertEqual([s['bytes'] for s in meta['segments']],[0x5000,0x22000])
        self.assertEqual(struct.unpack_from('<4I',raw,0x3000),(0xb000,0x3259,0x3279,0x327f))
        self.assertEqual(raw[0x8000:0xb000],b'\0'*0x3000)
        self.assertEqual(firmware.public_metadata(raw)['channel_count'],0)

    def test_modified_copy_loop_or_literal_rejected(self):
        source=firmware.platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin').read_bytes()
        for offset in (0x22c,0x238,0x3d0,0x410):
            bad=bytearray(source);bad[offset]^=1
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError):firmware.scatter_load(bad)
        with self.assertRaises(ValueError):firmware.scatter_load(source[:-1])

    def test_metadata_parser_rejects_unbounded_tables(self):
        raw=bytearray(0x38000)
        struct.pack_into('<I',raw,0x7008,0xfffffff0)
        struct.pack_into('<I',raw,0x7018,10)
        meta=firmware.public_metadata(raw)
        self.assertIsNone(meta['channels'])
        self.assertFalse(meta['usable_for_native_boot'])
        struct.pack_into('<I',raw,0x7018,33)
        self.assertIsNone(firmware.public_metadata(raw)['channels'])
        with self.assertRaises(ValueError):firmware.public_metadata(b'')

    def test_actual_initializer_generates_ten_channels_before_mmio(self):
        raw,_=firmware.original_image();result,after=firmware.execute(raw)
        self.assertIsNotNone(result['stopped'])
        self.assertEqual(result['metadata']['channel_count'],10)
        self.assertEqual(result['metadata']['plugin_count'],8)
        self.assertEqual(len(result['metadata']['channels']),10)
        self.assertIn('0x4528',result['entry_visits'])
        self.assertNotEqual(raw[0x7000:0x70c0],after[0x7000:0x70c0])
        self.assertFalse(result['native_boot_usable'])
        self.assertFalse(result['ap_connected'])
        self.assertFalse(result['full_acpm_boot_verified'])

    def test_actual_rings_are_generated_but_unknown_clock_stops(self):
        raw,_=firmware.original_image()
        result,after=firmware.execute(raw,system_registers=True,counter_config=True)
        self.assertEqual(result['stopped']['address'],'0xe000e010')
        self.assertIn('reference clock',result['stopped']['detail'])
        self.assertIn('0x43dc',result['entry_visits'])
        self.assertEqual(result['metadata']['plugins'][1]['words'][1],'0xb001')
        self.assertEqual(result['metadata']['channels'][0]['words'][6],'0x7910')
        self.assertEqual(result['nvic_external_lines_modelled'],64)
        self.assertFalse(result['native_boot_usable'])

    def test_conditional_clock_does_not_invent_counter_countdown(self):
        raw,_=firmware.original_image()
        result,_=firmware.execute(raw,system_registers=True,counter_config=True,reference_divider=1)
        self.assertEqual(result['stopped']['address'],'0xa0430008')
        self.assertEqual(result['conditional_systick_reference_divider'],1)
        self.assertIn('not measured',result['clock_provenance'])
        self.assertFalse(result['full_acpm_boot_verified'])

    def test_invalid_execution_bounds_rejected(self):
        raw,_=firmware.original_image()
        for size in (0,1000001):
            with self.assertRaises(ValueError):firmware.execute(raw,instructions=size)
        with self.assertRaises(ValueError):firmware.execute(b'')


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FirmwareTests))
    sys.exit(0 if result.wasSuccessful() else 1)
