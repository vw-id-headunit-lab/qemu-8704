
import unittest

import pcie2_platform as p
import mali_g3d1_platform as mali
import linux_uart_platform as uart
import g2d_reset_platform as g2d


class Pcie2ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.elbi = p.policy(p.PROFILE, True)
        cls.phy = p.phy_policy(p.PHY_PROFILE, cls.elbi, True)
        cls.pcs = p.pcs_policy(p.PCS_PROFILE, cls.elbi, cls.phy)

    def test_pcs_mandatory_original_windows_and_status(self):
        self.assertEqual(self.pcs['areas']['pcs'], (0x17ad0000,0xcb0))
        words = {r['address']:r for r in self.pcs['words']}
        self.assertEqual(len(words),121)
        for address in (0x17ad0988,0x17ad0188,0x17b10d24,0x17b11524):
            self.assertEqual(words[address],dict(address=address,read=True,writes=[]))
        self.assertEqual(words[0x17a80c00],dict(address=0x17a80c00,read=True,writes=[0,3]))
        self.assertEqual(words[0x17a80c04],dict(address=0x17a80c04,read=False,writes=[0x200000]))
        self.assertEqual(words[0x17a80c08],dict(address=0x17a80c08,read=False,writes=[0xffffffff]))
        self.assertEqual(words[0x17a80054],dict(address=0x17a80054,read=False,writes=[1,0]))
        self.assertEqual(words[0x17a802c8],dict(address=0x17a802c8,read=True,writes=[]))
        self.assertEqual(words[0x17a802e8],dict(address=0x17a802e8,read=True,writes=[]))
        self.assertEqual(words[0x17a803a0],dict(address=0x17a803a0,read=False,writes=[2]))
        self.assertEqual(words[0x17a803a4],dict(address=0x17a803a4,read=True,writes=[0,2]))
        self.assertEqual(words[0x17a803a8],dict(address=0x17a803a8,read=True,writes=[0]))
        self.assertEqual(words[0x17a803bc],dict(address=0x17a803bc,read=True,writes=[0x11]))
        self.assertIn(p.PCS_LATE_ELBI,self.pcs['original_config_and_reset_code'])
        self.assertIn(p.PCS_ELBI_READL,self.pcs['original_config_and_reset_code'])
        self.assertIn(p.PCS_ELBI_3BC,self.pcs['original_config_and_reset_code'])
        self.assertIn(p.PCS_ELBI_2E8,self.pcs['original_config_and_reset_code'])
        self.assertIn(p.PCS_ELBI_54_CLR,self.pcs['original_config_and_reset_code'])
        self.assertIn(p.PCS_ELBI_C88_DUMP,self.pcs['original_config_and_reset_code'])
        self.assertEqual(words[0x17a80c88],dict(address=0x17a80c88,read=True,writes=[]))
        self.assertEqual(words[0x17a80c0c],dict(address=0x17a80c0c,read=True,writes=[]))
        self.assertEqual(words[0x17a803d4]['writes'],[0])
        self.assertEqual(words[0x17b10400]['writes'],[0,3])
        self.assertFalse(any(self.pcs[k] for k in ('link','pll','irq','endpoint','dma')))

    def test_root_dbi_does_not_preseed_identity_or_expose_endpoint(self):
        dbi=p.dbi_policy(p.DBI_PROFILE,self.pcs)
        self.assertEqual(dbi['areas'],{'dbi':(0x13000000,0x5000),'dbi_shadow':(0x13200000,0x1000)})
        self.assertEqual(dbi['ro_write_enable'],0x8bc)
        self.assertFalse(any(dbi[k] for k in ('host_preseeded_id','link','downstream_enumeration','msi_delivery','dma')))

    def test_unrolled_atu_binds_granted_windows_and_has_no_endpoint(self):
        dbi=p.dbi_policy(p.DBI_PROFILE,self.pcs)
        atu=p.atu_policy(p.ATU_PROFILE,dbi)
        self.assertEqual(atu['windows'],{'atu':(0x13600000,0x1000),'config':(0x5fffd000,0x2000)})
        self.assertEqual(atu['outbound_memory'],(0x40000000,0x1fffd000))
        self.assertEqual(atu['pci_aperture_hcb'],(0x40000000,0x20000000))
        self.assertEqual((atu['discovery'],atu['root_header_type'],atu['banks']),(0xffffffff,1,8))
        self.assertEqual(atu['secondary_devices'],[])
        self.assertFalse(any(atu[k] for k in ('host_preseeded_id','link','inbound_dma','msi_delivery')))
        self.assertIsNone(p.atu_policy(None,None))
        with self.assertRaises(ValueError):
            p.atu_policy(p.ATU_PROFILE,None)

    def test_mali_discovery_is_opt_in_and_bound_to_original_code(self):
        self.assertIsNone(mali.policy(None,False))
        with self.assertRaises(ValueError):
            mali.policy(None,True,mali.DISCOVERY_PROFILE)
        with self.assertRaises(ValueError):
            mali.policy(0x72110000,False,mali.DISCOVERY_PROFILE)
        with self.assertRaises(ValueError):
            mali.verify_discovery(mali.DISCOVERY_PROFILE,b'not the original kernel')
        selected=mali.policy(0x72110000,True,mali.DISCOVERY_PROFILE)
        self.assertEqual(selected['advertised_gpu_resources'],[])
        self.assertFalse(any(selected[k] for k in ('physical_gpu_revision_verified','gpu_jobs','gpu_mmu','irq','scanout')))
        self.assertTrue(mali.policy(0x72110000,True)['virtual_identity_only'])
        with self.assertRaises(ValueError):
            mali.policy(0x72110000,True,reset_irq=mali.RESET_IRQ_PROFILE)
        reset=mali.policy(0x72110000,True,mali.DISCOVERY_PROFILE,mali.RESET_IRQ_PROFILE)
        self.assertEqual(reset['irq_spis'],{'gpu':255,'job':256,'mmu':257})
        self.assertFalse(reset['gpu_jobs'])
        with self.assertRaises(ValueError):
            mali.policy(0x72110000,True,mali.DISCOVERY_PROFILE,config=mali.CONFIG_PROFILE)
        cfg=mali.policy(0x72110000,True,mali.DISCOVERY_PROFILE,mali.RESET_IRQ_PROFILE,mali.CONFIG_PROFILE)
        self.assertEqual(cfg['config_offsets'],[0x304,0xf00,0xf04,0xf08,0xf0c])
        self.assertFalse(cfg['gpu_jobs'])
        with self.assertRaises(ValueError):
            mali.policy(0x72110000,True,mali.DISCOVERY_PROFILE,mali.RESET_IRQ_PROFILE,
                        empty_power=mali.EMPTY_POWER_PROFILE)
        power=mali.policy(0x72110000,True,mali.DISCOVERY_PROFILE,mali.RESET_IRQ_PROFILE,
                          mali.CONFIG_PROFILE,mali.EMPTY_POWER_PROFILE)
        self.assertEqual(power['present_core_mask'],0)

    def test_mali_vm3_control_requires_exclusive_native_backing(self):
        image=mali.factory_file('ivi/Image')
        topo=mali.jm_topology_policy(mali.JM_TOPOLOGY_PROFILE,image,mali.EMPTY_POWER_PROFILE)
        self.assertEqual(topo['backed_address_spaces'],0)
        self.assertFalse(topo['vm3_control'])
        with self.assertRaisesRegex(ValueError,'complete native board'):
            mali.jm_topology_policy(mali.JM_VM3_CONTROL_PROFILE,image,mali.EMPTY_POWER_PROFILE)
        vm3=mali.jm_topology_policy(mali.JM_VM3_CONTROL_PROFILE,image,mali.EMPTY_POWER_PROFILE,
                                     vm3_native=True)
        self.assertEqual(vm3['dma_private_banks'],mali.VM3_CONTROL_BANKS)
        self.assertEqual(vm3['backed_address_spaces'],1)
        self.assertEqual(vm3['supported_job_types'],[1,2,3,5,9])
        self.assertIn('exact shader digest',vm3['vertex_scope'])
        self.assertIn('no VM3 state access',vm3['vm4_irq_scope'])
        self.assertEqual(vm3['shader_present'],0)
        self.assertFalse(vm3['scanout'])

    def test_linux_usi_uart_keeps_original_port_boundaries(self):
        self.assertIsNone(uart.policy(None,False))
        with self.assertRaises(ValueError):
            uart.policy(uart.PROFILE,False)
        p=uart.policy(uart.PROFILE,True)
        self.assertEqual([(x['selector'],x['spi'],x['fifo']) for x in p['ports']],
                         [(0x10221018,351,64),(0x10221020,353,64),(0x10821010,362,64)])
        self.assertEqual([row['address'] for row in p['uintm_wide']],
                         [0x10360038,0x10380038,0x10940038])

    def test_g2d_is_opt_in_and_excludes_jobs(self):
        with self.assertRaises(ValueError):
            g2d.policy(g2d.PROFILE,False)
        with self.assertRaises(ValueError):
            g2d.verify_kernel(b'changed')
        p=g2d.policy(g2d.PROFILE,True)
        self.assertEqual((p['base'],p['size']),(0x19a20000,0xa000))
        self.assertFalse(any(p[k] for k in ('dma','jobs','irq','physical_version_verified')))

    def test_profiles_are_opt_in_and_cannot_be_used_out_of_order(self):
        self.assertIsNone(p.pcs_policy(None,None,None))
        self.assertIsNone(p.dbi_policy(None,None))
        with self.assertRaises(ValueError):p.pcs_policy(p.PCS_PROFILE,self.elbi,None)
        with self.assertRaises(ValueError):p.dbi_policy(p.DBI_PROFILE,None)
        with self.assertRaises(ValueError):p.dbi_policy('unknown',self.pcs)


if __name__=='__main__':
    unittest.main()

