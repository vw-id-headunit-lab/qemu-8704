
import unittest

import linux_mcu_identity as mcu
from vlm_platform import factory_file


class LinuxMcuIdentityTests(unittest.TestCase):
    def test_loader_reconstructs_exact_record_and_keeps_original_variant(self):
        selected=mcu.contract()
        args=mcu.loader_options(selected)
        self.assertEqual(args[::2],['-device']*4)
        data=bytearray(b'\0')
        for i,entry in enumerate(args[1::2]):
            model,addr,value,width=entry.split(',')
            self.assertEqual((model,addr,width),('loader',f'addr={mcu.PAGE+1+8*i:#x}','data-len=8'))
            data.extend(int(value.split('=')[1],16).to_bytes(8,'little'))
        self.assertEqual(data[1:].hex(),selected['data_hex'])
        with factory_file('ivi/Image').open('rb') as image:
            image.seek(mcu.FALLBACK[0]-mcu.KERNEL_BASE)
            original=image.read(mcu.FALLBACK[1])
        self.assertEqual([(offset,old,new) for offset,(old,new) in enumerate(zip(original,data[1:])) if old!=new],
                         [(2,0,4),(4,0,4)])
        self.assertEqual((selected['platform'],selected['market'],selected['variant']),(4,4,0x65))
        self.assertEqual(mcu.guest_identity(int.from_bytes(data[:8],'little')),
                         dict(platform=4,market=4,variant=0x65))


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(LinuxMcuIdentityTests)
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
