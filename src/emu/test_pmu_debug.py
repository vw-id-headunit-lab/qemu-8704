
import hashlib
import inspect
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import pmu_debug as debug
import vlm_platform as platform


class DebugInputTests(unittest.TestCase):
    def test_default_strict_and_complete_explicit_inputs_required(self):
        self.assertIsNone(debug.policy(None,None,0))
        for words in ((None,0,0),(0,None,0),(0,0,None),(-1,0,0),(0,0x100000000,0)):
            with self.assertRaises(ValueError):debug.policy('virtual-input-bank-v1','virtual-cold-v1',96000000,*words)
        for name in ('pmu_dptx_debug_profile','pmu_dptx_debug_word0','pmu_dptx_debug_word1','pmu_dptx_debug_word2'):
            self.assertIsNone(inspect.signature(platform.probe).parameters[name].default)

    def test_profile_clock_and_parent_pmu_bound(self):
        for profile,pmu,hz in ((None,'virtual-cold-v1',96000000),('wrong','virtual-cold-v1',96000000),
                                ('virtual-input-bank-v1',None,96000000),('virtual-input-bank-v1','virtual-cold-v1',0)):
            with self.assertRaises(ValueError):debug.policy(profile,pmu,hz,0,0,0)

    def test_exact_addresses_and_no_physical_effect_claim(self):
        result=debug.policy('virtual-input-bank-v1','virtual-cold-v1',96000000,0,0xffffffff,1)
        self.assertEqual([x['address'] for x in result['input_words']],['0x1046210c','0x18a00604','0x18a00614'])
        self.assertFalse(result['physical_reset_snapshot_verified']);self.assertFalse(result['hardware_field_meanings_verified'])
        self.assertFalse(result['factory_images_modified']);self.assertFalse(result['power_clock_ready_or_gpu_effects'])

    def test_runtime_capture_does_not_cause_mmio(self):
        qmp=Mock();qmp.execute.return_value=0
        self.assertEqual(len(debug.runtime_state(qmp)),7)
        self.assertTrue(all(c.args[0]=='qom-get' for c in qmp.execute.call_args_list))

    def test_original_identity_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'fixture.so';source.write_bytes(b'not-original')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):debug.original_consumer(source,Path(folder))

    def test_three_pointer_binding_and_length_instruction(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'fixture.so';source.write_bytes(b'fixture')
            image=Mock();image.elf.at.side_effect=lambda a,n:bytes.fromhex('440140b9' if a==0x25b44 else 'a5100051')
            image.string.return_value=' '
            prefix='Auto9 PMU unsupported read offset=0x210c\nAuto9 PMU caller cpu=0 el=0 '
            suffix='fp=0x100c7420 sp=0x100c7420 x0=0x100c75aa x1=0x782bc5a8 x2=0x0 x3=0x782bc5a8\n'
            trace=root/'tcg.log';trace.write_text(prefix+'pc=0x782a6b44 lr=0x782a6b5c '+suffix,encoding='utf-8')
            with patch.object(debug,'DISPLAY_SHA',hashlib.sha256(b'fixture').hexdigest()),patch.object(debug.cmu_sequence,'DynamicCalImage',return_value=image):
                result=debug.original_consumer(source,root)
                self.assertEqual(result['observations'][0]['derived_library_base'],'0x78281000')
                self.assertIn('subtracts4',result['dump_length_rule'])
                trace.write_text(prefix+'pc=0x782a6b44 lr=0x782a6b60 '+suffix,encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'do not bind'):debug.original_consumer(source,root)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DebugInputTests))
    sys.exit(0 if result.wasSuccessful() else 1)
