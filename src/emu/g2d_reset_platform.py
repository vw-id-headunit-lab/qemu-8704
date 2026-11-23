
import hashlib
import struct

import original_8704_vm as vm
from vlm_platform import factory_file, original, parse_dtb

PROFILE='virtual-fimg2d-reset-v1'
BASE=0x19a20000
SIZE=0xa000
SPI=243
RESET_NS=128
PROBE=(0x80c754,1660,'12742440dc374581a34a31357cb9c6c161533b2dc08f1729ebfdff2d15c7d9bd')

def verify_kernel(kernel):
    off,size,digest=PROBE
    if hashlib.sha256(kernel[off:off+size]).hexdigest()!=digest:
        raise ValueError('Original g2d_probe instructions changed')

def policy(profile,native_android):
    if profile is None:
        return None
    if profile!=PROFILE or not native_android:
        raise ValueError('FIMG2D reset requires original Android VM4 DT and HCB')
    vendor=factory_file('android/vendor_boot.img')
    with vendor.open('rb') as f:
        h=f.read(2112)
        page,rd,hs,ds=(struct.unpack_from('<I',h,i)[0] for i in (12,24,2096,2100))
        f.seek(((hs+page-1)//page+(rd+page-1)//page)*page)
        dt=f.read(ds)
    node=parse_dtb(dt)['/g2d@19A20000']
    reg=struct.pack('>QI',BASE,SIZE)
    interrupt=struct.pack('>III',0,SPI,4)
    if (node['compatible']!=b'samsung,exynos9820-g2d\0' or node['status']!=b'okay\0' or
        node['reg']!=reg or node['interrupts']!=interrupt):
        raise ValueError('Original Android FIMG2D node changed')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    prefix='/vplatform/vl,fimg2d/vl,fimg2d'
    for i in (2,3,4):
        start=raw.index(f'VDTVM{i}'.encode()+bytes(2))+8
        tree=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
        mem,irq=(tree[prefix+suffix] for suffix in ('@mem','@irq'))
        if (mem['reg']!=reg or irq['interrupts']!=interrupt or
            ('vl,disabled' in mem)!=(i!=4) or ('vl,disabled' in irq)!=(i!=4)):
            raise ValueError(f'Original VM{i} FIMG2D grant changed')
    return dict(profile=profile,base=BASE,size=SIZE,dt_sha256=hashlib.sha256(dt).hexdigest(),
                hcb_sha256=vm.sha(hcb),original_probe=PROBE,reset_ns=RESET_NS,
                version=0,physical_version_verified=False,dma=False,jobs=False,irq=False)

def runtime_state(qmp):
    return {k:qmp.execute('qom-get',dict(path='/machine/exynos-g2d-reset',property=k))
            for k in ('ready','resets','completions','status-reads','version-reads','rejected',
                      'last-rejected','last-rejected-write')}

