
import hashlib
import re
import struct


PROFILE = 'iid-arhud'
PAGE = 0xe5d01000
                                                                              
FIELDS = ((PAGE + 1, b'\x00\x72'), (PAGE + 8, b'\x01\x01\x01'))
IFS = 'qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn'


def contract(profile):
    from vlm_platform import factory_file, original, parse_dtb

    if profile != PROFILE:
        raise ValueError('Unknown virtual QNX display configuration')
    hcb = original('hcb/hcb.img').read_bytes()
    for vmid in (2, 3, 4):
        start = hcb.index(f'VDTVM{vmid}'.encode() + b'\0\0') + 8
        tree = parse_dtb(hcb[start:start + struct.unpack_from('>I', hcb, start + 4)[0]])
        for name, props in tree.items():
            if not name.startswith('/vplatform/vl,io-memory@') or len(props.get('reg', b'')) != 12:
                continue
            base, size = struct.unpack('>QI', props['reg'])
            if base < PAGE + 4096 and PAGE < base + size:
                if vmid != 2 or (base, size) != (PAGE - 4096, 8192):
                    raise ValueError('Unexpected owner of the original VM2 MCU page')
        if vmid == 2 and tree['/vplatform/vl,io-memory@E5D00000']['reg'] != struct.pack('>QI', PAGE - 4096, 8192):
            raise ValueError('VM2 MCU grant changed')
    dtb = parse_dtb(factory_file('qnx/qtd/icas3gp-qla-vm.dtb').read_bytes())
    if dtb['/reserved-memory/mcu']['reg'] != struct.pack('>QI', PAGE, 4096):
        raise ValueError('Original QNX MCU reservation changed')
    with factory_file(IFS).open('rb') as stream:
        stream.seek(178004160 + 112)
        if stream.read(20) != b'\x14\x00\x04\x00/dev/shmem\x00/tmp\x00':
            raise ValueError('Original QNX startup symlink record changed')
        stream.seek(177731680)
        early = stream.read(14955)
    if (hashlib.sha256(early).hexdigest() != '002113efe9acb972198c7bd55072ace971cb0e3c11743693c5d2b6c491263f6b' or
            b'0x0072' not in early or b'/tmp/eeprom' not in early):
        raise ValueError('Original QNX early HUD selection changed')
    return dict(profile=profile, vmid=2, variant='0x0072', display=1, cluster=1, hud=1,
                source='explicit virtual MCU display configuration, not a vehicle EEPROM dump',
                physical=hex(PAGE), bytes_supplied=sum(len(data) for _, data in FIELDS))


def loader_options():
    return [arg for addr, data in FIELDS for arg in
            ('-device', f'loader,addr={addr:#x},data={int.from_bytes(data, "little"):#x},data-len={len(data)}')]


def readback(qmp):
    rows = []
    for addr, expected in FIELDS:
        raw = qmp.execute('human-monitor-command', {'command-line': f'xp /{len(expected)}bx {addr:#x}'})
        row = re.fullmatch(r'\s*(?:0x)?([0-9a-f]+):((?:\s+0x[0-9a-f]{2})+)\s*', raw, re.I)
        if not row or int(row[1], 16) != addr:
            raise ValueError('Cannot read back the original VM2 MCU field')
        data = bytes(int(x, 16) for x in row[2].split())
        if len(data) != len(expected):
            raise ValueError('Incomplete original VM2 MCU field')
        rows.append(dict(address=hex(addr), expected=expected.hex(), observed=data.hex(), match=data == expected))
    return dict(fields=rows, all_match=all(row['match'] for row in rows))
