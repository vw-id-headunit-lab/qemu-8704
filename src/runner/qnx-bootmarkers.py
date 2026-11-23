
import hashlib
import mmap
from pathlib import Path
import re
import struct
from tempfile import TemporaryDirectory

import original_8704_vm as vm
from qnx_dp0_platform import SOURCE
from vlm_platform import Elf, factory_file, original, parse_dtb


LOW_BANK = (0x90000000, 0xa0000000)
HIGH_BANK = (0x880000000, 0x8ce000000)
BANKS = (LOW_BANK, HIGH_BANK)
CHUNK = 16 << 20
PAGE = 4096
SLOT = 0x104
INITIAL_ID = 0xd100
BOOTTIME_SHA = 'afa620f6506625391f2cfd16c278420808b775703bcd649ca35f72680aa723ab'
MARKERS = {
    b'[SCREEN]Start Screen': 'screen_start_request',
    b'[Sceen] Start Screen Ready': 'hmi_before_screen_wait',
    b'[HMIC] call GtfStartup': 'gtf_launch_request',
    b'[MW] MgrVCI Launched': 'mgrvci_launch_request',
    b'[TEE]tee services Start': 'tee_start_request',
                                                                         
    b'rpmb Start': 'mobicore_wait_returned',
    b'rpmb end': 'rpmb_wait_returned',
    b'[TEE]tee services End': 'mobicore_user_wait_returned',
    b'[ROOTFS]rootfs qtd mount Start': 'qtd_mount_request',
    b'[ROOTFS]rootfs qtd mount End': 'qtd_root_wait_returned',
                                                                        
    b'[FS]UCM mount Start': 'qnx6_startup_ucm_begin',
    b'[FS]UCM mount End': 'qnx6_startup_ucm_end',
    b'[FS] persist_backup mount Start': 'qnx6_startup_persist_backup_begin',
    b'[FS] persist_backup mount End': 'qnx6_startup_persist_backup_end',
    b'[FS] userdata(var) mount Start': 'qnx6_startup_userdata_begin',
    b'[FS] userdata(var) mount End': 'qnx6_startup_userdata_end',
    b'[FS] coredump mount Start': 'qnx6_startup_coredump_begin',
    b'[FS] coredump mount End': 'qnx6_startup_coredump_end',
    b'[MW] Language Change Service Launched': 'language_change_launch_request',
    b'[MW] p_pdureceiverfas_app Launched': 'pdureceiver_launch_request',
    b'[MW] MgrPduRouter Launched': 'mgrpdurouter_launch_request',
    b'[iSSW] CFA Launched': 'cfa_launch_request',
    b'[iSSW] CI ETH Launched': 'ci_eth_launch_request',
    b'[iSSW] IAA Launched': 'usermanagement_launch_request',
    b'[MW] distance_app Launched': 'lge_middleware_entry',
    b'[MW] MgrTsk Launched ICAS3CN-GP': 'mgrtsk_cn_launch_request',
    b'[MW] MgrLog Launched': 'mgrlog_launch_request',
    b'[MW] HeadUpDisplay_app Launched': 'hud_middleware_launch_request',
                                                                           
                                                                           
    b'[MW] displaytelltale Launched': 'displaytelltale_launch_request',
    b'[MW] VehicleLanguage_app Launched': 'vehiclelanguage_launch_request',
    b'[MW] MgrUpg Launched': 'mgrupg_launch_request',
    b'[MW] MgrComRx Launched': 'mgrcomrx_launch_request',
    b'[MW] MgrComTx Launched': 'mgrcomtx_launch_request',
    b'[MW] MemoryProfiler_app Launched': 'memoryprofiler_launch_request',
    b'[MW] seasonalevents_app Launched ICAS3EU-GP': 'seasonalevents_eu_launch_request',
    b'[MW] arhud wdi Launched': 'arhud_wdi_launch_request',
    b'[MW] whud wdi Launched': 'whud_wdi_launch_request',
    b'[MW] displaymanagement Launched': 'displaymanagement_launch_request',
}
MARKER_BYTES = re.compile(b'(?:'+b'|'.join(re.escape(label) for label in MARKERS)+b')\0')


