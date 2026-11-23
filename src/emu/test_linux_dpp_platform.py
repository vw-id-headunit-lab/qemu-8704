
from pathlib import Path
import re
import unittest

import dpp_platform as dpp


class LinuxDppContractTests(unittest.TestCase):
    def test_qnx_control_words_are_bound_to_original_enable_reset(self):
        policy=dpp.qnx_policy(True)
        self.assertEqual(policy['profile'],'virtual-qnx-dpp-irq-control-v3')
        self.assertEqual(len(policy['words']),12)
        self.assertEqual(len(policy['original_code']),7)
        self.assertEqual(policy['common_offsets'],(0x70,0x74))
        self.assertFalse(policy['dma'] or policy['scanout'] or policy['irq_assertion'])
        model=Path(__file__).with_name('exynos-auto9-dpp.inc.c').read_text(encoding='utf-8')
        block=re.search(r'auto9_qnx_dpp_config\[\]\s*=\s*\{(.*?)\};',model,re.S).group(1)
        pairs=[(int(kind),int(off,16)) for kind,off in re.findall(r'\{\s*([01]),\s*(0x[0-9a-f]+)\s*\}',block)]
        for kind, offsets in enumerate(dpp.QNX_CONFIG_OFFSETS):
            self.assertEqual({off for k,off in pairs if k==kind},set(offsets))
        self.assertIn('if (!vms->exynos_linux_dpp_irq)',model)

    def test_dpp_enable_and_source_tables(self):
        policy=dpp.linux_policy(dpp.LINUX_PROFILE,True)
        self.assertEqual(len(policy['config']),8)
        self.assertEqual(len(policy['words']),6)
        self.assertEqual(policy['config'][0x1aa63000]['kind'],'idma')
        self.assertIn(0,policy['config'][0x1aa63000]['offsets'])
        self.assertFalse(policy['dma'] or policy['scanout'] or policy['irq_assertion'])

        model=Path(__file__).with_name('exynos-auto9-dpp.inc.c').read_text(encoding='utf-8')
        self.assertRegex(model, r'memory_region_add_subregion_overlap\([^;]+,\s*1\)')
        for name,offsets in dpp.LINUX_CONFIG_OFFSETS.items():
            found=re.search(r'auto9_linux_'+name+r'_offsets\[\]\s*=\s*\{([^}]+)\}',model)
            self.assertIsNotNone(found)
            self.assertEqual(tuple(int(x,16) for x in re.findall(r'0x[0-9a-f]+',found[1])),offsets)


if __name__=='__main__':
    from types import SimpleNamespace
                                                                                
    unittest.main(module=SimpleNamespace(LinuxDppContractTests=LinuxDppContractTests))
