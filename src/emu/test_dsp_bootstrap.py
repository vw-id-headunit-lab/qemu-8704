import struct
import unittest

import dsp_bootstrap as dsp


class DspBootstrapTests(unittest.TestCase):
    def test_prdt_covers_original_389_blocks_without_padding_or_flags(self):
        length=389*4096
        rows=list(struct.iter_unpack('<4I',dsp.descriptors(0xd0000000,length)))
        self.assertEqual(len(rows),7)
        pos=0xd0000000
        for address,high,reserved,dbc in rows:
            self.assertEqual((address,high,reserved),(pos,0,0))
            self.assertLess(dbc,0x40000)
            pos+=dbc+1
        self.assertEqual(pos,0xd0000000+length)
        for invalid in (0,length+1,0x40000*32+4096):
            with self.assertRaises(ValueError):
                dsp.descriptors(0xd0000000,invalid)

    def test_dma_permission_table_only_allows_exact_pages_and_write(self):
        base=0xc0000000
        regions=[dict(address=0xd0000000,bytes=389*4096),
                 dict(address=0xd4000000,bytes=389*4096),
                 dict(address=0xd8000000,bytes=389*4096)]
        expected=set()
        for r in regions:
            expected.update(range((r['address']-base)//4096,(r['address']-base+r['bytes'])//4096))
        table=dsp.write_permissions(regions)
        for i,b in enumerate(table):
            for field in range(4):
                self.assertEqual((b>>(2*field))&3,2 if 4*i+field in expected else 0)
        with self.assertRaises(ValueError):
            dsp.write_permissions([dict(address=0xbffff000,bytes=4096)])
        with self.assertRaises(ValueError):
            dsp.write_permissions([dict(address=0xd0000000,bytes=4097)])

    def test_only_real_unit_attention_formats_supply_sense_key(self):
        r=bytearray(52)
        r[34],r[36]=0x70,6
        self.assertIsNone(dsp.sense_key(r))
        struct.pack_into('>H',r,32,18)
        self.assertEqual(dsp.sense_key(r),6)
        r[34],r[35]=0x72,5
        self.assertEqual(dsp.sense_key(r),5)
        self.assertIsNone(dsp.sense_key(r[:36]))

    def test_invalid_profiles_do_not_bootstrap(self):
        self.assertIsNone(dsp.policy(None,False,False,None,None))
        with self.assertRaises(ValueError):
            dsp.policy(dsp.PROFILE,True,True,{'profile':dsp.media.android.PROFILE},'a')

