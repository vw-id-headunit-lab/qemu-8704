
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import qnx_tmu_platform as qnx
import tmu_platform as tmu
import vlm_platform


class QnxTmuTests(unittest.TestCase):
    def test_default_remains_absent(self):
        self.assertEqual(tmu.machine_options(None), '')
        self.assertIsNone(inspect.signature(vlm_platform.probe).parameters['tmu_profile'].default)

    def test_legacy_profile_not_redefined(self):
        self.assertEqual(tmu.machine_options('virtual-onepoint-v1',25,96000000),',x-exynos-tmu-profile=1')
        self.assertEqual(tmu.profile_policy()['profile'],'virtual-onepoint-v1')

    def test_explicit_controller_and_management_cpu(self):
        self.assertEqual(tmu.machine_options('virtual-qnx-controller-v1',-40,96000000),',x-exynos-tmu-profile=2')
        for profile,temp,clock in (('wrong',25,96000000),('virtual-qnx-controller-v1',25,0),
                                   ('virtual-qnx-controller-v1',151,96000000),(None,30,0)):
            with self.assertRaises(ValueError):tmu.machine_options(profile,temp,clock)

    def test_assumptions_and_scope_are_explicit(self):
        p=qnx.profile_policy()
        self.assertFalse(p['factory_images_modified']);self.assertFalse(p['ready_value_injected'])
        self.assertTrue(p['sampling']['cold_enable_is_assumption'])
        self.assertTrue(p['sampling']['banks_independently_enabled'])
        self.assertIn('historical-sample FIFO',p['limits']);self.assertIn('462/461',p['controller']['irq'])

    def test_unknown_original_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'fixture';source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):qnx.original_contract(source)

    def test_fixed_original_domains_and_irq_contract(self):
        c=qnx.original_contract()
        self.assertEqual([r['spi'] for r in c['domains']],[462,462,461,461,461])
        self.assertEqual(c['domains'][2]['start_sensor'],10)
        self.assertEqual(c['control']['enable_bit'],0)
        self.assertEqual(c['valid']['offset'],40)

    def test_runtime_capture_does_not_write_guest(self):
        qmp=Mock();qmp.execute.return_value=0
        state=qnx.runtime_state(qmp)
        self.assertIn('control-history-first256',state)
        for call in qmp.execute.call_args_list:self.assertEqual(call.args[0],'qom-get')


if __name__=='__main__':unittest.main()
