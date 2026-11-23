
from pathlib import Path
import struct
import sys
import unittest
import ufs_platform as ufs
import ufs_mailbox_platform as mb


class UfsContracts(unittest.TestCase):
    def test_polled_mailbox_binds_original_owner_without_vh_dma(self):
        self.assertIsNone(mb.policy(None,None,False))
        with self.assertRaises(ValueError):
            mb.policy(mb.PROFILE,None,True)
        host0=ufs.host0_policy(ufs.HOST0_PROFILE,True)
        with self.assertRaises(ValueError):
            mb.policy(mb.PROFILE,host0,False)
        c=mb.policy(mb.PROFILE,host0,True)
        self.assertEqual([h['vmid'] for h in c['hosts']],[2,3,4])
        self.assertFalse(c['vh_dma'] or c['full_hci'] or c['vh_irq'])
        with self.assertRaises(ValueError):
            mb.verify_android(b'changed')

    def test_descriptor_native_packed_layout(self):
        raw = ufs.utrd()
        self.assertEqual(len(raw), 32)
        self.assertEqual(struct.unpack('<6I4H', raw),
                         (0x11000000, 0, 15, 0, ufs.COMMAND, 0, 288, 256, 0, 512))

    def test_descriptor_direction_and_64bit_address(self):
        words = struct.unpack('<6I4H', ufs.utrd(0x12300002000, 0x04000000, 1))
        self.assertEqual(words[0], 0x15000000)
        self.assertEqual(words[4:6], (0x300002000 & 0xffffffff, 0x123))
        self.assertEqual(words[-2], 1)

    def test_invalid_descriptor_arguments(self):
        for arguments in ((3, 0, 0), (ufs.COMMAND, 1, 0), (ufs.COMMAND, 0, -1), (ufs.COMMAND, 0, 33)):
            with self.assertRaises(ValueError):
                ufs.utrd(*arguments)

    def test_read_write10_wire_endianness(self):
        for opcode, flag in ((0x28, 0x40), (0x2a, 0x20)):
            raw = ufs.scsi_request(opcode, 0x12345678, 0x123)
            self.assertEqual(len(raw), 288)
            self.assertEqual(raw[:2], bytes((1, flag)))
            self.assertEqual(raw[12:16], (0x123*4096).to_bytes(4, 'big'))
            self.assertEqual(raw[16:26], bytes.fromhex(f'{opcode:02x}001234567800012300'))

    def test_invalid_scsi_arguments(self):
        for arguments in ((0, 0), (0x28, -1), (0x28, 1 << 32), (0x2a, 0, 0), (0x28, 0, 65536)):
            with self.assertRaises(ValueError):
                ufs.scsi_request(*arguments)

    def test_original_route_is_not_usb_fsys1(self):
        route = ufs.route_contract()
        self.assertEqual(route['vgen']['base'], 0x17dd0000)
        self.assertEqual(route['s2mpu']['base'], 0x17ca0000)
        self.assertEqual(route['spi'], 227)
        self.assertEqual(route['vgen']['layout'], 2)
        self.assertEqual(route['vgen']['factory_vid'], [2, 3, 4])

    def test_shareability_binds_original_hcb_word(self):
        policy = ufs.shareability_policy(ufs.SHAREABILITY_PROFILE, True)
        self.assertEqual((policy['address'],policy['width'],policy['mask']), ('0x17c20710',4,6))
        self.assertEqual(policy['hcb_sha256'], ufs.route_contract()['hcb_sha256'])
        self.assertFalse(policy['dma_policy_changed'])
        self.assertFalse(policy['ready_injected'])

    def test_shareability_requires_explicit_native_profile(self):
        self.assertIsNone(ufs.shareability_policy(None, True))
        with self.assertRaisesRegex(ValueError, 'native Auto9 UFS'):
            ufs.shareability_policy(ufs.SHAREABILITY_PROFILE, False)
        with self.assertRaisesRegex(ValueError, 'native Auto9 UFS'):
            ufs.shareability_policy('unknown', True)

    def test_phy_isolation_source_and_limits(self):
        self.assertIsNone(ufs.phy_isolation_policy(None,True))
        with self.assertRaisesRegex(ValueError,'native UFS and PMU'):
            ufs.phy_isolation_policy(ufs.PHY_ISOLATION_PROFILE,False)
        policy=ufs.phy_isolation_policy(ufs.PHY_ISOLATION_PROFILE,True)
        self.assertEqual((policy['address'],policy['width'],policy['mask']),('0x10460728',4,1))
        self.assertFalse(policy['phy_link_implemented'])
        self.assertFalse(policy['ready_injected'])

    def test_sg_uses_iommu_and_no_failed_read_bounce(self):
        text = Path(__file__).with_name('exynos-auto9-ufs.inc.c').read_text(encoding='utf-8')
        self.assertIn('exynos_auto9_s2mpu_translate(', text)
        self.assertIn('memory_region_init_iommu(', text)
        self.assertIn('"x-max-bounce-buffer-size", 0', text)
        self.assertIn('address_space_notify_map_clients(&s->engine->bus_master_as)', text)
        self.assertNotIn('memory_region_init_io(&s->filtered_dma', text)
        self.assertIn('vid < 2 || vid > 4', text)
        self.assertIn('s->vgen->vid[i] != vid', text)

    def test_unknown_vendor_space_not_mapped_as_ram(self):
        text = Path(__file__).with_name('exynos-auto9-ufs.inc.c').read_text(encoding='utf-8')
        self.assertIn('"auto9-ufs0-standard-hci", 0x100)', text)
        self.assertIn('"auto9-ufs0-vendor", 0x310)', text)
        self.assertIn('s->vendor_rejected++',text)
        self.assertNotIn('memory_region_init_ram', text)
        self.assertNotIn('0x17dc0000', text)
        gated = text.split('if (!s->host_index && vms->exynos_ufs_prelink) {',1)[1].split('if (s->mailbox)',1)[0]
        self.assertIn('0x17e80000', gated)
        self.assertIn('0x17e04000', gated)
        self.assertIn('auto9-ufs0-unipro-config", 0x8000', gated)
        self.assertIn('auto9-ufs0-pma-config", 0xc00', gated)
        self.assertIn('!(power || exynos_auto9_ufs_pma_supported(off, value, true))',text)
        self.assertIn('!s->power_config_enabled || s->power_timer_mask != 0x3f',text)
        self.assertIn('off >= sizeof(s->pma_words)',text)
        self.assertIn('return MEMTX_DECODE_ERROR;',text)

    def test_vendor_reset_runs_real_backend_cancellation(self):
        text=Path(__file__).with_name('exynos-auto9-ufs.inc.c').read_text(encoding='utf-8')
        self.assertIn('exynos_auto9_ufs_reset_engine(s);',text)
        self.assertIn('qemu_bh_schedule(s->reset_bh)',text)
        self.assertIn('scsi_device_purge_requests(',text)
        self.assertIn('s->vendor[0x50 / 4] || !s->vendor[0x70 / 4]',text)
        self.assertNotIn('->flags =',text)

    def test_replay_rejects_unknown_phase(self):
        with self.assertRaisesRegex(ValueError,'phase 0/1'):
            ufs.replay_original_power(None,2)

    def test_native_option_requires_board_adapter(self):
        with self.assertRaisesRegex(ValueError, 'requires original board adapter'):
            ufs.platform.probe(Path('unused'), 1, 0, native_ufs=True)

    def test_map_wakeup_export_changes_no_transfer_logic(self):
        import patch_qemu_exynos as patch
        original = 'static void address_space_notify_map_clients(AddressSpace *as)\n{\n    wake();\n}\n'
        result = patch.edits('system/physmem.c', original)
        self.assertEqual(result, original.removeprefix('static '))


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UfsContracts))
    sys.exit(0 if result.wasSuccessful() else 1)
