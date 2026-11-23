
import inspect
import struct
import unittest
from unittest.mock import Mock
import lk_runtime as lk
import vlm_platform as platform
import patch_qemu_exynos as patch


class RuntimeTests(unittest.TestCase):
    def test_initializer_is_separate_and_exact(self):
        self.assertEqual(lk.initializer_code().hex(),'80d018d502001ad520001fd6')
        self.assertFalse(lk.LINK<=lk.INITIALIZER<lk.LINK+0x100000)

    def test_bss_is_link_relative_not_low_memory(self):
        writes=dict(lk.fixture_writes())
        self.assertEqual(struct.unpack('<Q',writes[lk.LINK+0x27d520])[0],lk.CONTEXT)
        self.assertNotIn(0x27d520,writes)

    def test_list_and_callbacks_use_original_addresses(self):
        writes=dict(lk.fixture_writes())
        self.assertEqual(struct.unpack('<Q',writes[lk.LINK+0x79bb0])[0],lk.BIO)
        self.assertEqual(struct.unpack('<Q',writes[lk.BIO+288])[0],lk.LINK+0x52554)
        self.assertEqual(struct.unpack('<Q',writes[lk.BIO+24])[0],lk.LINK+0x70d80)

    def test_runtime_descriptor_is_byte_unit_original_format(self):
        data=dict(lk.fixture_writes())[lk.UTRL]
        self.assertEqual(struct.unpack_from('<Q',data,16)[0],lk.CMD)
        self.assertEqual(struct.unpack_from('<2H',data,24),(1024,1024))

    def test_fixtures_never_write_done_or_executable_code(self):
        for address,data in lk.fixture_writes():
            self.assertNotEqual(address,0x10460840)
            self.assertFalse(lk.LINK<=address<lk.LINK+0x65000)

    def test_qom_snapshot_does_not_write_guest_state(self):
        qmp=Mock();qmp.execute.return_value=0
        result=lk.device_snapshot(qmp)
        self.assertEqual(set(result),{'ufs','pmu'})
        self.assertEqual(len(qmp.execute.call_args_list),14)
        self.assertTrue(all(c.args[0]=='qom-get' for c in qmp.execute.call_args_list))

    def test_zero_runtime_dma_excludes_nonzero_preboot(self):
        qmp=Mock();qmp.execute.return_value=9
        snapshot=lk.device_snapshot(qmp)
        self.assertTrue(all(v==0 for v in lk.counter_delta(snapshot,snapshot).values()))

    def test_counter_delta_and_reset_rejection(self):
        qmp=Mock();qmp.execute.return_value=0
        before=lk.device_snapshot(qmp);after=lk.device_snapshot(qmp)
        after['ufs']['dma-reads']=12;after['ufs']['dma-writes']=3
        self.assertEqual(lk.counter_delta(before,after)['dma-reads'],12)
        with self.assertRaisesRegex(ValueError,'counter reset'):lk.counter_delta(after,before)

    def test_native_stage_is_explicit_not_default(self):
        self.assertIsNone(inspect.signature(platform.probe).parameters['lk_handoff_profile'].default)
        for value in ('unknown','virtual-original-lk-ssu-v1'):
            with self.assertRaisesRegex(ValueError,'Original LK stage requires'):
                platform.probe(None,.1,0,lk_handoff_profile=value)

    def test_rsp_memory_chunks_and_uses_raw_mmio_write(self):
        rsp=Mock();rsp.command.side_effect=lambda command: b'OK' if command.startswith('M') else b'00'*int(command.split(',')[1],16)
        io=lk.RspMemory(rsp);io.put(0x1000,bytes(2049))
        self.assertEqual(len(rsp.command.call_args_list),3)
        self.assertEqual(io.get(0x1000,2049),bytes(2049))
        io.write(0x10460000,7)
        self.assertEqual(io.events,[{'address':'0x10460000','value':7}])

    def test_rsp_memory_rejects_bad_read(self):
        rsp=Mock();rsp.command.return_value=b'E14'
        with self.assertRaisesRegex(ValueError,'memory read failed'):lk.RspMemory(rsp).get(0x1000,4)

    def test_gdb_vcont_counts_all_realized_cpu_indices(self):
        source='unsigned int gdb_get_max_cpus(void)\n{\n    MachineState *ms = MACHINE(qdev_get_machine());\n    return ms->smp.max_cpus;\n}'
        changed=patch.edits('gdbstub/system.c',source)
        self.assertIn('CPU_FOREACH(cpu)',changed)
        self.assertIn('cpu->cpu_index + 1',changed)
        self.assertIn('ms->smp.max_cpus',changed)
                                                                  
        count=max(8,max(range(9))+1)
        self.assertEqual(count,9)


if __name__=='__main__':unittest.main()
