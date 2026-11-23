
import struct
import sys
import unittest
import bl2_mcu as mcu


class McuTests(unittest.TestCase):
    def test_original_layout_has_present_plugins_and_correct_bss(self):
        raw,meta=mcu.original_candidate()
        self.assertEqual(meta['local_context'],'0xe000')
        self.assertEqual((meta['bss_base'],meta['bss_bytes']),('0xc000',0x4000))
        self.assertEqual(sum(r['bytes'] for r in meta['plugin_spans']),len(raw))
        self.assertEqual(raw[0x1581:0x1599],b'FRM\0ARR\0S2D\0ELD\0ECD\0BCM\0')

    def test_strict_replay_stops_instead_of_inventing_watchdog(self):
        raw,_=mcu.original_candidate();result,memory,dram=mcu.execute(raw)
        self.assertEqual(result['stopped']['address'],'0xa07a000c')
        self.assertFalse(result['public_sram_usable_for_boot'])
        self.assertFalse(result['acpm_identity_verified'])
        self.assertIsNone(dram)
        self.assertEqual(len(memory),0x10000)

    def test_thumb2_isa_is_executed_on_m3_candidate(self):
        raw,_=mcu.original_candidate()
        fixture=struct.pack('<2I',0x10000,9)+raw[0x307e:0x3082]+b'\x00\xbe'
        result,_,_=mcu.execute(fixture,instructions=8)
        self.assertEqual(result['registers']['r1'],'0x400')
        self.assertIn('cortex-m3',result['cpu_model'])

    def test_actual_plugins_initialize_only_under_declared_conditions(self):
        raw,_=mcu.original_candidate()
        result,memory,dram=mcu.execute(raw,watchdog=True,system_registers=True,reference_divider=1,
            mailbox=True,chipid=True,boot_marker=True,debug_dram=True,
            read_inputs={0xa0463b3c:0,0xa0460500:0,0xa0460404:0})
        self.assertEqual(result['stopped']['address'],'0xab430024')
        self.assertEqual(result['plugin_entry_visits'],['0x1900','0x3000','0x5400','0x6400','0x9800'])
        self.assertEqual([r['init'] for r in result['private_plugin_descriptors']],['0x0','0x1901','0x3001','0x5401','0x6401','0x9801'])
        self.assertTrue(all(r['enabled'] for r in result['private_plugin_descriptors']))
        self.assertFalse(result['public_sram_usable_for_boot'])
        self.assertFalse(result['hardware_state_verified'])
        self.assertEqual(len(dram),0x100000)
        self.assertEqual(struct.unpack_from('<I',memory,0xe000)[0],0xdb9c5a1d)

    def test_systick_reload_wrap_countflag_and_clear(self):
        core=mcu.MClassSystemRegisters();core.write(0x14,4,9,0);core.write(0x10,4,5,0)
        self.assertEqual(core.read(0x18,4,1),9)
        self.assertEqual(core.read(0x18,4,9),1)
        self.assertEqual(core.read(0x18,4,10),0)
        self.assertEqual(core.read(0x10,4,10),0x10005)
        self.assertEqual(core.read(0x10,4,10),5)
        self.assertEqual(core.read(0x18,4,11),9)
        core.write(0x18,4,100,11)
        self.assertEqual(core.read(0x18,4,11),0)

    def test_systick_external_clock_and_irq_remain_explicit(self):
        core=mcu.MClassSystemRegisters()
        with self.assertRaises(ValueError):core.write(0x10,4,3,0)
        core=mcu.MClassSystemRegisters(2);core.write(0x14,4,9,0);core.write(0x10,4,3,0)
        self.assertEqual(core.read(0x18,4,18),1)
        with self.assertRaises(ValueError):core.read(0x18,4,20)
        with self.assertRaises(ValueError):core.read(0x1c,4,20)

    def test_nvic_w1_set_clear_and_delivery_not_faked(self):
        core=mcu.MClassSystemRegisters();core.write(0x100,4,3,0)
        self.assertEqual(core.read(0x180,4,0),3)
        core.write(0x180,4,1,0);self.assertEqual(core.read(0x100,4,0),2)
        core.write(0x400,1,255,0);self.assertEqual(core.read(0x400,1,0),0xe0)
        with self.assertRaises(ValueError):core.write(0x200,4,2,0)

    def test_nvic_second_word_is_stateful_when_explicitly_modelled(self):
        core=mcu.MClassSystemRegisters(external_lines=64)
        core.write(0x104,4,0x20000,0)
        self.assertEqual(core.read(0x104,4,0),0x20000)
        self.assertEqual(core.read(0x100,4,0),0)
        core.write(0x431,1,0xff,0);self.assertEqual(core.read(0x431,1,0),0xe0)
        with self.assertRaises(ValueError):core.write(0x204,4,0x20000,0)
        core.write(0x284,4,0x20000,0);core.write(0x184,4,0x20000,0)
        self.assertEqual(core.read(0x104,4,0),0)
        with self.assertRaises(ValueError):mcu.MClassSystemRegisters(external_lines=33)

    def test_mailbox_no_echo_pending_masks_and_w1c(self):
        mailbox=mcu.MailboxRegisters();mailbox.write(8,4,0x10000)
        self.assertEqual(mailbox.read(0x14,4),0x10000)
        self.assertEqual(mailbox.read(0x28,4),0)
        mailbox.write(0x10,4,0);self.assertEqual(mailbox.read(0x18,4),0x10000)
        mailbox.write(0xc,4,0x10000);self.assertEqual(mailbox.read(0x14,4),0)
        mailbox.write(0x80,4,0x1234);self.assertEqual(mailbox.read(0x80,4),0x1234)
        with self.assertRaises(ValueError):mailbox.read(0x34,4)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(McuTests))
    sys.exit(0 if result.wasSuccessful() else 1)
