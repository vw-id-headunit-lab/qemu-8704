import importlib.util
import json
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import emu
from sanitize_pe import sanitize, sanitize_compiled_prefix
from sanitize_elf import sanitize as sanitize_elf


def image(prefix,in_code=False):
    blob=bytearray(1024)
    blob[:2]=b'MZ';struct.pack_into('<I',blob,0x3c,0x80)
    blob[0x80:0x84]=b'PE\0\0';struct.pack_into('<H',blob,0x86,2);struct.pack_into('<H',blob,0x94,0xf0)
    for i,(name,offset,flags) in enumerate([(b'.text',512,0x60000020),(b'.rdata',768,0x40000040)]):
        at=0x188+i*40;blob[at:at+len(name)]=name
        struct.pack_into('<II',blob,at+16,128,offset);struct.pack_into('<I',blob,at+36,flags)
    offset=512 if in_code else 768
    blob[offset:offset+len(prefix)]=prefix
    return bytes(blob)


class ReleaseTests(unittest.TestCase):
    def test_pe_layout_and_code_preserved(self):
        prefix='SYNTHETIC_SOURCE_ROOT'
        raw=image(prefix.encode())
        changed,proof=sanitize(raw,[prefix])
        self.assertEqual(len(raw),len(changed))
        self.assertEqual(raw[512:640],changed[512:640])
        self.assertNotIn(prefix.encode(),changed)
        self.assertEqual(proof['replacements'],1)

    def test_executable_section_rejected(self):
        prefix='SYNTHETIC_SOURCE_ROOT'
        with self.assertRaises(ValueError):sanitize(image(prefix.encode(),True),[prefix])

    def test_short_prefix_rejected(self):
        with self.assertRaises(ValueError):sanitize(image(b'abc'),['abc'])

    def test_compiled_prefix_changes_only_two_immediates(self):
        prefix='SYNTH_ROOT/EMU//'
        self.assertEqual(len(prefix),16)
        old=prefix.encode()
        sequence=b'\x48\xb8'+old[:8]+b'\x48\x33\x07\x48\xba'+old[8:]+b'\x48\x33\x57\x08\x48\x09\xd0'
        raw=image(sequence,True)
        changed,proof=sanitize_compiled_prefix(raw,prefix)
        expected=bytearray(raw)
        expected[514:522]=b'BUILD_RO'
        expected[527:535]=b'OT//////'
        self.assertEqual(changed,bytes(expected))
        self.assertEqual(proof['immediate_bytes'],16)

    def test_compiled_prefix_rejects_wrong_instruction(self):
        with self.assertRaises(ValueError):sanitize_compiled_prefix(image(b'fixture',True),'SYNTH_ROOT/EMU//')

    def test_elf_debug_removal_preserves_loaded_data(self):
        raw=bytearray(1024);raw[:6]=b'\x7fELF\x02\x01'
        struct.pack_into('<QQ',raw,32,64,512)
        struct.pack_into('<HHHHH',raw,54,56,1,64,4,3)
        struct.pack_into('<IIQQQQQQ',raw,64,1,5,0,0,0,288,288,4096)
        raw[256:288]=b'X'*32
        names=b'\0.text\0.debug_info\0.shstrtab\0'
        raw[384:384+len(names)]=names;raw[448:480]=b'SYNTHETIC_DEBUG_PATH'.ljust(32,b'/')
        struct.pack_into('<IIQQQQIIQQ',raw,576,1,1,6,256,256,32,0,0,16,0)
        struct.pack_into('<IIQQQQIIQQ',raw,640,7,1,0,0,448,32,0,0,1,0)
        struct.pack_into('<IIQQQQIIQQ',raw,704,19,3,0,0,384,len(names),0,0,1,0)
        changed,proof=sanitize_elf(bytes(raw))
        self.assertEqual(changed[:288],raw[:288])
        self.assertEqual(changed[448:480],bytes(32))
        self.assertEqual(proof['removed_sections'],['.debug_info'])
        struct.pack_into('<Q',raw,648,2)
        with self.assertRaises(ValueError):sanitize_elf(bytes(raw))


    def test_release_excludes_private_and_cache(self):
        with TemporaryDirectory() as name:
            root=Path(name)
            for path in ['README.md','src/core.py','var/inputs/private.img','.local/deps/bin/example.dll','src/__pycache__/core.pyc']:
                target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('example')
            selected={p.relative_to(root).as_posix() for p in emu.public_files(root)}
            self.assertEqual(selected,{'README.md','src/core.py'})

    def test_private_image_inside_runtime_rejected(self):
        with TemporaryDirectory() as name:
            root=Path(name);(root/'runtime').mkdir();(root/'runtime/oem.img').write_bytes(b'fixture')
            self.assertTrue(emu.privacy_findings(root))

    def test_personal_path_detected(self):
        with TemporaryDirectory() as name:
            root=Path(name)
            (root/'README.md').write_text('C'+chr(58)+chr(47)+'Users'+chr(47)+'SYNTHETIC'+chr(47)+'file')
            self.assertTrue(emu.privacy_findings(root))

    def test_import_preflights_every_entry(self):
        with TemporaryDirectory() as name:
            root=Path(name);bundle=root/'bundle';bundle.mkdir();(bundle/'file').write_bytes(b'fixture')
            digest=emu.sha(bundle/'file')
            rows=[{'target':'original/file','source':'file','sha256':digest},{'target':'../escape','source':'file','sha256':digest}]
            (bundle/'manifest.json').write_text(json.dumps({'schema_version':1,'files':rows}))
            previous=emu.ROOT;emu.ROOT=root/'project'
            try:
                with self.assertRaises(ValueError):emu.import_inputs(bundle)
                self.assertFalse(emu.ROOT.exists())
            finally:emu.ROOT=previous

    def test_import_copy_verified(self):
        with TemporaryDirectory() as name:
            root=Path(name);bundle=root/'bundle';bundle.mkdir();(bundle/'file').write_bytes(b'fixture')
            row={'target':'original/file','source':'file','sha256':emu.sha(bundle/'file')}
            (bundle/'manifest.json').write_text(json.dumps({'schema_version':1,'files':[row]}))
            previous=emu.ROOT;emu.ROOT=root/'project'
            try:
                result=emu.import_inputs(bundle)
                self.assertEqual(result['imported'],1)
                self.assertEqual((emu.ROOT/'var/inputs/original/file').read_bytes(),b'fixture')
                self.assertEqual(emu.import_inputs(bundle)['imported'],1)
            finally:emu.ROOT=previous

    def test_import_casefold_receipt_and_duplicates_rejected(self):
        with TemporaryDirectory() as name:
            root=Path(name);(root/'file').write_bytes(b'fixture')
            for targets in [('IMPORTED.JSON',),('original/A','original/a'),('original/a.',)]:
                rows=[{'target':target,'source':'file','sha256':emu.sha(root/'file')} for target in targets]
                (root/'manifest.json').write_text(json.dumps({'schema_version':1,'files':rows}))
                with self.assertRaises(ValueError):emu.import_inputs(root)


    def test_import_host_code_rejected(self):
        with TemporaryDirectory() as name:
            root=Path(name);(root/'file').write_bytes(b'fixture')
            row={'target':'original/host.exe','source':'file','sha256':emu.sha(root/'file')}
            (root/'manifest.json').write_text(json.dumps({'schema_version':1,'files':[row]}))
            with self.assertRaises(ValueError):emu.import_inputs(root)


if __name__=='__main__':
    unittest.main()
