
import inspect
import struct
import unittest
import lk_handoff as lk
import ufs_platform as ufs
import vlm_platform as platform


class HandoffTests(unittest.TestCase):
    def test_original_producer_and_call_boundary(self):
        c=lk.original_contract()
        self.assertEqual(c['producer'],'0x3b59c');self.assertEqual(c['write_instruction'],'0x3b5c0')
        self.assertEqual(c['device_lun'],0xd0);self.assertFalse(c['producer_executed'])
        self.assertFalse(c['runtime_physical_link_address_verified'])

    def test_signed_mov_alias_materializes_a5(self):
        c=lk.original_contract();d=lk.scan_a64(bytes.fromhex(c['producer_bytes']),base=0x3b5ac)
        self.assertEqual(d['a5_value_candidates'][0]['value'],'0xa5000000')
        self.assertEqual(d['handoff_access_candidates'][-1]['address'],'0x3b5c0')
        self.assertEqual(d['handoff_access_candidates'][-1]['mnemonic'],'str')
        self.assertIsNone(d['handoff_access_candidates'][-1]['value'])

    def test_zero_clear_not_confused_with_done(self):
        raw=platform.original('lk-boot/lk.bin').read_bytes()
        d=lk.scan_a64(raw[0x36314:0x3632c],base=0x36314)
        self.assertEqual(d['handoff_access_candidates'][-1]['value'],'0x0')
        self.assertEqual(d['a5_value_candidates'],[])

    def test_default_does_not_enable_byte_mode_or_done(self):
        self.assertEqual(ufs.descriptor_options(None,False),[])
        self.assertIsNone(inspect.signature(platform.probe).parameters['ufs_descriptor_profile'].default)
        self.assertFalse(ufs.descriptor_policy()['lk_done_written'])

    def test_explicit_profile_requires_native_ufs(self):
        for p,n in (('wrong',True),('virtual-samsung-byte-v1',False)):
            with self.assertRaises(ValueError):ufs.descriptor_options(p,n)
        self.assertEqual(ufs.descriptor_options('virtual-samsung-byte-v1',True),['-global','ufs.x-samsung-byte-granularity=on'])

    def test_descriptor_units_without_guest_rewrite(self):
        old=struct.unpack('<6I4H',ufs.utrd(prds=2))
        new=struct.unpack('<6I4H',ufs.utrd(prds=2,byte_granularity=True))
        self.assertEqual(old[:6],new[:6]);self.assertEqual(old[-4:],(288,256,2,512))
        self.assertEqual(new[-4:],(1152,1024,32,2048))

    def test_source_guard_rejects_changed_lk(self):
        from unittest.mock import patch
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as f:
            p=Path(f)/'changed';p.write_bytes(b'changed')
            with patch.object(lk.platform,'original',return_value=p):
                with self.assertRaisesRegex(ValueError,'identity mismatch'):lk.original_contract()

    def test_scope_remains_legacy_only(self):
        p=ufs.descriptor_policy()
        self.assertFalse(p['mcq_supported']);self.assertFalse(p['guest_descriptor_rewritten'])
        self.assertFalse(p['factory_images_modified'])


if __name__=='__main__':unittest.main()
