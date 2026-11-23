
import inspect
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import Mock
import pmu_domain as domain
import vlm_platform as platform


class DomainPolicyTests(unittest.TestCase):
    def test_strict_default(self):
        self.assertIsNone(domain.policy(None,None,0))
        self.assertEqual(inspect.signature(platform.probe).parameters['pmu_domain_profile'].default,None)
        self.assertEqual(inspect.signature(platform.probe).parameters['pmu_domain_supply_mask'].default,0)

    def test_requires_native_pmu_and_explicit_profile(self):
        for profile,pmu,clock in ((None,'virtual-cold-v1',96000000),('wrong','virtual-cold-v1',96000000),
                                   ('virtual-sequencer-v1',None,96000000),('virtual-sequencer-v1','virtual-cold-v1',0)):
            with self.assertRaises(ValueError):domain.policy(profile,pmu,clock,1001,0)

    def test_finite_environment_bounds(self):
        for latency,supply in ((0,0),(-1,0),(1000000001,0),(1000,-1),(1000,0x100000)):
            with self.assertRaises(ValueError):domain.policy('virtual-sequencer-v1','virtual-cold-v1',96000000,latency,supply)
        for latency,supply in ((1,0),(1000000000,0xfffff)):
            self.assertEqual(domain.policy('virtual-sequencer-v1','virtual-cold-v1',96000000,latency,supply)['transition_ns'],latency)

    def test_no_physical_claim_or_image_change(self):
        policy=domain.policy('virtual-sequencer-v1','virtual-cold-v1',96000000,1000,0xfffff)
        self.assertFalse(policy['factory_images_modified'])
        self.assertFalse(policy['physical_timing_or_power_spec_verified'])
        self.assertFalse(policy['physical_clock_gpu_dp_or_reset_effects_implemented'])
        self.assertEqual(len(policy['domain_config_offsets']),20)
        self.assertEqual(len(set(domain.CONFIGS)),20)
        self.assertEqual(domain.CONFIGS[9],0x2100)
        self.assertTrue(any('not executed' in s for s in policy['assumptions']))

    def test_state_capture_only_reads_qom_not_mmio(self):
        qmp=Mock();qmp.execute.return_value=7
        result=domain.runtime_state(qmp)
        self.assertEqual(set(result),set(domain.STATE_KEYS))
        self.assertEqual(qmp.execute.call_count,len(domain.STATE_KEYS))
        for call in qmp.execute.call_args_list:
            self.assertEqual(call.args[0],'qom-get')
            self.assertEqual(call.args[1]['path'],domain.QOM_PATH)

    def test_rejects_unknown_cal_before_metadata_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'unknown.so';p.write_bytes(b'not-original-CAL')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):domain.original_contract(p)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DomainPolicyTests))
    sys.exit(0 if result.wasSuccessful() else 1)
