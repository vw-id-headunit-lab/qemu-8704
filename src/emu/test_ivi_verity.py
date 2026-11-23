
import hashlib
from pathlib import Path
import tempfile
import unittest

from ivi_verity import BLOCK, FANOUT, verify


class VerityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'data'
        self.hash=Path(self.temp.name)/'hash'
        self.salt=bytes(range(32))
        self.blocks=FANOUT+1
        chunks=[bytes([i%256])*BLOCK for i in range(self.blocks)]
        self.data.write_bytes(b''.join(chunks))
        hashes=[self.digest(x) for x in chunks]
        leaves=[b''.join(hashes[:FANOUT]),hashes[-1].ljust(BLOCK,b'\0')]
        top=b''.join(self.digest(x) for x in leaves).ljust(BLOCK,b'\0')
        self.root=self.digest(top)
        self.hash.write_bytes(bytes(BLOCK)+top+b''.join(leaves))

    def digest(self, block):return hashlib.sha256(self.salt+block).digest()

    def run_verify(self):return verify(self.data,self.hash,self.blocks,1,self.root,self.salt)

    def test_full_two_leaf_tree(self):
        result=self.run_verify()
        self.assertEqual(result['verified_data_blocks'],129)
        self.assertEqual(result['hash_offsets_from_leaves'],[2,1])

    def test_data_tamper_detected_at_last_block(self):
        with self.data.open('r+b') as f:f.seek(128*BLOCK);f.write(b'\xff')
        with self.assertRaisesRegex(ValueError,'data mismatch.*128'):self.run_verify()

    def test_hash_tamper_detected(self):
        with self.hash.open('r+b') as f:f.seek(3*BLOCK);f.write(b'\xff')
        with self.assertRaisesRegex(ValueError,'Hash-tree mismatch'):self.run_verify()

    def test_root_is_anchored_in_bootargs_not_data(self):
        self.root=bytes(32)
        with self.assertRaisesRegex(ValueError,'Root hash.*does not match'):self.run_verify()


if __name__=='__main__':
    unittest.main()

