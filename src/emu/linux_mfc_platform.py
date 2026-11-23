
import hashlib
import struct

from vlm_platform import factory_file, original, parse_dtb

BASE = 0x198e0000
CODE = (
    (0x637fc8,3488,'334fe791e380d1e8ee08bd07ebb63642911e23ef291acafb92111bedd027e08b'),
    (0x653f30,240,'db19ff640e56d41d393ebe9f10cd5cfeb4826989deb7c5391729bd3cd98eae9f'),
    (0x64fc90,264,'8492a5009b0563a64921e5458bef8f43c47226289b787c4d171dd7188b41b20f'),
)


def policy(native):
    if not native:
        raise ValueError('MFC cold reset requires original VM3 and HCB')
    dt = factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes()
    node = parse_dtb(dt)['/mfc@198E0000']
    reg, irq = struct.pack('>QI',BASE,0x10000), struct.pack('>III',0,266,4)
    if (node.get('reg') != reg or node.get('interrupts') != irq or
            node.get('status') != b'okay\0' or node.get('compatible') != b'samsung,exynos-mfc\0'):
        raise ValueError('Original VM3 MFC DT changed')
    hcb = original('hcb/hcb.img').read_bytes()
    for vm in (2,3,4):
        start = hcb.index(f'VDTVM{vm}'.encode()+bytes(2))+8
        size = struct.unpack_from('>I',hcb,start+4)[0]
        nodes = parse_dtb(hcb[start:start+size])
        mem, intr = (nodes[f'/vplatform/vl,mfc/vl,mfc@{name}'] for name in ('mem','irq'))
        if (mem.get('reg') != reg or intr.get('interrupts') != irq or
                any(('vl,disabled' in entry) != (vm!=3) for entry in (mem,intr))):
            raise ValueError(f'Original VM{vm} MFC HCB changed')
    with factory_file('ivi/Image').open('rb') as img:
        for offset,size,digest in CODE:
            img.seek(offset)
            if hashlib.sha256(img.read(size)).hexdigest() != digest:
                raise ValueError('Original VM3 MFC cold driver changed')
    return dict(profile='auto9-mfc-cold-reset-v1',base=BASE,size=0x10000,vm=3,
                bus_reset_offset=0x7110,virtual_ack_ns=20000,
                original_driver=CODE,firmware=False,dma=False,irq=False)


def runtime(qmp):
    path='/machine/exynos-linux-mfc'
    return {key:qmp.execute('qom-get',{'path':path,'property':key})
            for key in ('bus-requests','bus-acks','bus-releases','resets','clear-words',
                        'poweroffs','rejected')}

