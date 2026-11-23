
import hashlib
from pathlib import Path
import struct
import unittest

from patch_qemu_exynos import qnx_gtf_watch_rows
from qnx_dp0_platform import SOURCE
from vlm_platform import factory_file


def executable_bytes(raw,address,count):
                                                                             
    if raw[:7]!=b'\x7fELF\x02\x01\x01' or struct.unpack_from('<H',raw,18)[0]!=183:
        raise ValueError('Expected original little-endian AArch64 ELF')
    offset=struct.unpack_from('<Q',raw,32)[0]
    size,n=struct.unpack_from('<HH',raw,54)
    if size!=56 or not 0<n<=32 or offset+n*size>len(raw):
        raise ValueError('Invalid original program header table')
    matches=[]
    for row in range(n):
        kind,flags,pos,va,_,length,_,_=struct.unpack_from('<II6Q',raw,offset+row*size)
        if kind==1 and flags&1 and va<=address<=address+count<=va+length and pos+length<=len(raw):
            start=pos+address-va
            matches.append(raw[start:start+count])
    if len(matches)!=1:
        raise ValueError('Watch site not in a unique executable file-backed segment')
    return matches[0]


class QnxGtfWatchTests(unittest.TestCase):
    def test_original_executable_and_library_sites(self):
        rows=qnx_gtf_watch_rows(Path(__file__).with_name('exynos-auto9-qnx-gtf-watch.inc.c').read_text(encoding='utf-8'))
        members=(
            (0xfcb000,8494,'e02e310510b7fb60e456bc347833cf0f4029537cf323cdedb891e4727d344133',
             {'crt-argv':0xa38,'crt-errno':0xa80,'wrapper-main':0x970,'core-return':0x998}),
            (0x196c000,8494,'ecf9a237f1099a274fd1f4dadcacbbd76caba0082db8e10e27e010392469ecb4',
             {'crt-argv':0xa38,'crt-errno':0xa80,'wrapper-main':0x970,'core-return':0x998}),
            (0x1271000,368974,'0310f98b7a4ee1d73ab8fa89e1d74fae8a5568872b72a0d466ecd9c805206985',
             {'core-main':0xf790,'config-import-return':0x101ec,'launcher-present':0xff48,
              'launcher-run':0xffc4,'launcher-return':0xffc8}),
            (20332544,884030,'5f66c452010d86d4c72c170c3def9fe9177571dd10f7d195ad03ef5a7147b425',
             {'gles3-catalog':0x11308}),
            (111013888,18884528,'258f9b4c04aacf5db581b3e05df4ea129e05377d7d6507bfcd7af1c53bb288b6',
             {'egl-initialize':0x1b8af8,'egl-initialize-return':0x1b8d80,
              'egl-create-context':0x1b7970,'egl-create-context-return':0x1b7a10,
              'egl-make-current-return':0x1c2244,'egl-swap-buffers':0x1c46e8,
              'egl-swap-buffers-return':0x1c4788,'egl-swap-without-present':0x1c4828,
              'egl-present-window':0x1c3b78,'gles-clear':0x120ad0,
              'gles-draw-arrays':0x121910,'gles-draw-elements':0x121998,
              'gles-draw-arrays-instanced':0x1148d0,'gles-draw-elements-instanced':0x114ce0}),
            (133689344,94866,'88f14e5113758867e39d12e715c4e44dbf89b740d38b486e17cd962e3fa13dab',
             {'screen-post-window':0x12a00,'screen-post-command':0x10fd0,
              'screen-post-window-return':0x12afc}),
            (0x69a4000,238491,'608ee478b0a8de97e772d997b15d4c2872db405db308b0de8a3e8e6a7f191cd2',
             {'kbase-job-ioctl':0x12a98,'kbase-as-update-request':0x253f8,
              'kbase-job-start-request':0x186bc}),
            (27385856,1094098,'f7bc3f73b5048212d00da19fc5eab13a5049c6e220a141e06a6df1292a98d650',
             {'hud-available-subscribe-entry':0x75c10,'hud-persistence-callback':0x76f10,
              'hud-variant-committed':0x7717c,'hud-available-value':0x78974,
              'hud-variant-path-present':0x78a04,'hud-model-load-entry':0x77360,
              'hud-model-load-id':0x773dc,'adapter-3s-timer-construction':0x7ac9c,
              'adapter-3s-timer-start-return':0x7acd8,'adapter-3s-timer-callback':0x74f00,
              'adapter-executor-submit':0x74fa4,'adapter-executor-late-worker':0x781d0,
              'adapter-dlopen-result':0x77dd4}),
            (21307392,2238626,'1bfd372ce71a879ddfa0da185674aedc53edd16c5fe3acb3224299045409963e',
             {'gtf-stream-create-return':0x198ee4,'gtf-stream-buffers-return':0x199120,
              'gtf-stream-post-return':0x199970,'gtf-stream-sharing-return':0x1991f0,
              'gtf-stream-name-return':0x1993d0}),
            (33624064,14467922,'7e34f51627f8140f769e5a6037721d7310fef06c4f219a9e36efda05593dad35',
             {'hud-publisher-start-entry':0x6eeef0,'hud-publisher-start-value':0x6ef0a4,
              'hud-publisher-stop-value':0x6ef794,'hud-timer-executor':0x6f1e3c,
              'hud-timer-construct':0x6f1fbc,'hud-timer-fired':0x6ef580}),
        )
        names=set()
        with factory_file(SOURCE).open('rb') as stream:
            for offset,size,digest,sites in members:
                stream.seek(offset)
                raw=stream.read(size)
                self.assertEqual(hashlib.sha256(raw).hexdigest(),digest)
                for name,address in sites.items():
                    low,words=rows[name]
                    self.assertEqual(low,address & 0xfff,name)
                    self.assertEqual(executable_bytes(raw,address,16),struct.pack('<4I',*words),name)
                    names.add(name)
                                                                          
                                                                       
        with factory_file('qnx/qtd/system-qla.part_qtd.qnx6.vw_cn').open('rb') as stream:
            for name,elf_at,disk_at in (('wdi-crt-first-call',0xc23d0,0x5ff83d0),
                                        ('wdi-call-main',0xc2424,0x5ff8424),
                                        ('wdi-main',0xb33f8,0x5fe93f8),
                                        ('wdi-constructor-call',0xb3434,0x5fe9434),
                                        ('wdi-constructor-return',0xb3438,0x5fe9438),
                                        ('wdi-vci-thread-return',0xc9118,0x5fff118),
                                        ('wdi-warp-sleep-enter',0xc9174,0x5fff174),
                                        ('wdi-warp-sleep-return',0xc9180,0x5fff180),
                                        ('wdi-warp-wait-done',0xc9270,0x5fff270),
                                        ('wdi-create-window-call',0xc88f0,0x5ffe8f0),
                                        ('wdi-display-bind-return',0xd672c,0x600c72c),
                                        ('wdi-acquire-stream-present',0xc8964,0x5ffe964),
                                        ('wdi-acquire-return',0xc8968,0x5ffe968),
                                        ('wdi-stream-event-handle',0xd6168,0x600c168),
                                        ('wdi-stream-id-compare',0xd6190,0x600c190),
                                        ('wdi-consumer-create-return',0xd61b8,0x600c1b8),
                                        ('wdi-consumer-bind-return',0xd61c8,0x600c1c8),
                                        ('wdi-consumer-buffer-count',0xd6288,0x600c288),
                                        ('wdi-stream-close',0xd6300,0x600c300),
                                        ('wdi-acquired-buffer-query-return',0xc897c,0x5ffe97c),
                                        ('wdi-egl-initialize-return',0xcf564,0x6005564),
                                        ('wdi-screen-context-return',0xd65f0,0x600c5f0),
                                        ('wdi-screen-window-return',0xd6678,0x600c678),
                                        ('wdi-screen-buffers-return',0xd67f4,0x600c7f4)):
                stream.seek(disk_at)
                low,words=rows[name]
                self.assertEqual(low,elf_at & 0xfff)
                self.assertEqual(stream.read(16),struct.pack('<4I',*words))
                names.add(name)
        self.assertEqual(names,set(rows))

    def test_overlapping_page_offsets_keep_distinct_opcodes(self):
        table=qnx_gtf_watch_rows(Path(__file__).with_name('exynos-auto9-qnx-gtf-watch.inc.c').read_text(encoding='utf-8'))
        a,b=table['wrapper-main'],table['egl-create-context']
        self.assertEqual(a[0],b[0])
        self.assertNotEqual(a[1][0],b[1][0])

    def test_original_wdi_acquire_call_and_libc_errno_contract(self):
                                                                         
        with factory_file('qnx/qtd/system-qla.part_qtd.qnx6.vw_cn').open('rb') as stream:
            stream.seek(0x5ffe944)
            self.assertEqual(stream.read(48),struct.pack('<12I',
                0xf9400260,0x9400370b,0xaa0003e1,0x52800405,0xd2800004,0xd2800003,
                0xd2800002,0xaa1803e0,0x97ff9c57,0x35000d60,0xaa1703e2,0x52800441))
        from clock_rpc_platform import LIBC,LIBC_SHA
        raw=LIBC.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),LIBC_SHA)
        self.assertEqual(executable_bytes(raw,0x81668,12),bytes.fromhex('60d03bd5000840f9c0035fd6'))


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(QnxGtfWatchTests)
    raise SystemExit(0 if unittest.TextTestRunner().run(suite).wasSuccessful() else 1)
