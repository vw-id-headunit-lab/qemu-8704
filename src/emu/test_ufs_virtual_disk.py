
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zlib

import ufs_virtual_disk as disk


class VirtualLunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.factory = disk.factory_plan()

    def test_factory_slots_and_missing_partitions(self):
        rows = {r['slot']:r for r in self.factory['partitions']}
        self.assertEqual(list(rows),list(range(1,56)))
        self.assertEqual(self.factory['omitted'][0]['label'],'ramdump')
        self.assertEqual({r for r in rows if 'payload' in rows[r]},set(disk.PAYLOAD))
        self.assertEqual(rows[32]['payload'],rows[33]['payload'])
        self.assertEqual(rows[14]['payload'],rows[31]['payload'])
        self.assertEqual(rows[27]['payload'],rows[55]['payload'])
        self.assertNotIn('payload',rows[34])
        self.assertNotIn('payload',rows[36])
        self.assertFalse(self.factory['factory_runtime_gpt'])

    def test_geometry_and_crc(self):
        b = self.factory['blocks']
        table = disk.gpt(self.factory)
        self.assertEqual(len(table),5)
        self.assertEqual(table[2],table[b-5])
        self.assertEqual(table[0][450],0xee)
        self.assertEqual(struct.unpack_from('<II',table[0],454),(1,b-1))
        for lba, arr in ((1,2),(b-1,b-5)):
            head = table[lba]
            self.assertEqual(head[:8],b'EFI PART')
            self.assertEqual(struct.unpack_from('<Q',head,24)[0],lba)
            self.assertEqual(struct.unpack_from('<Q',head,72)[0],arr)
            self.assertEqual(struct.unpack_from('<I',head,88)[0],zlib.crc32(table[arr]))
            self.assertEqual(struct.unpack_from('<I',head,16)[0],
                             zlib.crc32(head[:16]+bytes(4)+head[20:92]))

    def test_reject_wrong_factory_template(self):
        with patch('storage_layout.inventory',return_value={'xml_matched_entries':106}):
            with self.assertRaisesRegex(ValueError,'107 PIT/XML'):
                disk.factory_plan()

    def test_wrong_payload_and_gpt_are_not_accepted(self):
        plan = dict(blocks=512,disk_guid=self.factory['disk_guid'],partitions=[dict(
            self.factory['partitions'][0],first=256,last=256,
            payload=dict(member='fixture',bytes=4,sha256=hashlib.sha256(b'ABCD').hexdigest()))])
        with tempfile.TemporaryDirectory() as path:
            image=Path(path)/disk.IMAGE
            with image.open('xb') as stream:
                disk._mark_sparse(stream)
                for lba,value in disk.gpt(plan).items():
                    stream.seek(lba*disk.SECTOR)
                    stream.write(value)
                stream.seek(256*disk.SECTOR)
                stream.write(b'ABCD')
            self.assertEqual(len(disk.verify_image(image,plan)['archive_payloads_verified']),1)
            with image.open('r+b') as stream:
                stream.seek(256*disk.SECTOR)
                stream.write(b'W')
            with self.assertRaisesRegex(ValueError,'payload hash mismatch'):
                disk.verify_image(image,plan)
            with image.open('r+b') as stream:
                stream.seek(511*disk.SECTOR)
                stream.write(b'W')
            with self.assertRaisesRegex(ValueError,'GPT byte mismatch'):
                disk.verify_image(image,plan)

    def test_reject_outside_scratch_and_short_stream(self):
        with self.assertRaises(ValueError):
            disk._safe_dir(disk.vm.ROOT)
        with tempfile.TemporaryFile() as stream:
            stream.write(b'x')
            stream.seek(0)
            with self.assertRaises(EOFError):
                disk._transfer(stream,2)

    def test_qnx_seed_only_retains_four_ordinary_filesystem_extents(self):
        plan = self.factory
        rows = [p for p in plan['partitions'] if p['slot'] in disk.QNX_FIRSTBOOT_SLOTS]
        mapping=[]
        start=0
        for p in rows:
            at=p['first']*disk.SECTOR
            mapping.append(dict(start=start,length=at-start,depth=1,present=True))
            mapping.append(dict(start=at,length=disk.SECTOR,depth=0,present=True))
            start=at+disk.SECTOR
        mapping.append(dict(start=start,length=plan['blocks']*disk.SECTOR-start,depth=1,present=True))
        want={str(n):dict(label=label,bytes=disk.SECTOR) for n,label in disk.QNX_FIRSTBOOT_SLOTS.items()}
        self.assertEqual(disk.qnx_initial_allocations(plan,mapping),want)
                                                                                
        with self.assertRaisesRegex(ValueError,'outside the four'):
            disk.qnx_initial_allocations(plan,[dict(mapping[0],depth=0,data=False,zero=True),*mapping[1:]])
        with self.assertRaisesRegex(ValueError,'required first-boot'):
            disk.qnx_initial_allocations(plan,[mapping[0],dict(mapping[1],depth=1),*mapping[2:]])
        with self.assertRaisesRegex(ValueError,'complete map'):
            disk.qnx_initial_allocations(plan,mapping[:-1])

    def test_qnx_completed_var_requires_a_pinned_source_and_ordered_markers(self):
        slots=disk.QNX_FIRSTBOOT_SLOTS | disk.QNX_COMPLETE_VAR_SLOTS
        mapping=[]
        start=0
        for p in self.factory['partitions']:
            if p['slot'] not in slots:
                continue
            at=p['first']*disk.SECTOR
            mapping.append(dict(start=start,length=at-start,depth=1,present=True))
            mapping.append(dict(start=at,length=disk.SECTOR,depth=0,present=True))
            start=at+disk.SECTOR
        mapping.append(dict(start=start,length=self.factory['blocks']*disk.SECTOR-start,depth=1,present=True))
        with self.assertRaisesRegex(ValueError,'outside the four'):
            disk.qnx_initial_allocations(self.factory,mapping)
        self.assertEqual(set(disk.qnx_initial_allocations(self.factory,mapping,completed_var=True)),
                         {str(n) for n in slots})
        with self.assertRaisesRegex(ValueError,'outside the six'):
            disk.qnx_initial_allocations(self.factory,[dict(mapping[0],depth=0),*mapping[1:]],completed_var=True)

        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/'qnx-native-runtime/qnx-bootmarkers.json'
            path.parent.mkdir()
            marks=('qtd_root_wait_returned','qnx6_startup_userdata_end',
                   'qnx6_startup_coredump_begin','qnx6_startup_coredump_end')
            blob=json.dumps(dict(status='observed_original_marker_records',
                            records=[dict(marker=m,sequence=i) for i,m in enumerate(marks)])).encode()
            path.write_bytes(blob)
            self.assertFalse(disk.qnx_completed_var_source(root,'different'))
            with patch.object(disk,'QNX_COMPLETE_VAR_MARKERS',hashlib.sha256(blob).hexdigest()):
                self.assertTrue(disk.qnx_completed_var_source(root,disk.QNX_COMPLETE_VAR_SHA256))
                path.write_bytes(blob+b'\n')
                with self.assertRaisesRegex(ValueError,'bootmarkers differ'):
                    disk.qnx_completed_var_source(root,disk.QNX_COMPLETE_VAR_SHA256)


    def test_final_guest_ram_matches_only_completed_good_read(self):
        from ufs_platform import verify_completed_read, scsi_request
        body=bytes(range(256))*16
        with tempfile.TemporaryDirectory() as path:
            path=Path(path)
            image=path/'fixture.raw'
            image.write_bytes(bytes(4096)+body)
            class QMP:
                calls=0
                def execute(self, command, args):
                    self.calls+=1
                    if command!='pmemsave' or args['size']!=2048:
                        raise AssertionError('Unbounded capture')
                    at=args['val']-0x906f0000
                    Path(args['filename']).write_bytes(body[at:at+args['size']])
            qmp=QMP()
            slot=dict(slot=0,ocs=0,response_transaction=0x21,response_result=0,scsi_status=0,scsi_lun=0,
                      scsi_cdb=scsi_request(0x28,1)[16:32].hex(),expected_data_bytes=4096,
                      prdt={'entries':[dict(address=0x906f0000+i,bytes=2048,reserved=0,high_size_flags=0)
                                       for i in (0,2048)]})
            self.assertTrue(verify_completed_read(qmp,path,image,slot)['matches'])
            self.assertEqual(qmp.calls,2)
            self.assertEqual(list(path.iterdir()),[image])
            self.assertFalse(verify_completed_read(qmp,path,image,dict(slot,ocs=15))['compared'])
            self.assertEqual(qmp.calls,2)

if __name__ == '__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(VirtualLunTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)

