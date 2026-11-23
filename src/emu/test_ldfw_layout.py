
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from Crypto.Cipher import AES
import ldfw_layout as ldfw


def fixture(name='test_fw',size=0x2000,encoded=1):
    raw=bytearray(size)
    words=(0x10adab1e,size,0x1100,0x1100,0,0,0x82001000,1,0,0,encoded,0)
    struct.pack_into('<12I',raw,0,*words);raw[48:64]=name.encode().ljust(16,b'\0')
    struct.pack_into('<2I',raw,0x44,0x1000,0x1200)
    return raw


class LdfwTests(unittest.TestCase):
    def test_original_five_containers_match_exact_source_bytes(self):
        raw=ldfw.platform.original('boot-binary/ldfw.bin').read_bytes();report=ldfw.parse_ldfw(raw)
        self.assertEqual([r['name'] for r in report['entries']],['CryptoManagerV50','fmp_fw_V20','drm_fw','srpmb_fw','tail_fw'])
        self.assertEqual(report['consumed_bytes'],len(raw))
        self.assertEqual(report['trailing_padding_bytes'],0)
        for row in report['entries']:
            chunk=raw[row['offset']:row['offset']+row['size']]
            self.assertEqual(row['sha256'],hashlib.sha256(chunk).hexdigest())
        self.assertEqual([r['encoding_flag'] for r in report['entries']],[1,1,1,1,0])

    def test_truncation_wrong_magic_and_zero_size_rejected(self):
        raw=fixture();zero=bytearray(raw);struct.pack_into('<I',zero,4,0)
        for value in (b'',b'bad',b'x'*64,raw[:-1],zero):
            with self.subTest(length=len(value)):
                with self.assertRaises(ValueError):ldfw.parse_ldfw(value)

    def test_tail_entries_can_point_past_header_only_marker(self):
        raw=fixture('tail_fw',0x1000,0)
        struct.pack_into('<2I',raw,8,0x1000,0x1070)
        report=ldfw.parse_ldfw(raw+b'\xff'*4096)
        self.assertFalse(report['entries'][0]['entries_within_container'])
        self.assertEqual(report['trailing_padding_bytes'],4096)
        with self.assertRaises(ValueError):ldfw.parse_ldfw(raw+b'x'*4096)

    def test_public_parameters_are_read_without_executing_source(self):
        key=bytes(range(32));iv=bytes(range(16))
        text=f"MASTER_KEY=bytes.fromhex('{key.hex()}')\nMASTER_IV=bytes.fromhex('{iv.hex()}')\nraise RuntimeError('must not execute')\n"
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'reference.py';path.write_text(text,encoding='utf-8')
            self.assertEqual(ldfw.public_source_parameters(path),(key,iv))
            path.write_text(text+"MASTER_IV=bytes.fromhex('00')\n",encoding='utf-8')
            with self.assertRaises(ValueError):ldfw.public_source_parameters(path)
            path.write_text("MASTER_KEY=open('unknown').read()\n",encoding='utf-8')
            with self.assertRaises(ValueError):ldfw.public_source_parameters(path)

    def test_candidate_roundtrip_does_not_claim_production_decryption(self):
        raw=fixture();seed=bytes(range(32));seed_iv=bytes(range(16))
        key=seed[::-1];iv=b'\x35'*16;plain=b'original-fixture'*32
        self.assertEqual(len(plain),512)
        raw[0x4c:0x6c]=AES.new(seed,AES.MODE_CBC,seed_iv).encrypt(key)
        raw[0xec:0xfc]=bytes(a^b for a,b in zip(seed_iv,iv))
        raw[0x1000:0x1200]=AES.new(key,AES.MODE_CBC,iv).encrypt(plain)
        row=ldfw.parse_ldfw(raw)['entries'][0]
        candidate,observations=ldfw.candidate_decode(bytes(raw),row,seed,seed_iv)
        self.assertEqual(candidate[0x1000:0x1200],plain)
        self.assertEqual(candidate[:0x1000],raw[:0x1000])
        self.assertEqual(candidate[0x1200:],raw[0x1200:])
        self.assertFalse(observations['decryption_verified'])
        self.assertFalse(observations['firmware_executed'])
        self.assertFalse(observations['candidate_usable_for_boot'])
        row['crypto_region_bounds_valid']=False
        with self.assertRaises(ValueError):ldfw.candidate_decode(bytes(raw),row,seed,seed_iv)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LdfwTests))
    sys.exit(0 if result.wasSuccessful() else 1)
