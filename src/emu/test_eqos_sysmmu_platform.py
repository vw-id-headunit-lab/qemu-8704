
import unittest

import eqos_platform as eqos
import sysmmu_platform as mmu


class NativeDeviceContracts(unittest.TestCase):
    def test_linux_sysmmu_only_original_ports_and_roots(self):
        self.assertIsNone(mmu.linux_policy(None,False))
        with self.assertRaises(ValueError):
            mmu.linux_policy(mmu.LINUX_PROFILE,False)
        p=mmu.linux_policy(mmu.LINUX_PROFILE,True)
        self.assertEqual(len(p['addresses']),19)
        self.assertEqual(p['addresses'][0x18c80000]['vid'],3)
        self.assertEqual(p['addresses'][0x19e00000]['vid'],1)
        self.assertEqual([x for x,y in p['addresses'].items() if y['early_enable']],[0x19e00000])
        self.assertFalse(p['translation'] or p['irq'])
        self.assertEqual(p['mfc_resume']['ports'],(0x19880000,0x198b0000))
        self.assertEqual(p['mfc_resume']['index_words'],{0x2024:0x20,0x2028:0x10300,0x2034:0x10001})
        self.assertFalse(p['mfc_resume']['dma'])
        display=p['display_resume']
        self.assertEqual((display['port'],display['vid'],display['qos_word']),(0x1aaa0000,3,0xf80))
        self.assertEqual(display['default_tlb'],0x0a)
        self.assertEqual(display['indexed_tlbs'][0],(1,0x0a,0x10100,0x70001))
        self.assertEqual(display['indexed_tlbs'][-1],(4,0x0a,0x10100,0x70004))
        self.assertEqual(display['virtual_non_vcr_capa1']>>4,1+len(display['indexed_tlbs']))
        self.assertFalse(display['dma'] or display['irq_source'])
        g0=p['display_g0_resume']
        self.assertEqual((g0['port'],g0['vid'],g0['qos_word'],g0['virtual_non_vcr_capa1']),
                         (0x18c80000,3,0xf80,0x40))
        self.assertEqual(g0['dpp'],('/dpp@0x18C21000','/dpp@0x18C22000'))
        self.assertEqual(g0['indexed_tlbs'],((1,0x0a,0x10100,0x0c070004),
                                            (2,0x0a,0x10100,0x0c000400),
                                            (3,0x0a,0x10100,0x0c000800)))
        self.assertFalse(g0['dma'] or g0['irq_source'])

    def test_eqos_default_and_address_selection(self):
        self.assertIsNone(eqos.policy(None, True))
        for mac in ('00:87:04:00:00:01','03:87:04:00:00:01','invalid'):
            with self.assertRaises(ValueError):
                eqos.policy(mac, True)
        with self.assertRaises(ValueError):
            eqos.policy('02:87:04:00:00:01', False)

    def test_eqos_only_granted_channel_registers(self):
        p=eqos.policy('02:87:04:00:00:01', True)
        self.assertEqual(p['mac_le'],0x010000048702)
        self.assertIn(eqos.CHANNEL_BASE+0x4034,p['words'])
        self.assertNotIn(eqos.CHANNEL_BASE+0x5034,p['words'])
        self.assertIn(eqos.CHANNEL_BASE+0x5034,p['vm3_channel_words'])
        self.assertIn(eqos.CHANNEL_BASE+0x5064,p['vm3_channel_words'])
        self.assertIn(eqos.CHANNEL_BASE+0x4064,p['words'])
        self.assertNotIn(eqos.CHANNEL_BASE+0x4068,p['words'])
        self.assertEqual(p['rx_missed_frames']['channels'],list(range(8)))
        self.assertTrue(p['rx_missed_frames']['read_only'])
        self.assertEqual(p['channel_grants'], {'vm2':[0,1,2,3,4], 'vm3':[5,6,7], 'vm4':[]})
        self.assertEqual(p['linux_fsys2']['mask'],'0x11')
        self.assertFalse(p['descriptor_dma'])
        presentation=p['ptp']['presentation_update']
        self.assertEqual(presentation['address'],'0x17d80244')
        self.assertEqual(presentation['original_store_pc'],'0xffffffc0105ec0a4')
        self.assertEqual(presentation['vmid'],3)
        self.assertNotIn(eqos.PRESENTATION_UPDATE,p['words'])
        self.assertFalse(presentation['emits_packet_or_dma'])

    def test_sysmmu_only_original_active_android_ports(self):
        self.assertIsNone(mmu.policy(None, True))
        with self.assertRaises(ValueError):
            mmu.policy(mmu.PROFILE, False)
        p=mmu.policy(mmu.PROFILE, True)
        self.assertEqual(tuple(sorted(p['addresses'])),mmu.PORTS)
        self.assertEqual(p['registers'],[0x34,0x874,0x400c,0x4010,0x60,0x64])
        self.assertFalse(p['translation'])


if __name__=='__main__':
    unittest.main()

