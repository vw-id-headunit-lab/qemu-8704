import hashlib
import io
import struct
import unittest
import zlib

from ufs_android_sparse import expand


def sparse(chunks, blocks, crc=0):
    out = bytearray(struct.pack('<I4H4I',0xed26ff3a,1,0,28,12,4096,blocks,len(chunks),crc))
    for kind,n,payload in chunks:
        out += struct.pack('<HHII',kind,0,n,12+len(payload))+payload
    return bytes(out)


class SparseReader(unittest.TestCase):
    def test_raw_fill_hole_and_crc_match(self):
        raw = bytes(range(256))*16
        fill = b'\xf1\x92\x18\x13'
        expected = raw+fill*1024+bytes(4096)
        crc = zlib.crc32(expected)
        image = sparse([(0xcac1,1,raw),(0xcac2,1,fill),(0xcac3,1,b''),
                        (0xcac4,0,struct.pack('<I',crc))],3,crc)
        target = io.BytesIO(b'a'*4096+bytes(3*4096)+b'b'*4096)
        d = expand(io.BytesIO(image),target,4096,len(expected))
        self.assertEqual(target.getvalue(),b'a'*4096+expected+b'b'*4096)
        self.assertEqual(d.input_size,len(image))
        self.assertEqual(d.input_sha256,hashlib.sha256(image).hexdigest())
        self.assertEqual(d.output_sha256,hashlib.sha256(expected).hexdigest())
        self.assertEqual(d.crc32,crc)

    def test_bad_checksum_and_trailer_rejected(self):
        one = [(0xcac3,1,b'')]
        for data in (sparse(one,1,123),sparse(one,1)+b'a',
                     sparse(one+[(0xcac4,0,bytes(4))],1)):
            with self.subTest(data=data),self.assertRaises(ValueError):
                expand(io.BytesIO(data),io.BytesIO(),0,4096)

    def test_bounded_chunk_geometry_and_unknown_type(self):
        for chunks in ([(0xcac1,2,bytes(8192))],[(0xcac2,1,b'')],[(0xcac3,1,b'x')],
                       [(0xcac5,1,b'')],[(0xcac1,1,b'x')],[(0xcac3,0,b'')]):
            with self.subTest(chunks=chunks[0][:2]),self.assertRaises(ValueError):
                expand(io.BytesIO(sparse(chunks,1)),io.BytesIO(),0,4096)

    def test_truncated_input_rejected(self):
        data=sparse([(0xcac1,1,bytes(4096))],1)
        with self.assertRaises(EOFError):
            expand(io.BytesIO(data[:-4]),io.BytesIO(),0,4096)

    def test_wrong_expanded_length_rejected_without_writes(self):
        target=io.BytesIO(b'unchanged')
        with self.assertRaises(ValueError):
            expand(io.BytesIO(sparse([(0xcac3,1,b'')],1)),target,0,8192)
        self.assertEqual(target.getvalue(),b'unchanged')

