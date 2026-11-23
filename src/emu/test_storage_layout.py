
import struct
import unittest
import sys
import storage_layout as storage


def fixture(version=0x10003):
    header=struct.pack('<7I',0x12349876,1,version,0,0,0,0)
    entry=bytearray(132)
    struct.pack_into('<8I',entry,0,11,22,269,33,5,48,6291456,128)
    offset=32 if version else 36
    entry[offset:offset+4]=b'pit\0'
    if version:entry[64:68]=b'raw\0'
    entry[100:109]=b'remained\0'
    return header+entry


class PitContractTests(unittest.TestCase):
    def test_native_descriptor_offsets(self):
        result=storage.parse_pit(fixture());row=result['entries'][0]
        self.assertEqual(result['name_offset'],32)
        self.assertEqual((row['identifier'],row['filesystem'],row['block_offset'],
            row['block_count'],row['device_lun']),(269,5,48,6291456,128))
        self.assertEqual((row['name'],row['flash_filename'],row['option']),('pit','raw','remained'))

    def test_layout_selected_by_header_not_first_name(self):
        raw=bytearray(fixture());raw[60:64]=b'env\0'
        self.assertEqual(storage.parse_pit(raw)['entries'][0]['name'],'env')

    def test_legacy_descriptor_contract(self):
        row=storage.parse_pit(fixture(0))['entries'][0]
        self.assertEqual(row['identifier'],0)
        self.assertIsNone(row['flash_filename'])
        self.assertEqual((row['name'],row['option']),('pit','remained'))

    def test_fixed_width_fields(self):
        raw=bytearray(fixture());raw[60:92]=b'n'*32
        self.assertEqual(storage.parse_pit(raw)['entries'][0]['name'],'n'*32)

    def test_invalid_signature_truncation_and_count(self):
        inputs=[b'',b'x'*160,fixture()[:-1]]
        for count in (0,257):
            raw=bytearray(fixture());struct.pack_into('<I',raw,4,count);inputs.append(raw)
        for raw in inputs:
            with self.subTest(size=len(raw)):
                with self.assertRaises(ValueError):storage.parse_pit(raw)

    def test_original_all_xml_fields_and_payload_units(self):
        report=storage.inventory()
        self.assertEqual(report['xml_matched_entries'],107)
        self.assertEqual(report['xml_compared_entries'],107)
        self.assertTrue(all(row['xml_match'] for row in report['entries']))
        self.assertTrue(all(row['block_offset']==0 for row in report['entries']))
        rows={row['name']:row for row in report['payload_size_comparisons']}
        self.assertEqual(rows['a_agl_system_ivi_rbhv-qia']['bytes_per_block_if_exact'],512)
        self.assertEqual(rows['system_qnx_rbhv-qia']['bytes_per_block_if_exact'],512)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PitContractTests))
    sys.exit(0 if result.wasSuccessful() else 1)
