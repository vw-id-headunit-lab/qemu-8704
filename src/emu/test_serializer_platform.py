
import inspect
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
import serializer_platform as ser
import vlm_platform as platform


class SerializerTests(unittest.TestCase):
    def test_default_has_no_endpoint(self):
        self.assertIsNone(ser.policy(None,False));self.assertIsNone(ser.device_option(None,False))
        self.assertIsNone(inspect.signature(platform.probe).parameters['serializer_profile'].default)

    def test_explicit_profile_board_and_nonzero_deadlines(self):
        for profile,board,power,probe in ((None,True,1,100000),('wrong',True,1,1),('virtual-ti941-iid-v1',False,1,1),
                                         ('virtual-ti941-iid-v1',True,0,1),('virtual-ti941-iid-v1',True,1,1000000001)):
            with self.assertRaises(ValueError):ser.policy(profile,board,power,probe)
        self.assertIn('powerup-ns=1,probe-ns=1000000000',ser.device_option('virtual-ti941-iid-v1',True,1,1000000000))

    def test_control_endpoint_is_not_training_or_video(self):
        p=ser.policy('virtual-ti941-iid-v1',True)
        self.assertFalse(p['factory_images_modified']);self.assertFalse(p['physical_probe_semantics_verified'])
        self.assertFalse(p['exact_ti_suffix_verified']);self.assertFalse(p['dsi_fpd_training_hdcp_video_implemented'])
        self.assertFalse(p['virtual_cable_connected']);self.assertEqual(p['pdb']['output'],34)
        self.assertIn('bus=i2c17,address=0x1a',ser.device_option('virtual-ti941-iid-v1',True))

    def test_capture_only_reads_qom(self):
        qmp=Mock();qmp.execute.return_value=0
        self.assertEqual(len(ser.runtime_state(qmp)),len(ser.STATE_KEYS))
        for c in qmp.execute.call_args_list:self.assertEqual(c.args[0],'qom-get')

    def test_original_program_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'fixture';p.write_bytes(b'unknown')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):ser.original_contract(p)

    def test_recorded_sequence_has_two_independent_port_configs(self):
        rows=ser.initialization_writes()
        self.assertEqual(len(rows),42);self.assertEqual(rows[:2],[(1,8),(0x1e,1)])
        self.assertEqual(rows.count((0x1e,2)),2);self.assertEqual(rows.count((0x4f,0x8c)),2)
        self.assertEqual(rows.count((0x5c,0x67)),2)
        self.assertIn((0x41,0x86),rows);self.assertIn((0x41,0x94),rows)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SerializerTests))
    sys.exit(0 if result.wasSuccessful() else 1)
