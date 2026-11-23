
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = (HERE / 'exynos-auto9-sss.inc.c').read_text(encoding='utf-8')


class Auto9SssSourceTests(unittest.TestCase):
    def test_original_epbl_addresses_and_wait_bits(self):
        self.assertIn('0x1bf50000', SOURCE)
        self.assertIn('0x1fc00', SOURCE)
        self.assertIn('word == 960', SOURCE)
        self.assertIn('0x1000 / 4] == 25', SOURCE)
        self.assertIn('auto9_sss_complete(s, 2)', SOURCE)
        self.assertIn('auto9_sss_complete(s, 4)', SOURCE)
        self.assertIn('s->words[0x1010 / 4] |= 0x40', SOURCE)

    def test_no_forged_payload_or_digest(self):
        self.assertIn('Digest words stay reset-zero', SOURCE)
        self.assertNotIn('qcrypto', SOURCE)
        self.assertNotIn('SHA384', SOURCE)
        self.assertNotIn('AES', SOURCE)


if __name__ == '__main__':
    unittest.main()