def contract():
    with factory_file(SOURCE).open('rb') as f:
        f.seek(16093184)
        image = f.read(37308)
    if hashlib.sha256(image).hexdigest() != BOOTTIME_SHA:
        raise ValueError('Original QNX boottime changed')
    elf = Elf(image)
    for address, length, digest in (
        (0x5258, 0x188, '489ff1aa384d8633707a836f1b263b36c96d9d0d7d81545966cd803bc3f102e5'),
        (0x5440, 0x120, 'f86cb21d156b3018d732ad96805adc989e1c9ae9649020211f86cd4b0a2dae69'),
    ):
        if hashlib.sha256(elf.at(address, length)).hexdigest() != digest:
            raise ValueError('Original marker writer changed')
    hcb = original('hcb/hcb.img').read_bytes()
    off = hcb.index(b'VDTHYP\0\0') + 8
    nodes = parse_dtb(hcb[off:off+struct.unpack_from('>I', hcb, off+4)[0]])
    for name, row in nodes.items():
        if name.startswith('/vlm/vm@') and name.endswith('/vm-memory'):
            ranges = tuple(struct.iter_unpack('>QQ', row['reg']))
            if name == '/vlm/vm@2/vm-memory':
                if any((lo,hi-lo) not in ranges for lo,hi in BANKS):
                    raise ValueError('Original VM2 marker RAM changed')
            elif any(start < hi and lo < start+size for start, size in ranges for lo,hi in BANKS):
                raise ValueError('Another original VM overlaps the marker scan bank')


def record(buffer, off):
    if off < 0 or off+SLOT > len(buffer) or off//PAGE != (off+SLOT-1)//PAGE:
        return None
    word = buffer[off:off+0x100]
    label, sep, padding = word.partition(b'\0')
    num = struct.unpack_from('<I', buffer, off+0x100)[0]
    if (not sep or any(padding) or not label or not all(32 <= b < 127 for b in label)
            or not INITIAL_ID <= num < INITIAL_ID+1024):
        return None
    return label, num


def markers(buffer, phys, counters=None):
    for match in MARKER_BYTES.finditer(buffer):
        off=match.start()
        label=match.group()[:-1]
        if counters is not None:
            counters['literal_occurrences']+=1
        found = record(buffer, off)
        if found is None or found[0] != label:
            continue
        if counters is not None:
            counters['record_shaped_occurrences']+=1
        number = found[1]
        neighbors = sum(
            1 for delta in (-2, -1, 1, 2)
            if (off+delta*SLOT)//PAGE == off//PAGE
            and (peer := record(buffer, off+delta*SLOT)) is not None
            and peer[1] == number+delta
        )
        if neighbors >= 2:
            yield dict(marker=MARKERS[label], sequence=number-INITIAL_ID, physical=hex(phys+off),
                       same_page_neighbors=neighbors)


def capture(qmp, output):
    contract()
    if qmp.execute('query-status')['status'] != 'paused':
        raise ValueError('QNX marker scan requires the stopped original board')
    hits = {}
    banks = []
    with TemporaryDirectory(prefix='qnx-marker-', dir=output) as directory:
        p = Path(directory)/'chunk.bin'
        for lo,hi in BANKS:
            counters=dict(base=hex(lo),bytes=hi-lo,literal_occurrences=0,record_shaped_occurrences=0)
            for base in range(lo,hi,CHUNK):
                count=min(CHUNK,hi-base)
                qmp.execute('pmemsave', {'val':base, 'size':count, 'filename':str(p)})
                with p.open('rb') as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as raw:
                        if len(raw) != count:
                            raise ValueError('Incomplete QNX marker RAM read')
                        for hit in markers(raw, base, counters):
                            hits[hit['physical']] = hit
                p.unlink()
            banks.append(counters)
    vm.save(output/'qnx-bootmarkers.json', dict(
        status='observed_original_marker_records' if hits else 'not_observed',
        source_sha256=BOOTTIME_SHA, banks=banks, records=sorted(hits.values(), key=lambda h:h['sequence']),
        scope='Read-only OEM boottime records; a launch request is not proof of exec success'))
